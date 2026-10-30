"""Stage 7 · Context and second opinions. Runs after the verdicts are set.

Borrowed from PHASE3_SYSTEM, lighter: named people on both sides with exact
quotes (receipt-checked like the hops), what the video leaves out, and only
now the other fact-checkers' verdicts, shown next to ours with disagreements
flagged."""
import json
import re

from . import config, db, llm
from .tracer import Trace, TOOLS as TRACE_TOOLS, find_passage, VERDICTS

SYSTEM = """You add context to a finished fact-check of a social video. The verdicts are already set from our own source tracing. Your job is what a fair-minded viewer would also want:

1. VOICES: real, named people on different sides of the central claim, with their exact words. At least one who supports or accepts it and one who disputes or qualifies it, when such people exist. Quote them word for word from a page you read (record_voice runs the same receipt check as the trace). Never summarize a position without a named person and a quote.
2. LEFT OUT: the context a viewer needs that the video does not give (what the full study also found, what happened after, the base rate, the other side's main evidence).
3. SECOND OPINIONS: we found existing fact-check articles while tracing. Their ratings were hidden from the tracer. Now record each one: its rating, whether it agrees with our verdict, and why they differ if they do. Read the article if the rating is unknown. Do not change our verdict.

Be even-handed. Write plainly and briefly. No em dashes."""

EXTRA_TOOLS = [
    {"name": "record_voice", "description": "Record a named person's view on a claim, with their exact words from a saved source.",
     "input_schema": {"type": "object", "properties": {
         "claim_id": {"type": "string"}, "name": {"type": "string"}, "affiliation": {"type": "string"},
         "stance": {"type": "string", "enum": ["supports", "disputes", "qualifies"]},
         "quote": {"type": "string", "description": "Their exact words, 5 to 80 words, word for word from the source"},
         "source_id": {"type": "string"}}, "required": ["claim_id", "name", "affiliation", "stance", "quote", "source_id"]}},
    {"name": "record_second_opinion", "description": "Record another fact-checker's verdict next to ours.",
     "input_schema": {"type": "object", "properties": {
         "claim_id": {"type": "string"}, "publisher": {"type": "string"}, "url": {"type": "string"},
         "rating": {"type": "string"}, "agrees": {"type": "string", "enum": ["yes", "partly", "no"]},
         "note": {"type": "string", "description": "If they differ from us, why, in one or two sentences"}},
     "required": ["claim_id", "publisher", "url", "rating", "agrees", "note"]}},
    {"name": "complete_context", "description": "Finish.",
     "input_schema": {"type": "object", "properties": {
         "left_out": {"type": "array", "items": {"type": "string"}, "description": "Context the video leaves out, one plain sentence each"},
         "bottom_line": {"type": "string", "description": "One or two sentences on the video as a whole, based on the verdicts"}},
         "required": ["left_out", "bottom_line"]}},
]

KEEP = {"search_library", "web_search", "read_source", "find_in_source", "archive_lookup"}


class ContextAgent(Trace):
    def __init__(self, check, central_claim, claim_ids):
        super().__init__(check, central_claim, "", 0, config.CONTEXT_READS, config.CONTEXT_MODEL, attempt=0)
        self.claim_ids = set(claim_ids)
        self.voices = 0

    def _progress(self, action):
        db.merge_check_data(self.check["id"], context_progress=action[:140])

    def _log(self, tool, inp, result):
        db.audit(self.check["id"], "", f"context:{self.model}", tool, inp, result)

    def t_record_voice(self, claim_id, name, affiliation, stance, quote, source_id):
        if claim_id not in self.claim_ids:
            return "REJECTED: unknown claim_id.", None
        src = db.get_source(source_id)
        if not src:
            return "REJECTED: unknown source_id. Read the source first.", None
        n = len(re.findall(r"\w+", quote or ""))
        if n < 5 or n > 80:
            return f"REJECTED: the quote must be 5 to 80 words (it is {n}).", None
        loc = find_passage(src.get("full_text"), quote)
        if loc < 0:
            return "REJECTED by the receipt check: those words do not appear word for word in that source. Use find_in_source.", None
        pid = db.add_passage(source_id, quote.strip(), loc)
        db.add_voice(self.check["id"], claim_id, name, affiliation, stance, quote.strip(), source_id, pid)
        self.voices += 1
        self._progress(f"recorded {name}")
        return "Recorded. Receipt check passed.", None

    def t_record_second_opinion(self, claim_id, publisher, url, rating, agrees, note):
        if claim_id not in self.claim_ids:
            return "REJECTED: unknown claim_id.", None
        db.add_second_opinion(self.check["id"], claim_id, publisher, url, rating, agrees, note)
        return "Recorded.", None

    def t_complete_context(self, left_out, bottom_line):
        return "Done.", {"left_out": left_out, "bottom_line": bottom_line}


def run_context(check, claims):
    traced = [c for c in claims if c["status"] in ("done", "stopped")]
    if not traced:
        return None
    central = next((c for c in traced if c["central"]), traced[0])
    db.clear_context(check["id"])
    lines = [f"VIDEO: {check.get('url')} by {check.get('creator') or 'unknown'}", f"THESIS: {check.get('thesis')}", "", "OUR VERDICTS:"]
    for c in traced:
        chain = db.chain_for(c["id"])
        bottom = chain[-1] if chain else None
        lines.append(f"- claim_id {c['id']}{' (CENTRAL)' if c['id'] == central['id'] else ''}: \"{c['claim']}\"\n  verdict {c['verdict']} ({c['confidence']}), ending {c['ending']}: {c['bottom_line']}"
                     + (f"\n  origin/bottom of chain: {bottom.get('title') or bottom['url']} [{bottom['url']}]" if bottom else ""))
        fcs = (c.get("data") or {}).get("factchecks") or []
        for f in fcs:
            lines.append(f"  existing fact-check: {f.get('publisher')} | {f.get('title','')} | {f.get('url')} | rating: {f.get('rating') or 'unknown, read it'}")
    tools = [t for t in TRACE_TOOLS if t["name"] in KEEP] + EXTRA_TOOLS
    agent = ContextAgent(check, central, [c["id"] for c in traced])
    out = llm.run_agent(config.CONTEXT_MODEL, SYSTEM, "\n".join(lines), tools, agent.handle, "complete_context",
                        max_turns=config.CONTEXT_READS * 2 + 12)
    return out
