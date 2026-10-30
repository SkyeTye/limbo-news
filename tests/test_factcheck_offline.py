"""Offline tests for the fact-checker (no network, no API key).
Run: python3 tests/test_factcheck_offline.py"""
import json, os, shutil, subprocess, sys, tempfile
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="fc-test-")
os.environ["DATA_DIR"] = TMP
os.environ["FACTCHECK_PASSWORD"] = "test-password-123"
os.environ["BUDGET_OFF"] = "1"
os.environ.pop("ANTHROPIC_API_KEY", None)

from backend.factcheck import capture, config, db, extract, tracer, web, budget
ok = 0
def check(cond, msg):
    global ok
    assert cond, msg
    ok += 1
    print("  ok:", msg)

print("links")
check(capture.platform_of("https://www.tiktok.com/@a/video/1") == "TT", "tiktok platform")
check(capture.clean_url("https://www.instagram.com/reel/ABC/?igsh=xyz&utm_source=ig") == "https://www.instagram.com/reel/ABC", "strip tracking")
check(capture.clean_url("https://www.youtube.com/watch?v=abc&t=10s") == "https://www.youtube.com/watch?v=abc", "keep youtube v")
check(capture.first_url("look at this https://vm.tiktok.com/ZM123/ lol") == "https://vm.tiktok.com/ZM123/", "first url from share text")

print("transcripts + frames")
segs = capture._vtt_segments("WEBVTT\n\n00:00:01.000 --> 00:00:03.500\nCrime is up 40%\n\n00:01:05.000 --> 00:01:07.000\nsince <c>2020</c>\n")
check(len(segs) == 2 and segs[1]["start"] == 65.0 and segs[1]["text"] == "since 2020", "vtt parse with timestamps")
check(capture.transcript_text(segs).splitlines()[1].startswith("[1:05]"), "timestamped transcript lines")
times = capture.pick_times(120, [10.0, 10.2, 55.5, 90.0])
check(len(times) <= config.MAX_FRAMES and times[0] == 0.0 and any(abs(t - 55.65) < 0.01 for t in times), "frame picks: opening + cuts, capped")

# Generated test video: 3 scenes + a tone, so frames, cuts and audio all get exercised.
vid = os.path.join(TMP, "v.mp4")
ff = capture.ffmpeg_bin()
r = subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=320x568:d=3", "-f", "lavfi", "-i", "color=c=blue:s=320x568:d=3",
    "-f", "lavfi", "-i", "color=c=green:s=320x568:d=3", "-f", "lavfi", "-i", "sine=frequency=440:duration=9",
    "-filter_complex", "[0][1][2]concat=n=3:v=1:a=0[v]", "-map", "[v]", "-map", "3", "-shortest", "-pix_fmt", "yuv420p", vid], capture_output=True, text=True)
check(r.returncode == 0, "made a test video")
dur = capture.probe_duration(vid)
check(8.5 < dur < 9.5, f"duration probe ({dur:.1f}s)")
frames, ncuts = capture.extract_frames(vid, dur, os.path.join(TMP, "frames"))
check(ncuts >= 2 and len(frames) >= 5, f"frames from video ({len(frames)} frames, {ncuts} cuts)")
check(capture.extract_audio(vid, TMP) is not None, "audio extracted for transcription")

print("receipt check")
text = "The report found that [violent crime](https://example.gov/data) rose 4.5% in 2023, mostly in three states. Officials said more data is needed."
check(tracer.find_passage(text, "violent crime rose 4.5% in 2023, mostly in three states") > 0, "passage matches across a markdown link")
check(tracer.find_passage(text, "violent crime rose 40% in 2023") == -1, "changed number is rejected")
check(tracer.find_passage(text, "crime rose nationwide") == -1, "paraphrase is rejected")

print("library")
sid, _ = db.save_source("https://news.example.com/story?utm_source=x", "News story text. A new study by State University found crime rose 4.5% in 2023. [the study](https://example.gov/report.pdf)", title="Crime is up")
sid2, _ = db.save_source("https://example.gov/report.pdf", text, title="Annual Crime Report 2023", publisher="State University")
check(db.get_source_by_url("https://news.example.com/story")["id"] == sid, "url normalized for lookup")
_, changed = db.save_source("https://news.example.com/story", "News story text, edited later. Crime rose 4.5% in 2023.", title="Crime is up")
check(changed, "page edit detected by content hash")
hits = db.search_sources("crime 2023 State University report")
check(any(h["id"] == sid2 for h in hits), "full-text search finds the report")

