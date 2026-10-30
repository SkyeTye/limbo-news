"""Web routes for the fact-checker: the private /check pages and /api/check/*.

Security (basic, as decided 2026-10-30):
- Everything under /check and /api/check needs the password (FACTCHECK_PASSWORD).
- The iPhone Shortcut uses a separate token, derived from the password and never
  shown on screen unless you tap Show, so screen recordings don't leak it.
  Changing the password replaces the token and signs everyone out.
- noindex headers + robots.txt so search engines skip it. Daily check limit in budget.py.
"""
import hashlib
import hmac
import os
import time
from collections import defaultdict, deque
from datetime import timedelta

from flask import Blueprint, abort, jsonify, request, send_file, send_from_directory, session

from . import budget, capture, config, db, pipeline

bp = Blueprint("factcheck", __name__)
_fails = defaultdict(deque)
_research_queue = None
_storage = None


def register(app, research_queue=None, storage=None):
    global _research_queue, _storage
    _research_queue, _storage = research_queue, storage
    pw = config.password()
    app.secret_key = os.environ.get("SECRET_KEY") or hashlib.sha256(("limbo-session:" + (pw or os.urandom(16).hex())).encode()).hexdigest()
    app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SECURE=bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID")),
                      PERMANENT_SESSION_LIFETIME=timedelta(days=120))
    app.register_blueprint(bp)
    pipeline.start_worker()


# ---------- auth ----------

def _fingerprint():
    return hashlib.sha256(("fc:" + config.password()).encode()).hexdigest()[:20]


def ingest_token():
    pw = config.password()
    return hmac.new(pw.encode(), b"factcheck-ingest", hashlib.sha256).hexdigest()[:40] if pw else ""


def _authed():
    if not config.password():
        return False
    if session.get("fc") == _fingerprint():
        return True
    return False


def _token_ok():
    tok = ingest_token()
    got = (request.headers.get("Authorization") or "").replace("Bearer ", "").strip()
    return bool(tok) and hmac.compare_digest(got, tok)


def _require():
    if not config.password():
        abort(503, "The fact-checker is off until FACTCHECK_PASSWORD is set in Railway.")
    if not _authed():
        abort(401)


