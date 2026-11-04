"""Stage 3 · Find the central claim (and Stage 4 · Triage, now trivial).

Since v0.6 the tool checks ONE claim per video: the single, central, checkable
claim the video rests on. In testing, the "top 3" claims were nearly always the
same claim from different lines, with the same root sources. The transcript is
shown to the viewer as-is, line by line, so no other claims are written up."""
import re

from . import config, llm
from .capture import PLATFORM_NAME, stamp

CLAIM_TYPES = ["statistic", "quote", "event", "study", "causal", "clip", "comparison", "other"]

SYSTEM = """You are the first step of a fact-checking tool. You read a short social video (still frames, transcript, caption) and find the ONE central, checkable claim it rests on. You do not judge whether it is true.

Rules:
- Use only what the frames, transcript and caption show. Never guess. Never add facts.
- Pick the single factual claim the video's message depends on most, that can be checked against sources (a number, an event, a quote, a study, a funding link, a clip's origin). Not an opinion, a value judgment, a joke or a prediction.
- If several lines say versions of the same thing, it is one claim: pick the most specific version (the one with the number, the name, the source).
- "quote" is the creator's exact words from the transcript, from on-screen text visible in a frame, or from the caption. Do not paraphrase in "quote".
- "claim" is a plain, specific, checkable restatement of the quote, 25 words or fewer, with names, numbers, places and dates filled in from context.
- "stated_source" is what the creator says it comes from ("a Harvard study", "Effort News"), or "" if they name nothing.
- If the video makes no checkable factual claim at all, set checkable to false and say why in why_not (one short sentence).
- Write plainly. No em dashes."""

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Concrete label for the video, max 8 words"},
        "checkable": {"type": "boolean"},
        "why_not": {"type": "string", "description": "Only when checkable is false"},
        "quote": {"type": "string"},
        "claim": {"type": "string"},
        "timestamp": {"type": "string", "description": "m:ss where it is said or shown, '' if only in the caption"},
        "where": {"type": "string", "enum": ["speech", "on_screen", "caption"]},
        "type": {"type": "string", "enum": CLAIM_TYPES},
        "stated_source": {"type": "string"},
    },
    "required": ["title", "checkable", "quote", "claim", "timestamp", "where", "type", "stated_source"],
}

EXTRACT_FRAMES = 10  # fewer images = faster answer; on-screen text still gets read


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


def _pick_frames(frames, n):
    if len(frames) <= n:
        return frames
    return [frames[round(i * (len(frames) - 1) / (n - 1))] for i in range(n)]


def extract(packet):
    lines = [f"A {PLATFORM_NAME.get(packet['platform'], 'social')} video by {packet.get('creator') or 'an unknown creator'}"
             + (f", posted {packet['posted_at']}" if packet.get("posted_at") else "")
             + (f", {stamp(packet['duration'])} long." if packet.get("duration") else ".")]
    content = []
    frames = _pick_frames(packet.get("frames") or [], EXTRACT_FRAMES)
    if frames:
        lines.append(f"You are given {len(frames)} still frames from the video, in order, each labeled with its timestamp.")
        content += llm.images_content(frames, stamp)
    else:
        lines.append("You cannot see the video, only its text.")
    lines += ["", "TRANSCRIPT\n" + (packet.get("transcript") or "(none available: the video may have no speech)"),
              "", "CAPTION\n" + (packet.get("caption") or "(none)"), "\nFind the one central, checkable claim."]
    content.append({"type": "text", "text": "\n".join(lines)})
    return llm.structured(config.EXTRACT_MODEL, SYSTEM, content, "record_central_claim", SCHEMA, max_tokens=1500)


def central_claim(packet, extracted):
    """Return the claim dict to trace, or None when the video makes no checkable claim."""
    q = (extracted.get("quote") or "").strip()
    if not extracted.get("checkable", True) or not q or not (extracted.get("claim") or "").strip():
        return None
    haystack = _norm(" ".join([packet.get("transcript", ""), packet.get("caption", "")]))
    verified = _norm(q) in haystack if extracted.get("where") != "on_screen" else None
    return {
        "quote": q, "claim": extracted["claim"].strip(), "timestamp": extracted.get("timestamp", ""),
        "seconds": _secs(extracted.get("timestamp")), "where": extracted.get("where", "speech"),
        "type": extracted.get("type", "other"), "stated_source": extracted.get("stated_source", ""),
        "quote_verified": verified, "central": True, "rank": 1, "status": "queued",
    }
