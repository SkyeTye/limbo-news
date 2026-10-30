"""Runs a fact-check from link to report (Diagram 1). One check at a time;
the claims inside a check are traced in parallel. Progress is written to the
database as it goes, so the phone shows claims first and verdicts as they land."""
import os
import queue
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

from . import budget, capture, config, context, db, extract, tracer, web

_q = queue.Queue()
_current = {"id": None}
_lock = threading.Lock()
_started = False
_archive_q = queue.Queue()


def start_worker():
    global _started
    with _lock:
        if _started:
            return
        _started = True
    db.init()
    # Anything left running by a restart is marked so it can be retried.
    for c in db.stale_running_checks():
        db.update_check(c["id"], status="error", step="", error="Interrupted by a server restart. Tap Retry.")
        for cl in db.claims_for(c["id"]):
            if cl["status"] in ("queued", "tracing"):
                db.update_claim(cl["id"], status="failed", bottom_line="Interrupted by a server restart.")
    threading.Thread(target=_worker, daemon=True, name="factcheck-worker").start()
    threading.Thread(target=_archiver, daemon=True, name="factcheck-archiver").start()


def submit(job):
    """job: ('check', check_id) or ('claim', check_id, claim_id)."""
    _q.put(job)


def queue_size():
    return _q.qsize()


def current():
    return _current["id"]


def _worker():
    while True:
        job = _q.get()
        try:
            _current["id"] = job[1]
            if job[0] == "check":
                run_check(job[1])
            elif job[0] == "claim":
                run_single_claim(job[1], job[2])
        except Exception:
            traceback.print_exc()
        finally:
            _current["id"] = None
            _q.task_done()


def _archiver():
    """Ask the Wayback Machine to keep copies of every source in a chain, one at a time."""
    while True:
        sid = _archive_q.get()
        try:
            src = db.get_source(sid)
            if src and not src.get("archive_url"):
                snap = web.wayback_save(src["url"])
                if snap:
                    db.update_source(sid, archive_url=snap)
        except Exception:
            pass


def _archive_chain(claim_id):
    for h in db.hops_for(claim_id):
        _archive_q.put(h["source_id"])


def frames_dir(check_id):
    return os.path.join(config.FILES_DIR, "checks", check_id)


def run_check(check_id):
    check = db.get_check(check_id)
    if not check:
        return
    warnings = []

    def step(s):
        db.update_check(check_id, step=s)

    try:
        db.update_check(check_id, status="running", step="Starting", error="")
        budget.take_check()
        budget.ensure_ai()

        # 2 · Capture
        packet = capture.capture(check["url"], frames_dir(check_id), progress=step)
        warnings += packet["warnings"]
        frames_meta = [{"t": f["t"], "name": f["name"]} for f in packet["frames"]]
        db.update_check(check_id, creator=packet["creator"], posted_at=packet["posted_at"], caption=packet["caption"],
                        transcript=packet["transcript"], transcript_source=packet["transcript_source"], duration=packet["duration"],
                        title=packet.get("title") or "")
        db.merge_check_data(check_id, frames=frames_meta, stats=packet["stats"], comments=packet["comments"], warnings=warnings)
        if not (packet["transcript"] or packet["caption"] or packet["frames"]):
            raise RuntimeError("Nothing to check: no video, transcript or caption could be fetched. " + " ".join(warnings))

        # 3 · Claim extraction, 4 · Triage
        step("Finding the claims")
        extracted = extract.extract(packet)
        claims = extract.triage(packet, extracted)
        db.update_check(check_id, thesis=extracted.get("thesis", ""), title=extracted.get("title", "") or packet.get("title", ""))
        for i, c in enumerate(claims):
            db.add_claim(check_id, i, quote=c["quote"], claim=c["claim"], timestamp=c.get("timestamp", ""), seconds=c.get("seconds"),
                         where_said=c.get("where", ""), type=c["type"], stated_source=c.get("stated_source", ""),
                         central=1 if c.get("central") else 0, rank=c.get("rank", 99),
                         quote_verified=1 if c.get("quote_verified") else 0, status=c["status"])
        if not any(c["status"] == "queued" for c in claims):
            warnings.append("No checkable factual claims were found in this video.")

        # 5 · Trace, 6 · Compare (in parallel, central claim first)
        _trace_queued(check_id, packet["transcript"], step)

        # 7 · Context + second opinions
        step("Adding context and second opinions")
        check = db.get_check(check_id)
        try:
            ctx = context.run_context(check, db.claims_for(check_id))
            if ctx:
                db.merge_check_data(check_id, left_out=ctx.get("left_out", []), overall=ctx.get("bottom_line", ""))
        except budget.BudgetError as e:
            warnings.append(e.public)
        except Exception as e:
            traceback.print_exc()
            warnings.append(f"Context could not be added ({str(e)[:100]}).")

        final = db.claims_for(check_id)
        failed = any(c["status"] == "failed" for c in final)
        db.merge_check_data(check_id, warnings=warnings, context_progress="")
        db.update_check(check_id, status="partial" if (failed or warnings) else "done", step="")
    except budget.BudgetError as e:
        db.merge_check_data(check_id, warnings=warnings)
        db.update_check(check_id, status="error", step="", error=e.public)
    except Exception as e:
        traceback.print_exc()
        db.merge_check_data(check_id, warnings=warnings)
        db.update_check(check_id, status="error", step="", error=str(e)[:400])


def _trace_queued(check_id, transcript, step):
    check = db.get_check(check_id)
    queued = [c for c in db.claims_for(check_id) if c["status"] == "queued"]
    if not queued:
        return
    step(f"Tracing {len(queued)} claim{'s' if len(queued) != 1 else ''} to the origin")

    def one(c):
        try:
            tracer.trace_claim(check, c, transcript, bool(c["central"]))
            _archive_chain(c["id"])
        except budget.BudgetError as e:
            db.update_claim(c["id"], status="failed", bottom_line=e.public)
        except Exception as e:
            traceback.print_exc()
            db.update_claim(c["id"], status="failed", bottom_line=f"The trace failed ({str(e)[:150]}). Tap Retry.")

    with ThreadPoolExecutor(max_workers=max(1, len(queued))) as ex:
        list(ex.map(one, queued))


def run_single_claim(check_id, claim_id):
    """'Check this' on a claim that was not traced the first time."""
    check = db.get_check(check_id)
    claim = db.get_claim(claim_id)
    if not check or not claim:
        return
    try:
        budget.ensure_ai()
        db.update_claim(claim_id, status="queued")
        _trace_queued(check_id, check.get("transcript", ""), lambda s: None)
    except budget.BudgetError as e:
        db.update_claim(claim_id, status="failed", bottom_line=e.public)