def _ip():
    return (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()


@bp.after_request
def _noindex(resp):
    if request.path.startswith(("/check", "/api/check")):
        resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if request.path.startswith("/api/check"):
            resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.errorhandler(401)
def _e401(e):
    return jsonify({"error": "Sign in first.", "auth": False}), 401


@bp.route("/robots.txt")
def robots():
    return "User-agent: *\nDisallow: /check\nDisallow: /api/\n", 200, {"Content-Type": "text/plain"}


@bp.route("/api/check/session")
def session_info():
    return jsonify({"configured": bool(config.password()), "authed": _authed()})


@bp.route("/api/check/login", methods=["POST"])
def login():
    if not config.password():
        return jsonify({"error": "Set FACTCHECK_PASSWORD in Railway first."}), 503
    q = _fails[_ip()]
    while q and q[0] < time.time() - 900:
        q.popleft()
    if len(q) >= 8:
        return jsonify({"error": "Too many tries. Wait 15 minutes."}), 429
    pw = ((request.get_json(silent=True) or {}).get("password") or "")
    if not hmac.compare_digest(pw.encode(), config.password().encode()):
        q.append(time.time())
        time.sleep(0.6)
        return jsonify({"error": "Wrong password."}), 401
    session.permanent = True
    session["fc"] = _fingerprint()
    return jsonify({"ok": True})


@bp.route("/api/check/logout", methods=["POST"])
def logout():
    session.pop("fc", None)
    return jsonify({"ok": True})


# ---------- pages ----------

def _frontend():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend"))


@bp.route("/check")
@bp.route("/check/")
@bp.route("/check/<path:_rest>")
def page(_rest=None):
    return send_from_directory(_frontend(), "check.html")


# ---------- API ----------

@bp.route("/api/check", methods=["POST"])
def create():
    if not (_authed() or _token_ok()):
        if not config.password():
            return jsonify({"error": "Set FACTCHECK_PASSWORD in Railway first."}), 503
        return jsonify({"error": "Not signed in (or the Shortcut's Authorization value is wrong)."}), 401
    body = request.get_json(silent=True) or request.form.to_dict() or {}
    raw = capture.first_url(body.get("url") or "") or capture.first_url(body.get("text") or "")
    url = capture.clean_url(raw)
    if not url:
        return jsonify({"error": "That does not look like a link. Share or paste the video link."}), 400
    origin = request.host_url.rstrip("/")
    if request.headers.get("X-Forwarded-Proto") == "https":
        origin = origin.replace("http://", "https://", 1)
    force = bool(body.get("force"))
    existing = db.find_check_by_url(url)
    if existing and not force and existing["status"] != "error":
        return jsonify({"ok": True, "id": existing["id"], "duplicate": True, "open_url": f"{origin}/check/{existing['id']}",
                        "message": "Already checked. Opening it."})
    try:
        budget.ensure_ai()
    except budget.BudgetError as e:
        return jsonify({"error": e.public}), 429
    cid = db.create_check(url, url, capture.platform_of(url), via="share" if body.get("via") == "share" else "app")
    pipeline.submit(("check", cid))
    return jsonify({"ok": True, "id": cid, "open_url": f"{origin}/check/{cid}",
                    "message": "Checking. Claims show up in about a minute, verdicts a few minutes later."})


@bp.route("/api/checks")
def list_checks():
    _require()
    return jsonify({"checks": db.list_checks(), "queue": pipeline.queue_size(), "current": pipeline.current()})


def _claim_view(c):
    chain = db.chain_for(c["id"]) if c["status"] not in ("opinion", "unchecked") else []
    return {**c, "chain": chain, "max_depth": len(chain)}


@bp.route("/api/check/<cid>")
def get_check(cid):
    _require()
    check = db.get_check(cid)
    if not check:
        abort(404)
    claims = [_claim_view(c) for c in db.claims_for(cid)]
    for c in claims:
        (c.get("data") or {}).pop("factchecks_raw", None)
    return jsonify({**check, "claims": claims, "voices": db.voices_for(cid), "second_opinions": db.second_opinions_for(cid)})


@bp.route("/api/check/<cid>/retry", methods=["POST"])
def retry(cid):
    _require()
    check = db.get_check(cid)
    if not check:
        abort(404)
    if check["status"] in ("queued", "running"):
        return jsonify({"error": "Already running."}), 409
    new_id = db.create_check(check["url"], check["clean_url"], check["platform"], via=check.get("via", "app"))
    pipeline.submit(("check", new_id))
    return jsonify({"ok": True, "id": new_id})


@bp.route("/api/check/<cid>/claims/<claim_id>/trace", methods=["POST"])
def trace_one(cid, claim_id):
    _require()
    claim = db.get_claim(claim_id)
    if not claim or claim["check_id"] != cid:
        abort(404)
    if claim["status"] in ("queued", "tracing"):
        return jsonify({"error": "Already running."}), 409
    if claim["status"] in ("failed", "done", "stopped"):
        db.reorder_hops(claim_id, [], [h["id"] for h in db.hops_for(claim_id)])  # start a clean chain
    db.update_claim(claim_id, status="queued", verdict="", ending="", confidence="", bottom_line="")
    db.add_event(cid, claim_id, "check_claim")
    pipeline.submit(("claim", cid, claim_id))
    return jsonify({"ok": True})


@bp.route("/api/check/<cid>/claims/<claim_id>/deeper", methods=["POST"])
def deeper(cid, claim_id):
    _require()
    claim = db.get_claim(claim_id)
    check = db.get_check(cid)
    if not claim or not check or claim["check_id"] != cid:
        abort(404)
    if not (_research_queue and _storage):
        return jsonify({"error": "The deep research pipeline is not available."}), 503
    existing = (claim.get("data") or {}).get("deeper_article")
    if existing:
        return jsonify({"ok": True, "article_id": existing, "url": f"/article/{existing}"})
    chain = db.chain_for(claim_id)
    topic = (f"{claim['claim']} (claim made in a {capture.PLATFORM_NAME.get(check['platform'], 'social')} video by "
             f"{check.get('creator') or 'a creator'}: \"{claim['quote']}\")")
    if chain:
        topic += " Known sources so far: " + "; ".join(h["url"] for h in chain[:6])
    job = _storage.create_job(topic)
    _research_queue.submit(topic, job["id"])
    db.merge_claim_data(claim_id, deeper_article=job["id"])
    db.add_event(cid, claim_id, "deeper")
    return jsonify({"ok": True, "article_id": job["id"], "url": f"/article/{job['id']}"})


@bp.route("/api/check/<cid>/events", methods=["POST"])
def event(cid):
    _require()
    b = request.get_json(silent=True) or {}
    action = str(b.get("action") or "")
    if action not in {"view", "open_receipts", "open_saved", "open_live", "open_archive", "open_file", "open_video", "open_log"}:
        return jsonify({"error": "unknown action"}), 400
    db.add_event(cid, b.get("claim_id") or "", action, b.get("depth") or 0, b.get("max_depth") or 0, b.get("source_id") or "")
    return jsonify({"ok": True})


@bp.route("/api/check/<cid>/audit")
def audit(cid):
    _require()
    return jsonify({"audit": db.audit_for(cid)})


@bp.route("/api/check/<cid>/frames/<name>")
def frame(cid, name):
    _require()
    if "/" in name or ".." in name or not name.endswith(".jpg"):
        abort(404)
    return send_from_directory(pipeline.frames_dir(cid), name, max_age=86400)


@bp.route("/api/check/sources/<sid>")
def source(sid):
    _require()
    s = db.get_source(sid)
    if not s:
        abort(404)
    text = s.pop("full_text", "") or ""
    return jsonify({**s, "text": text[:300_000], "truncated": len(text) > 300_000, "passages": db.passages_for(sid),
                    "has_file": bool(s.get("file_path") and os.path.exists(s["file_path"]))})


@bp.route("/api/check/sources/<sid>/file")
def source_file(sid):
    _require()
    s = db.get_source(sid)
    if not s or not s.get("file_path") or not os.path.exists(s["file_path"]):
        abort(404)
    return send_file(s["file_path"], mimetype="application/pdf")


@bp.route("/api/check/settings")
def settings():
    _require()
    origin = request.host_url.rstrip("/")
    if request.headers.get("X-Forwarded-Proto") == "https":
        origin = origin.replace("http://", "https://", 1)
    return jsonify({
        "endpoint": f"{origin}/api/check",
        "token": "Bearer " + ingest_token(),
        "usage": budget.summary(),
        "library": db.library_stats(),
        "rabbit_hole": db.rabbit_hole_stats(),
        "models": {"trace": config.TRACE_MODEL, "retry": config.RETRY_MODEL if config.OPUS_RETRY else "off"},
        "connections": {
            "claude": bool(config.anthropic_key()),
            "post_lookup": bool(config.scrape_key()),
            "transcripts": bool(config.groq_key()),
            "search": "Brave" if os.environ.get("BRAVE_SEARCH_API_KEY") else "DuckDuckGo (free, rate-limited)",
            "factcheck_api": bool(config.factcheck_api_key()),
            "persistent_storage": config.DATA_DIR_IS_PERSISTENT,
        },
    })
