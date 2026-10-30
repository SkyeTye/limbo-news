"""Stage 3 · Claim extraction and Stage 4 · Triage.

Claude watches the frames, reads the transcript and caption, and pulls out the
thesis plus every factual claim with its timestamp, exact words and stated
source. Then the top claims (max 3, central first) are picked for tracing."""
import re

from . import config, llm
from .capture import PLATFORM_NAME, stamp

CLAIM_TYPES = ["statistic", "quote", "event", "study", "causal", "clip", "comparison", "prediction", "opinion"]

SYSTEM = """You are the first step of a fact-checking tool. You read a short social video (still frames, transcript, caption) and pull out exactly what it claims. You do not judge whether anything is true. That comes later.

Rules:
- Use only what the frames, transcript and caption show. Never guess. Never add facts.
- "quote" must be the creator's exact words, copied from the transcript, from on-screen text visible in a frame, or from the caption. Do not paraphrase in "quote".
- "claim" is a plain, specific, checkable restatement of the quote, with names, numbers, places and dates filled in from context. One claim per item: split compound sentences ("crime is up 40% because of X" is a statistic claim and a causal claim).
- On-screen text, charts, screenshots of headlines or posts shown in the video are claims too. Read them carefully.
- "stated_source" is what the creator says the claim comes from ("a Harvard study", "the CDC", "this clip"), or "" if they name nothing.
- Opinions, value judgments, jokes and predictions get type "opinion" or "prediction". They are not checked as facts.
- The thesis is one sentence: what the video wants the viewer to believe or do. Individual claims can be true while the thesis is not.
- Mark exactly one claim as central: the one the thesis most depends on. Rank the others by how much the thesis depends on them and how specific and checkable they are.
- Write plainly. No em dashes."""

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Concrete label for the video, max 8 words"},
        "thesis": {"type": "string", "description": "One sentence: what the video wants you to believe"},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "quote": {"type": "string"},
                    "claim": {"type": "string"},
                    "timestamp": {"type": "string", "description": "m:ss where it is said or shown, '' if only in the caption"},
                    "where": {"type": "string", "enum": ["speech", "on_screen", "caption"]},
                    "type": {"type": "string", "enum": CLAIM_TYPES},
                    "stated_source": {"type": "string"},
                    "central": {"type": "boolean"},
                    "checkworthy": {"type": "integer", "description": "0 = not checkable or trivial, 3 = specific, checkable and central to the thesis"},
                },
                "required": ["quote", "claim", "timestamp", "where", "type", "stated_source", "central", "checkworthy"],
            },
        },
    },
    "required": ["title", "thesis", "claims"],
}


def _norm(s):
    s = (s or "").lower()
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"[^a-z0-9%$.]+", " ", s).strip()


def _secs(ts):
    try:
        p = [float(x) for x in str(ts).strip().split(":")]
        return p[0] * 3600 + p[1] * 60 + p[2] if len(p) == 3 else p[0] * 60 + p[1] if len(p) == 2 else None
    except ValueError:
        return None


def extract(packet):
    lines = [f"A {PLATFORM_NAME.get(packet['platform'], 'social')} video by {packet.get('creator') or 'an unknown creator'}"
             + (f", posted {packet['posted_at']}" if packet.get("posted_at") else "")
             + (f", {stamp(packet['duration'])} long." if packet.get("duration") else ".")]
    content = []
    frames = packet.get("frames") or []
    if frames:
        lines.append(f"You are given {len(frames)} still frames from the video, in order, each labeled with its timestamp.")
        content += llm.images_content(frames, stamp)
    else:
        lines.append("You cannot see the video, only its text.")
    lines.append("")
    lines.append("TRANSCRIPT\n" + (packet.get("transcript") or "(none available: the video may have no speech)"))
    lines.append("")
    lines.append("CAPTION\n" + (packet.get("caption") or "(none)"))
    lines.append("\nPull out the thesis and every factual claim.")
    content.append({"type": "text", "text": "\n".join(lines)})
    out = llm.structured(config.EXTRACT_MODEL, SYSTEM, content, "record_claims", SCHEMA, max_tokens=6000)
    return out


def triage(packet, extracted):
    """Return claims in report order with status: queued (to trace), opinion, or unchecked."""
    haystack = _norm(" ".join([packet.get("transcript", ""), packet.get("caption", "")]))
    claims = []
    for c in extracted.get("claims") or []:
        q = (c.get("quote") or "").strip()
        if not q:
            continue
        verified = _norm(q) in haystack if c.get("where") != "on_screen" else None
        claims.append({**c, "quote_verified": verified, "seconds": _secs(c.get("timestamp"))})
    if claims and not any(c.get("central") for c in claims):
        best = max(claims, key=lambda c: c.get("checkworthy", 0))
        best["central"] = True
    seen_central = False
    for c in claims:
        if c.get("central") and seen_central:
            c["central"] = False
        seen_central = seen_central or bool(c.get("central"))

    def score(c):
        return (1 if c.get("central") else 0, c.get("checkworthy", 0))
    checkable = [c for c in claims if c["type"] not in ("opinion", "prediction") and c.get("checkworthy", 0) > 0]
    checkable.sort(key=score, reverse=True)
    picked = checkable[: config.MAX_CLAIMS]
    for rank, c in enumerate(checkable, 1):
        c["rank"] = rank
        c["status"] = "queued" if c in picked else "unchecked"
    for c in claims:
        if c not in checkable:
            c["rank"] = 99
            c["status"] = "opinion" if c["type"] in ("opinion", "prediction") else "unchecked"
            if c["status"] == "opinion":
                c["central"] = c.get("central") and not checkable
    # If the central claim is an opinion, the first checkable claim becomes the one we lead with.
    if checkable and not any(c.get("central") and c["status"] == "queued" for c in claims):
        for c in claims:
            c["central"] = False
        checkable[0]["central"] = True
    return sorted(claims, key=lambda c: (c["status"] != "queued", c.get("rank", 99)))
