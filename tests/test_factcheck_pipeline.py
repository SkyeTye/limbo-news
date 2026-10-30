"""End-to-end pipeline test with a scripted stand-in for Claude and a fake web.
Uses the real Anthropic SDK message types, the real agent loop, tools, receipt
checks, database and API. No network, no API key.

Run: python3 tests/test_factcheck_pipeline.py   (add --serve to keep the app running on :5077 with the test data)
"""
import itertools, json, os, re, subprocess, sys, tempfile
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="fc-pipe-")
os.environ.update(DATA_DIR=TMP, FACTCHECK_PASSWORD="test-password-123", BUDGET_OFF="1", ANTHROPIC_API_KEY="test")

from anthropic.types import Message, ToolUseBlock, Usage
from backend.factcheck import capture, config, context, db, llm, pipeline, tracer, web

# ---------- fake web ----------
PAGES = {
    "https://dailyviral.example.com/teens-phones": ("Teens are hooked", "Daily Viral",
        "Shocking new numbers. A Stanford study shows 70% of teens are addicted to their phones, [according to researchers](https://news.example.org/stanford-phone-study). Experts are alarmed."),
    "https://news.example.org/stanford-phone-study": ("Study looks at teen phone habits", "Example News",
        "PALO ALTO. Stanford researchers surveyed 1,200 teens and found that 31% met criteria for problematic phone use, a measure the authors call a warning sign rather than a diagnosis. The full paper is [here](https://stanford.example.edu/paper.pdf). Lead author Dr. Ana Ruiz said the results do not show addiction."),
    "https://stanford.example.edu/paper.pdf": ("Problematic smartphone use in US adolescents", "Stanford University",
        "Abstract. In a sample of 1,200 US adolescents surveyed in 2023, 31% met criteria for problematic smartphone use, and 70% reported checking their phone within five minutes of waking. Problematic use is not a clinical diagnosis of addiction."),
    "https://critic.example.com/op-ed": ("Stop calling it addiction", "Example Review",
        "Psychologist Dr. Lee Park wrote that calling everyday phone habits an addiction confuses a common habit with a disorder. Others disagree."),
}
def fake_read(url, max_age_days=30):
    existing = db.get_source_by_url(url)
    if existing and existing.get("full_text"):
        return existing, True
    if url not in PAGES:
        raise RuntimeError("HTTP 404")
    title, pub, text = PAGES[url]
    sid, _ = db.save_source(url, text, title=title, publisher=pub, fetched_via="fake")
    return db.get_source(sid), False
web.read_into_library = fake_read
web.wayback_lookup = lambda url: None
web.wayback_save = lambda url: "https://web.archive.org/web/2026/" + url
web.factcheck_leads = lambda q: ([{"url": "https://www.politifact.com/x", "publisher": "PolitiFact", "title": "Mostly False: 70% of teens addicted", "rating": "Mostly False"}],
                                 [{"url": "https://stanford.example.edu/paper.pdf", "link_text": "the study"}])
def fake_search(q, n=5):
    return [{"title": t, "url": u, "snippet": x[:150]} for u, (t, p, x) in PAGES.items() if any(w in x.lower() for w in q.lower().split())][:n]
tracer.web_search = fake_search

