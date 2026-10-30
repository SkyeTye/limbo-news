"""Stage 5 · Provenance trace (Diagram 2) and Stage 6 · Compare.

One agent per claim. It follows the claim from the video back through every
source that passed it along, down to where it started, and records each step
as a hop with an exact passage. Every passage goes through the receipt check:
it must appear word for word in the saved copy of that source, or the hop is
rejected.

Built on PHASE2_SYSTEM in research_pipeline.py, with a structured hop log
instead of a flat list."""
import json
import re

from . import config, db, llm, web
from ..tools.web_search import web_search
from .capture import PLATFORM_NAME

VERDICTS = ["matches_origin", "stretched", "missing_context", "misattributed", "outdated", "contradicted", "no_origin_found"]
ENDINGS = ["origin_found", "unsupported_assertion", "circular", "dead_end", "stopped"]
MUTATIONS = ["none", "number_changed", "scope_widened", "hedge_dropped", "time_shifted", "correlation_to_causation",
             "quote_clipped", "misattributed", "wrong_clip", "cherry_picked", "other"]

SYSTEM = """You trace one claim from a social video back to where it started. You are building a chain of receipts that a viewer will follow themselves, step by step, so every link must be real and quoted exactly.

THE CHAIN
The video is the top. Below it, each hop is a source that passed the claim along. The bottom is the origin: where the claim actually started.
Record hops in order from the one closest to the video down to the origin. Hop 1 is usually the source the creator named, or the earliest/most-cited place the claim appears.

SOURCE KINDS
- primary: the original document, dataset, study, court or government record, hearing transcript, original full footage, or the person's own statement or post. A claim's origin is a primary source.
- secondary: a news story, blog, post or video that reports or repeats the claim and points (or should point) to something else.
- unsupported: asserts the claim but cites nothing ("experts say", "reports show"). If this is the bottom of the chain, the origin is an unsupported assertion.

HOW TO WORK
1. search_library first. We keep every source and every past trace. Reuse them.
2. Then the stated source, web_search, and factcheck_leads. factcheck_leads gives you only the links that existing fact-check articles cite. Their conclusions are withheld on purpose: we check for ourselves. Never treat a fact-checker as an authority. If a fact-check article is itself part of how the claim spread, it can be a hop like any other source.
3. read_source to read a page (it is saved to the library). Pages are long: use find_in_source to locate the exact passage and the citations near it.
4. record_hop for each verified step. The passage must be copied word for word from the saved text (5 to 120 words). If it is rejected, use find_in_source and copy it exactly.
5. For every hop, run the mutation check: compare what this source says to what the hop above it says (for hop 1, compare to the video's quote). Did a number change, the scope widen, a hedge ("may", "some") disappear, old data get presented as new, correlation become causation, a quote get clipped, the speaker get swapped, the clip come from a different event, or one study get picked over many? Use "none" if it matches.
6. Follow each source's citation down. If a link is dead or blocked, try archive_lookup. If nothing works, the chain ends in a dead end.
7. Stop when you reach a primary source, when the bottom source cites nothing, when sources cite each other in a loop, or when you run out of budget. Then call complete_trace.

THE VERDICT (relationship between the video's version and the origin)
- matches_origin: the video's version matches what the origin says.
- stretched: the origin is real but the video exaggerates it (bigger number, wider scope, more certainty).
- missing_context: the words are accurate but leave out context that changes the meaning.
- misattributed: credited to the wrong person, group or source.
- outdated: true once, presented as current, no longer true.
- contradicted: the origin, or the best primary evidence, says otherwise.
- no_origin_found: you could not find where it started, or it started as an unsupported assertion.
Confidence is "high" only when the origin was found and its passage directly addresses the claim. Use "low" whenever you are relying on secondary sources.

Be fair to the creator. Quote them accurately. If a claim holds up, say so plainly. Say what is unknown. Write plainly and briefly. No em dashes."""