print("tracer tools (no LLM)")
cid = db.create_check("https://www.tiktok.com/@x/video/1", "https://www.tiktok.com/@x/video/1", "TT")
db.update_check(cid, thesis="Crime is exploding", creator="@x")
clid = db.add_claim(cid, 0, quote="crime is up 40 percent", claim="Crime rose 40% in 2023", timestamp="0:05", seconds=5, type="statistic", central=1, rank=1, status="queued")
check_row, claim_row = db.get_check(cid), db.get_claim(clid)
t = tracer.Trace(check_row, claim_row, "", hop_limit=3, read_limit=1, model="test")
web.read_into_library = lambda url, max_age_days=30: (db.get_source_by_url(url), True) if db.get_source_by_url(url) else (_ for _ in ()).throw(RuntimeError("HTTP 404"))
out, _ = t.handle("read_source", {"url": "https://news.example.com/story"})
check("source_id: " + sid in out and "from our library" in out, "read_source serves the library copy")
out, _ = t.handle("record_hop", {"source_id": sid, "passage": "crime rose 40% in 2023", "kind": "secondary", "says": "x", "cites": "the study", "mutation": "none", "mutation_note": ""})
check(out.startswith("REJECTED"), "receipt check rejects a misquote")
out, _ = t.handle("record_hop", {"source_id": sid, "passage": "Crime rose 4.5%", "kind": "secondary", "says": "Reports 4.5%", "cites": "https://example.gov/report.pdf", "mutation": "number_changed", "mutation_note": "video says 40%"})
check(out.startswith("REJECTED") and "5 to 120" in out, "too-short passage rejected")
out, _ = t.handle("record_hop", {"source_id": sid, "passage": "News story text, edited later. Crime rose 4.5% in 2023.", "kind": "secondary", "says": "Reports 4.5%", "cites": "https://example.gov/report.pdf", "mutation": "number_changed", "mutation_note": "video says 40%"})
check("Recorded as hop 1" in out, "hop 1 recorded")
out, _ = t.handle("record_hop", {"source_id": sid, "passage": "News story text, edited later. Crime rose 4.5% in 2023.", "kind": "secondary", "says": "dup", "cites": "", "mutation": "none", "mutation_note": ""})
check("already hop 1" in out, "same source twice is flagged (circular check)")
out, done = t.handle("complete_trace", {"ending": "origin_found", "verdict": "stretched", "confidence": "high", "bottom_line": "b", "video_vs_origin": "", "mutations": [], "unknowns": []})
check(done is None and "primary" in out, "origin_found refused without a primary hop")
out, _ = t.handle("record_hop", {"source_id": sid2, "passage": "violent crime rose 4.5% in 2023, mostly in three states", "kind": "primary", "says": "4.5% in three states", "cites": "nothing", "mutation": "scope_widened", "mutation_note": "three states -> nationwide"})
check("hop 2" in out, "primary origin recorded as hop 2")
out, done = t.handle("complete_trace", {"ending": "origin_found", "verdict": "stretched", "confidence": "high", "bottom_line": "The real figure is 4.5% in three states.", "video_vs_origin": "40% vs 4.5%", "mutations": [{"type": "number_changed", "note": "4.5 -> 40"}], "unknowns": []})
check(done and done["verdict"] == "stretched", "trace completes with a verdict")
chain = db.chain_for(clid)
check([h["kind"] for h in chain] == ["secondary", "primary"] and chain[1]["passage"].startswith("violent"), "chain ordered video -> origin with passages")
from backend.factcheck import db as _db
with _db.conn() as c:
    check(c.execute("select count(*) from citations where from_source=? and to_source=?", (sid, sid2)).fetchone()[0] == 1, "who-cites-whom edge saved")
t2 = tracer.Trace(check_row, claim_row, "", hop_limit=3, read_limit=0, model="test")
web.read_into_library = lambda url, max_age_days=30: (_ for _ in ()).throw(AssertionError("should not fetch"))
out, _ = t2.handle("read_source", {"url": "https://brand-new.example.org/page"})
check(out.startswith("BUDGET"), "read budget enforced")