# ---------- fake capture (real frames from a generated video) ----------
vid = os.path.join(TMP, "v.mp4")
subprocess.run([capture.ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=360x640:d=10", "-pix_fmt", "yuv420p", vid], check=True)
def fake_capture(url, frames_dir, progress=lambda s: None):
    progress("Watching the video")
    frames, _ = capture.extract_frames(vid, 10, frames_dir)
    return {"found": True, "platform": "TT", "creator": "@phonefacts", "title": "", "caption": "Wake up parents #screentime",
            "posted_at": "2026-10-20", "stats": {"views": 120000}, "comments": [], "duration": 10.0, "segments": [],
            "transcript": "[0:02] A Stanford study shows 70% of teens are addicted to their phones.\n[0:06] Parents need to wake up.\n[0:08] Screen time has doubled since 2019.",
            "transcript_source": "platform", "frames": frames, "warnings": []}
pipeline.capture.capture = fake_capture

# ---------- scripted Claude ----------
ids = itertools.count(1)
calls = {"n": 0, "max_breakpoints": 0, "rejections_seen": 0}

def msg(model, name, inp):
    return Message(id=f"msg_{next(ids)}", type="message", role="assistant", model=model,
                   content=[ToolUseBlock(id=f"tu_{next(ids)}", type="tool_use", name=name, input=inp)],
                   stop_reason="tool_use", stop_sequence=None, usage=Usage(input_tokens=2000, output_tokens=300))

def sid_of(url):
    s = db.get_source_by_url(url)
    return s["id"] if s else "missing"

def fake_create(**kw):
    calls["n"] += 1
    model = kw["model"]
    bp = sum(1 for m in kw["messages"] if isinstance(m["content"], list) for b in m["content"] if isinstance(b, dict) and "cache_control" in b)
    calls["max_breakpoints"] = max(calls["max_breakpoints"], bp)
    if any(t["name"] == "record_claims" for t in kw.get("tools", [])):
        assert kw.get("tool_choice", {}).get("type") != "tool", "forced tool_choice is rejected by current models"
        imgs = sum(1 for b in kw["messages"][0]["content"] if b.get("type") == "image")
        assert imgs >= 3, "frames were sent to Claude"
        return msg(model, "record_claims", {"title": "Stanford study on teen phone addiction", "thesis": "Teens are addicted to phones and parents must act.", "claims": [
            {"quote": "A Stanford study shows 70% of teens are addicted to their phones.", "claim": "A Stanford study found 70% of teens are addicted to their phones", "timestamp": "0:02", "where": "speech", "type": "study", "stated_source": "a Stanford study", "central": True, "checkworthy": 3},
            {"quote": "Parents need to wake up.", "claim": "Parents need to act", "timestamp": "0:06", "where": "speech", "type": "opinion", "stated_source": "", "central": False, "checkworthy": 0},
            {"quote": "Screen time has doubled since 2019.", "claim": "Teen screen time doubled between 2019 and 2026", "timestamp": "0:08", "where": "speech", "type": "statistic", "stated_source": "", "central": False, "checkworthy": 2}]})
    system = kw["system"][0]["text"]
    user0 = kw["messages"][0]["content"][0]["text"]
    turn = sum(1 for m in kw["messages"] if m["role"] == "assistant")
    last = kw["messages"][-1]["content"]
    if any(isinstance(b, dict) and "REJECTED" in str(b.get("content", "")) for b in last):
        calls["rejections_seen"] += 1
    if system.startswith("You trace one claim"):
        if "Screen time" in user0.split("CLAIM TO TRACE:")[1][:200]:
            steps = [("web_search", {"query": "teen screen time doubled since 2019"}),
                     ("complete_trace", {"ending": "dead_end", "verdict": "no_origin_found", "confidence": "low", "bottom_line": "We could not find where this number comes from.", "video_vs_origin": "", "mutations": [], "unknowns": ["Any source for 'doubled'."]})]
        elif "A FIRST TRACER" in user0:  # the Opus retry continues the chain
            steps = [("read_source", {"url": "https://news.example.org/stanford-phone-study"}),
                     ("record_hop", {"source_id": sid_of("https://news.example.org/stanford-phone-study"), "passage": "Stanford researchers surveyed 1,200 teens and found that 31% met criteria for problematic phone use", "kind": "secondary", "says": "Reports 31% problematic use", "cites": "https://stanford.example.edu/paper.pdf", "mutation": "number_changed", "mutation_note": "Video says 70% addicted; this says 31% problematic use."}),
                     ("read_source", {"url": "https://stanford.example.edu/paper.pdf"}),
                     ("record_hop", {"source_id": sid_of("https://stanford.example.edu/paper.pdf"), "passage": "31% met criteria for problematic smartphone use, and 70% reported checking their phone within five minutes of waking", "kind": "primary", "says": "31% problematic use; 70% check phone soon after waking", "cites": "nothing", "mutation": "other", "mutation_note": "The 70% is about checking phones after waking, not addiction."}),
                     ("complete_trace", {"ending": "origin_found", "verdict": "stretched", "confidence": "high", "bottom_line": "The study is real, but it found 31% had problematic use. The 70% figure is about checking phones after waking, not addiction.", "video_vs_origin": "Video: 70% addicted. Study: 31% problematic use (not a diagnosis); 70% check their phone within five minutes of waking.", "mutations": [{"type": "number_changed", "note": "31% became 70%"}], "unknowns": []})]
        else:  # first Sonnet pass: one hop, one deliberate misquote, then stops
            steps = [("search_library", {"query": "Stanford 70% teens addicted phones"}),
                     ("factcheck_leads", {"query": "70% teens addicted phones Stanford"}),
                     ("web_search", {"query": "Stanford study 70% teens addicted phones"}),
                     ("read_source", {"url": "https://dailyviral.example.com/teens-phones"}),
                     ("record_hop", {"source_id": sid_of("https://dailyviral.example.com/teens-phones"), "passage": "Stanford proved 70 percent of teens are addicted", "kind": "secondary", "says": "x", "cites": "y", "mutation": "none", "mutation_note": ""}),
                     ("record_hop", {"source_id": sid_of("https://dailyviral.example.com/teens-phones"), "passage": "A Stanford study shows 70% of teens are addicted to their phones", "kind": "secondary", "says": "Repeats the 70% claim", "cites": "https://news.example.org/stanford-phone-study", "mutation": "none", "mutation_note": ""}),
                     ("complete_trace", {"ending": "stopped", "verdict": "no_origin_found", "confidence": "low", "bottom_line": "Ran out of budget.", "video_vs_origin": "", "mutations": [], "unknowns": []})]
        name, inp = steps[min(turn, len(steps) - 1)]
        return msg(model, name, inp)
    if system.startswith("You add context"):
        central = next(c for c in db.claims_for(cid) if c["central"])
        steps = [("read_source", {"url": "https://critic.example.com/op-ed"}),
                 ("record_voice", {"claim_id": central["id"], "name": "Dr. Lee Park", "affiliation": "psychologist", "stance": "disputes", "quote": "calling everyday phone habits an addiction confuses a common habit with a disorder", "source_id": sid_of("https://critic.example.com/op-ed")}),
                 ("record_voice", {"claim_id": central["id"], "name": "Dr. Ana Ruiz", "affiliation": "Stanford, lead author", "stance": "qualifies", "quote": "Lead author Dr. Ana Ruiz said the results do not show addiction.", "source_id": sid_of("https://news.example.org/stanford-phone-study")}),
                 ("record_second_opinion", {"claim_id": central["id"], "publisher": "PolitiFact", "url": "https://www.politifact.com/x", "rating": "Mostly False", "agrees": "yes", "note": ""}),
                 ("complete_context", {"left_out": ["The study measured 'problematic use', which its authors say is not a diagnosis of addiction.", "The 70% figure is about checking phones after waking."], "bottom_line": "The video turns a real study into a bigger claim than the study makes."})]
        name, inp = steps[min(turn, len(steps) - 1)]
        return msg(model, name, inp)
    raise AssertionError("unexpected call: " + system[:60])

llm.client = lambda: type("C", (), {"messages": type("M", (), {"create": staticmethod(fake_create)})()})()

# ---------- run ----------
cid = db.create_check("https://www.tiktok.com/@phonefacts/video/123", "https://www.tiktok.com/@phonefacts/video/123", "TT")
pipeline.run_check(cid)
pipeline._archive_q.queue.clear() if hasattr(pipeline._archive_q, "queue") else None

check = db.get_check(cid)
claims = db.claims_for(cid)
central = next(c for c in claims if c["central"])
chain = db.chain_for(central["id"])
ok = 0
def expect(cond, m):
    global ok
    assert cond, m
    ok += 1
    print("  ok:", m)

print("pipeline")
expect(check["status"] in ("done", "partial"), f"check finished ({check['status']}, error={check['error']!r})")
expect(check["thesis"].startswith("Teens are addicted"), "thesis saved")
expect(len(check["data"]["frames"]) >= 5, "frames saved for the report")
expect([c["status"] for c in claims].count("opinion") == 1, "opinion labeled, not traced")
expect(central["verdict"] == "stretched" and central["ending"] == "origin_found" and central["confidence"] == "high", "central claim: Opus retry reached the origin")
expect([a["model"] for a in central["data"]["attempts"]] == [config.TRACE_MODEL, config.RETRY_MODEL], "first Sonnet pass, then one Opus retry")
expect([h["kind"] for h in chain] == ["secondary", "secondary", "primary"], "chain: viral post -> news story -> study")
expect(chain[1]["mutation"] == "number_changed", "mutation recorded on the hop where it happened")
expect(calls["rejections_seen"] >= 1, "misquoted passage was rejected and the agent saw the rejection")
expect(central["data"]["factchecks"][0]["rating"] == "Mostly False", "fact-check rating stored for later, not shown to the tracer")
second = next(c for c in claims if c["claim"].startswith("Teen screen time"))
expect(second["ending"] == "dead_end" and second["verdict"] == "no_origin_found" and len(second["data"]["attempts"]) == 1, "secondary claim: no Opus retry")
voices = db.voices_for(cid)
expect(len(voices) == 2 and {v["stance"] for v in voices} == {"disputes", "qualifies"}, "named voices with receipt-checked quotes")
expect(db.second_opinions_for(cid)[0]["agrees"] == "yes", "second opinion recorded after the verdict")
expect(len(check["data"]["left_out"]) == 2, "what the video leaves out")
expect(calls["max_breakpoints"] <= 1, "one rolling cache breakpoint in messages")
expect(len(db.audit_for(cid)) >= 15, f"research log has every step ({len(db.audit_for(cid))} entries)")
with db.conn() as c:
    expect(c.execute("select count(*) from citations").fetchone()[0] >= 2, "who-cites-whom edges")
print(f"\nALL {ok} PIPELINE CHECKS PASSED · {calls['n']} scripted Claude calls")

if "--serve" in sys.argv:
    from backend import server
    print("serving on http://127.0.0.1:5077  check id:", cid)
    server.app.run(host="127.0.0.1", port=5077, debug=False)