TOOLS = [
    {"name": "search_library", "description": "Search our saved sources and past traces before going to the web. Free.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "web_search", "description": "Search the web. Returns titles, snippets and URLs. Use specific queries: exact wording, the key number, named people, studies, agencies.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}}, "required": ["query"]}},
    {"name": "factcheck_leads", "description": "Find the sources that existing fact-check articles cite for this claim. Their verdicts are withheld. Use once.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "The claim in a few plain words"}}, "required": ["query"]}},
    {"name": "read_source", "description": "Read a web page or PDF in full and save it to the library. Shows the first part; use offset to page or find_in_source to search it. Counts against the read budget unless it is already in the library.",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string"}, "offset": {"type": "integer", "description": "Character offset to start from (paging an already-read source is free)"}}, "required": ["url"]}},
    {"name": "find_in_source", "description": "Find words in a saved source. Returns the surrounding text with exact wording and nearby links. Free.",
     "input_schema": {"type": "object", "properties": {"source_id": {"type": "string"}, "terms": {"type": "array", "items": {"type": "string"}}}, "required": ["source_id", "terms"]}},
    {"name": "archive_lookup", "description": "Get the Wayback Machine copy of a dead, deleted or blocked page and read it. Counts as a read.",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
    {"name": "record_hop", "description": "Record the next hop in the chain (closest to the video first, origin last). The passage must appear word for word in the saved source.",
     "input_schema": {"type": "object", "properties": {
         "source_id": {"type": "string"},
         "passage": {"type": "string", "description": "Exact words from the source (5 to 120 words) that carry the claim"},
         "kind": {"type": "string", "enum": ["primary", "secondary", "unsupported"]},
         "says": {"type": "string", "description": "One plain sentence: what this source says about the claim"},
         "cites": {"type": "string", "description": "What this source points to as its own source (URL or description), or 'nothing'"},
         "mutation": {"type": "string", "enum": MUTATIONS},
         "mutation_note": {"type": "string", "description": "If the claim changed here, exactly how (quote both versions). '' if none."},
     }, "required": ["source_id", "passage", "kind", "says", "cites", "mutation", "mutation_note"]}},
    {"name": "complete_trace", "description": "Finish the trace with the verdict.",
     "input_schema": {"type": "object", "properties": {
         "ending": {"type": "string", "enum": ENDINGS},
         "verdict": {"type": "string", "enum": VERDICTS},
         "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
         "bottom_line": {"type": "string", "description": "One or two plain sentences a viewer reads first"},
         "video_vs_origin": {"type": "string", "description": "What the origin actually says vs what the video says. '' if no origin."},
         "mutations": {"type": "array", "items": {"type": "object", "properties": {"type": {"type": "string", "enum": MUTATIONS}, "note": {"type": "string"}}, "required": ["type", "note"]}},
         "unknowns": {"type": "array", "items": {"type": "string"}},
         "hop_order": {"type": "array", "items": {"type": "integer"}, "description": "Optional: hop ids in the right order, closest to the video first"},
         "drop_hop_ids": {"type": "array", "items": {"type": "integer"}, "description": "Optional: hops that turned out not to belong in the chain"},
     }, "required": ["ending", "verdict", "confidence", "bottom_line", "video_vs_origin", "mutations", "unknowns"]}},
]


# ---------- the receipt check ----------

def passage_pattern(passage):
    words = re.findall(r"\w+", passage or "", re.UNICODE)
    if not words:
        return None
    # Between words allow any punctuation/whitespace, or markdown link syntax left in the saved text.
    return re.compile(r"(?:[\W_]|\]\([^)]*\))+".join(re.escape(w) for w in words), re.I | re.UNICODE)


def find_passage(text, passage):
    pat = passage_pattern(passage)
    if not pat:
        return -1
    m = pat.search(text or "")
    return m.start() if m else -1


class Trace:
    def __init__(self, check, claim, packet_summary, hop_limit, read_limit, model, attempt=1):
        self.check, self.claim = check, claim
        self.summary = packet_summary
        self.hop_limit, self.read_limit = hop_limit, read_limit
        self.model, self.attempt = model, attempt
        self.reads = 0
        self.read_this_trace = set()
        self.factchecks_done = False
        self.searches = 0

    # -- helpers --
    def _log(self, tool, inp, result):
        db.audit(self.check["id"], self.claim["id"], f"tracer:{self.model}:{self.attempt}", tool, inp, result)

    def _progress(self, action):
        hops = db.hops_for(self.claim["id"])
        db.merge_claim_data(self.claim["id"], progress={"reads": self.reads, "read_limit": self.read_limit,
                                                         "hops": len(hops), "hop_limit": self.hop_limit, "last": action[:140]})

    def _source_view(self, src, offset=0, from_library=False):
        text = src.get("full_text") or ""
        offset = max(0, min(int(offset or 0), len(text)))
        chunk = text[offset: offset + config.PAGE_CHARS]
        more = len(text) - (offset + len(chunk))
        head = [f"source_id: {src['id']}", f"title: {src.get('title') or ''}", f"url: {src['url']}"]
        if src.get("published_at"):
            head.append(f"published: {src['published_at']}")
        if from_library:
            head.append(f"(from our library, first read {src.get('first_read','')[:10]})")
        head.append(f"length: {len(text)} characters. Showing {offset}-{offset + len(chunk)}." + (f" {more} more: page with offset or use find_in_source." if more > 0 else ""))
        return "\n".join(head) + "\n\n" + chunk

    # -- tools --
    def handle(self, name, inp):
        try:
            result, done = getattr(self, "t_" + name)(**inp)
        except TypeError as e:
            result, done = f"ERROR: bad arguments ({e})", None
        self._log(name, inp, result if done is None else json.dumps(done)[:1500])
        return result, done

    def t_search_library(self, query):
        sources = db.search_sources(query, 8)
        past = db.search_past_claims(query, exclude_check=self.check["id"], limit=4)
        out = []
        if past:
            out.append("PAST TRACES OF SIMILAR CLAIMS:")
            for p in past:
                chain = db.chain_for(p["id"])
                bottom = chain[-1] if chain else None
                out.append(f"- \"{p['claim'][:160]}\" -> ended {p['ending']}, verdict {p['verdict']} ({p['confidence']})"
                           + (f"; bottom of chain: {bottom['title'] or bottom['url']} [{bottom['url']}] source_id {bottom['source_id']}" if bottom else ""))
        if sources:
            out.append("SAVED SOURCES:")
            for s in sources:
                out.append(f"- source_id {s['id']} | {s.get('title') or ''} | {s['url']} | {s.get('published_at') or ''}\n  …{(s.get('snip') or '').strip()[:300]}…")
        self._progress("searched the library")
        return ("\n".join(out) if out else "Nothing in the library yet for this. Go to the web."), None

    def t_web_search(self, query, max_results=6):
        self.searches += 1
        if self.searches > self.read_limit + 6:
            return "Search limit reached for this claim. Work with what you have.", None
        res = web_search(query, min(int(max_results or 6), 10))
        self._progress(f"searched: {query}")
        lines = []
        for r in res:
            if r.get("error"):
                lines.append(f"(search error: {r['error'][:120]})")
                continue
            lines.append(f"- {r.get('title','')}\n  {r.get('url','')}\n  {r.get('snippet','')[:300]}")
        return ("\n".join(lines) or "No results."), None

    def t_factcheck_leads(self, query):
        if self.factchecks_done:
            return "Already done for this claim.", None
        self.factchecks_done = True
        found, leads = web.factcheck_leads(query)
        prev = (db.get_claim(self.claim["id"]) or {}).get("data", {}).get("factchecks") or []
        merged = prev + [f for f in found if not any(p.get("url") == f.get("url") for p in prev)]
        db.merge_claim_data(self.claim["id"], factchecks=merged)  # kept for the second-opinions stage, not shown to you
        self._progress("checked other fact-checkers' sources")
        if not found:
            return "No existing fact-check articles found for this claim. Trace it from scratch.", None
        lines = [f"{len(found)} existing fact-check article(s) found. Their conclusions are withheld so they cannot steer you.",
                 "These are the sources THEY link to. Some may lead you to the origin faster. Read them yourself:"]
        lines += [f"- {l['url']}  ({l['link_text']})" for l in leads] or ["(they cite no outside links)"]
        return "\n".join(lines), None

    def _count_read(self, url):
        key = db.normalize_url(url)
        if key in self.read_this_trace:
            return True
        if self.reads >= self.read_limit:
            return False
        self.reads += 1
        self.read_this_trace.add(key)
        return True

    def t_read_source(self, url, offset=0):
        key = db.normalize_url(url)
        existing = db.get_source_by_url(url)
        if offset and existing and existing.get("full_text"):
            return self._source_view(existing, offset), None
        if key not in self.read_this_trace and not (existing and existing.get("full_text")) and self.reads >= self.read_limit:
            return ("BUDGET: no reads left for this claim. Record any hop you have verified, then call complete_trace "
                    "(ending 'stopped' if you have not reached the origin)."), None
        try:
            src, from_lib = web.read_into_library(url)
        except Exception as e:
            self._progress(f"could not open {url}")
            return f"Could not open this page: {str(e)[:200]}. Try archive_lookup, another copy, or the PDF.", None
        if not from_lib:
            self._count_read(url)
        else:
            self.read_this_trace.add(key)
        self._progress(f"read {src.get('title') or url}")
        return self._source_view(src, offset, from_library=from_lib), None

    def t_find_in_source(self, source_id, terms):
        src = db.get_source(source_id)
        if not src:
            return "No source with that id. Use the source_id shown by read_source.", None
        text = src.get("full_text") or ""
        if isinstance(terms, str):
            terms = [terms]
        windows = []
        for term in terms[:6]:
            pat = passage_pattern(term) if len(re.findall(r"\w+", term)) > 1 else re.compile(re.escape(term), re.I)
            if not pat:
                continue
            for m in list(pat.finditer(text))[:4]:
                windows.append((max(0, m.start() - 500), min(len(text), m.end() + 500)))
        if not windows:
            return f"None of those words appear in this source ({len(text)} characters).", None
        windows.sort()
        merged = [list(windows[0])]
        for a, b in windows[1:]:
            if a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        parts = [f"[{a}-{b}] …{text[a:b]}…" for a, b in merged[:6]]
        return "\n\n".join(parts), None

    def t_archive_lookup(self, url):
        snap = web.wayback_lookup(url)
        if not snap:
            return "No Wayback Machine copy exists. Try another copy, or treat this as a dead end.", None
        if not self._count_read(snap):
            return "BUDGET: no reads left. Record what you have and call complete_trace.", None
        try:
            res = web.fetch(snap)
        except Exception as e:
            return f"The archived copy could not be read either ({str(e)[:150]}).", None
        sid, _ = db.save_source(url, res["text"], title=res.get("title", ""), final_url=snap, fetched_via="wayback", archive_url=snap)
        src = db.get_source(sid)
        self._progress(f"read archived copy of {url}")
        return "(Archived copy from the Wayback Machine, saved under the original URL.)\n" + self._source_view(src), None

    def t_record_hop(self, source_id, passage, kind, says, cites, mutation="none", mutation_note=""):
        src = db.get_source(source_id)
        if not src:
            return "REJECTED: unknown source_id. Read the source first.", None
        hops = db.hops_for(self.claim["id"])
        if len(hops) >= self.hop_limit:
            return f"REJECTED: hop limit ({self.hop_limit}) reached. Call complete_trace now.", None
        for h in hops:
            if h["source_id"] == source_id:
                return (f"REJECTED: this source is already hop {h['position']}. If sources cite each other in a loop, "
                        "finish with ending 'circular'."), None
        n_words = len(re.findall(r"\w+", passage or ""))
        if n_words < 5 or n_words > 120:
            return f"REJECTED: the passage must be 5 to 120 words (it is {n_words}).", None
        loc = find_passage(src.get("full_text"), passage)
        if loc < 0:
            return ("REJECTED by the receipt check: that passage does not appear word for word in the saved text of this source. "
                    "Use find_in_source and copy the words exactly."), None
        pid = db.add_passage(source_id, passage.strip(), loc)
        hid, pos = db.add_hop(self.claim["id"], source_id, pid, kind, says, cites, mutation or "none", mutation_note or "", self.attempt)
        if hops:
            db.add_citation(hops[-1]["source_id"], source_id, hops[-1].get("cites", ""))
        kinds = {"primary": "primary", "secondary": "secondary", "unsupported": "unsupported"}
        if not src.get("kind") or src.get("kind") == "unknown" or kind == "primary":
            db.update_source(source_id, kind=kinds.get(kind, "unknown"))
        self._progress(f"recorded hop {pos}: {src.get('title') or src['url']}")
        left = self.hop_limit - pos
        return f"Recorded as hop {pos} (hop id {hid}). Receipt check passed. {left} hop(s) left.", None

    def t_complete_trace(self, ending, verdict, confidence, bottom_line, video_vs_origin="", mutations=None, unknowns=None, hop_order=None, drop_hop_ids=None):
        if hop_order or drop_hop_ids:
            db.reorder_hops(self.claim["id"], hop_order or [], drop_hop_ids or [])
        hops = db.hops_for(self.claim["id"])
        if ending == "origin_found" and not any(h["kind"] == "primary" for h in hops):
            return "REJECTED: ending 'origin_found' needs a recorded hop of kind 'primary'. Record it, or choose another ending.", None
        if verdict != "no_origin_found" and not hops:
            return "REJECTED: a verdict other than no_origin_found needs at least one recorded hop with a receipt.", None
        if ending == "origin_found" and hops and hops[-1]["kind"] != "primary":
            prim = [h["id"] for h in hops if h["kind"] == "primary"]
            rest = [h["id"] for h in hops if h["kind"] != "primary"]
            db.reorder_hops(self.claim["id"], rest + prim)
        return "Trace recorded.", {
            "ending": ending, "verdict": verdict, "confidence": confidence, "bottom_line": bottom_line,
            "video_vs_origin": video_vs_origin, "mutations": mutations or [], "unknowns": unknowns or [],
        }


def _prompt(check, claim, packet_summary, prior=None):
    lines = [
        f"VIDEO: {PLATFORM_NAME.get(check.get('platform'), 'social')} video by {check.get('creator') or 'unknown'}"
        + (f", posted {check['posted_at']}" if check.get("posted_at") else "") + f". {check.get('url')}",
        f"THESIS OF THE VIDEO: {check.get('thesis') or ''}",
        "",
        f"CLAIM TO TRACE: {claim['claim']}",
        f"EXACT WORDS ({claim.get('where_said') or 'speech'}, at {claim.get('timestamp') or '?'}): \"{claim['quote']}\"",
        f"CLAIM TYPE: {claim.get('type')}",
        f"STATED SOURCE: {claim.get('stated_source') or '(the creator names none)'}",
        "",
        "Context from the transcript around it:",
        packet_summary or "(none)",
    ]
    if prior:
        lines += ["", "A FIRST TRACER ALREADY WORKED ON THIS AND ENDED BADLY. Its chain so far (hop id: source -> passage):", prior,
                  "Continue from here. Keep hops that hold up, drop any that do not (drop_hop_ids), and push further down the chain."]
    return "\n".join(lines)


def _context_window(transcript, seconds, quote):
    if not transcript:
        return ""
    lines = transcript.splitlines()
    idx = None
    if quote:
        q = re.sub(r"\W+", " ", quote.lower()).strip()[:40]
        for i, l in enumerate(lines):
            if q and q[:25] in re.sub(r"\W+", " ", l.lower()):
                idx = i
                break
    if idx is None and seconds is not None:
        for i, l in enumerate(lines):
            m = re.match(r"\[(\d+):(\d\d)\]", l)
            if m and int(m.group(1)) * 60 + int(m.group(2)) >= seconds:
                idx = i
                break
    if idx is None:
        return "\n".join(lines[:12])[:2000]
    return "\n".join(lines[max(0, idx - 4): idx + 5])[:2000]


def _prior_chain(claim_id):
    out = []
    for h in db.chain_for(claim_id):
        out.append(f"- hop id {h['id']} (position {h['position']}, {h['kind']}): {h.get('title') or h['url']} [{h['url']}] source_id {h['source_id']}\n  \"{(h.get('passage') or '')[:300]}\"\n  cites: {h.get('cites','')}")
    return "\n".join(out) or "(no hops recorded)"


def trace_claim(check, claim, transcript, central):
    """Run the trace (and the Opus retry if the central claim ends badly). Updates the claim row."""
    hop_limit = config.CENTRAL_HOPS if central else config.OTHER_HOPS
    read_limit = config.CENTRAL_READS if central else config.OTHER_READS
    ctx = _context_window(transcript, claim.get("seconds"), claim.get("quote"))
    db.update_claim(claim["id"], status="tracing", model=config.TRACE_MODEL)

    def run(model, hops, reads, attempt, prior=None):
        t = Trace(check, claim, ctx, hops, reads, model, attempt)
        max_turns = reads * 2 + hops + 10
        return llm.run_agent(model, SYSTEM, _prompt(check, claim, ctx, prior), TOOLS, t.handle, "complete_trace", max_turns=max_turns)

    result = run(config.TRACE_MODEL, hop_limit, read_limit, 1)
    attempts = [{"model": config.TRACE_MODEL, "result": result}]
    bad = (result is None or result.get("ending") in ("stopped", "dead_end") or result.get("confidence") == "low")
    if central and bad and config.OPUS_RETRY:
        db.update_claim(claim["id"], model=config.RETRY_MODEL)
        db.merge_claim_data(claim["id"], retrying=True)
        retry = run(config.RETRY_MODEL, hop_limit * 2, read_limit * 2, 2, prior=_prior_chain(claim["id"]))
        attempts.append({"model": config.RETRY_MODEL, "result": retry})
        if retry is not None:
            result = retry
    if result is None:
        result = {"ending": "stopped", "verdict": "no_origin_found", "confidence": "low",
                  "bottom_line": "The trace ran out of steps before it could finish. The partial chain is shown below.",
                  "video_vs_origin": "", "mutations": [], "unknowns": ["Where this claim started."]}
    status = "stopped" if result["ending"] == "stopped" else "done"
    db.update_claim(claim["id"], status=status, ending=result["ending"], verdict=result["verdict"],
                    confidence=result["confidence"], bottom_line=result["bottom_line"])
    db.merge_claim_data(claim["id"], video_vs_origin=result.get("video_vs_origin", ""), mutations=result.get("mutations", []),
                        unknowns=result.get("unknowns", []), attempts=[{"model": a["model"], "ending": (a["result"] or {}).get("ending", "unfinished")} for a in attempts],
                        retrying=False)
    return result