print("triage")
packet = {"transcript": "[0:05] crime is up 40 percent since 2020\n[0:09] and it's the mayor's fault", "caption": "wake up"}
ex = {"thesis": "t", "claims": [
    {"quote": "it's the mayor's fault", "claim": "The mayor caused it", "timestamp": "0:09", "where": "speech", "type": "opinion", "stated_source": "", "central": True, "checkworthy": 1},
    {"quote": "crime is up 40 percent since 2020", "claim": "Crime rose 40% since 2020", "timestamp": "0:05", "where": "speech", "type": "statistic", "stated_source": "", "central": False, "checkworthy": 3},
    {"quote": "x1 a", "claim": "c2", "timestamp": "", "where": "caption", "type": "event", "stated_source": "", "central": False, "checkworthy": 2},
    {"quote": "x2 b", "claim": "c3", "timestamp": "", "where": "caption", "type": "event", "stated_source": "", "central": False, "checkworthy": 2},
    {"quote": "x3 c", "claim": "c4", "timestamp": "", "where": "caption", "type": "event", "stated_source": "", "central": False, "checkworthy": 1}]}
cl = extract.triage(packet, ex)
queued = [c for c in cl if c["status"] == "queued"]
check(len(queued) == 3, "max 3 claims traced")
check(queued[0]["claim"] == "Crime rose 40% since 2020" and queued[0]["central"], "central claim is the first checkable one when the central is an opinion")
check(queued[0]["quote_verified"] and queued[0]["seconds"] == 5, "quote verified against transcript, timestamp parsed")
check(any(c["status"] == "opinion" for c in cl) and any(c["status"] == "unchecked" for c in cl), "opinion labeled, extra claim left unchecked")

print("web helpers")
from bs4 import BeautifulSoup
html = '<html><body><article><p>We rate this False. <a href="https://www.politifact.com/other">more</a> The data comes from <a href="https://bjs.ojp.gov/report.pdf">the BJS report</a> and <a href="https://twitter.com/share">share</a>.</p></article></body></html>'
class R: text = html
web.requests.get = lambda *a, **k: R()
links = web._outbound_links("https://www.politifact.com/factchecks/2024/x/")
check([l["url"] for l in links] == ["https://bjs.ojp.gov/report.pdf"], "fact-check leads keep only outside sources, no verdict text")

print("routes + security")
import importlib
from backend import server
app = server.app
app.config["TESTING"] = True
from backend.factcheck import pipeline, routes
submitted = []
pipeline.submit = lambda job: submitted.append(job)
cl = app.test_client()
r = cl.get("/api/checks"); check(r.status_code == 401, "API needs sign-in")
r = cl.get("/check"); check(r.status_code == 200 and b"Fact Check" in r.data and r.headers.get("X-Robots-Tag", "").startswith("noindex"), "/check page served with noindex")
check(b"Disallow: /check" in cl.get("/robots.txt").data, "robots.txt hides /check")
r = cl.post("/api/check", json={"url": "https://www.tiktok.com/@a/video/9"}); check(r.status_code == 401, "creating a check needs sign-in or token")
r = cl.post("/api/check/login", json={"password": "nope"}); check(r.status_code == 401, "wrong password refused")
tok = routes.ingest_token()
r = cl.post("/api/check", json={"url": "share text https://www.tiktok.com/@a/video/9?is_from_webapp=1", "via": "share"}, headers={"Authorization": "Bearer " + tok})
j = r.get_json(); check(r.status_code == 200 and j["open_url"].endswith("/check/" + j["id"]) and submitted, "Shortcut token creates a check and returns open_url")
r = cl.post("/api/check", json={"url": "https://www.tiktok.com/@a/video/9"}, headers={"Authorization": "Bearer " + tok})
check(r.get_json().get("duplicate"), "same video shared twice opens the existing check")
r = cl.post("/api/check/login", json={"password": "test-password-123"}); check(r.status_code == 200, "right password signs in")
r = cl.get(f"/api/check/{cid}"); d = r.get_json()
check(d["claims"][0]["chain"][1]["kind"] == "primary" and d["claims"][0]["max_depth"] == 2, "check API returns the chain")
r = cl.post(f"/api/check/{cid}/events", json={"action": "open_saved", "claim_id": clid, "depth": 2, "max_depth": 2, "source_id": sid2}); check(r.status_code == 200, "rabbit-hole event logged")
s = cl.get("/api/check/settings").get_json()
check(s["rabbit_hole"]["reached_origin"] == 1 and s["token"] == "Bearer " + tok and s["library"]["sources"] >= 2, "settings: token, library and rabbit-hole stats")
src = cl.get(f"/api/check/sources/{sid2}").get_json(); check(src["passages"] and "text" in src, "saved source with passages for highlighting")
r = cl.get("/api/check/x/frames/../../etc.jpg"); check(r.status_code == 404, "frame path traversal blocked")
check(cl.get("/").status_code == 200, "public homepage untouched")

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nALL {ok} CHECKS PASSED")
