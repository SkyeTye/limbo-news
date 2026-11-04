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
out, _ = t.handle("record_hop", {"source_id": sid, "passage": "crime rose 40% in 2023", "kind": "secondary", "source_name": "Test Source", "compare": "x", "cites": "the study", "mutation": "none", "mutation_note": ""})
check(out.startswith("REJECTED"), "receipt check rejects a misquote")
out, _ = t.handle("record_hop", {"source_id": sid, "passage": "Crime rose 4.5%", "kind": "secondary", "source_name": "Test Source", "compare": "Reports 4.5%", "cites": "https://example.gov/report.pdf", "mutation": "number_changed", "mutation_note": "video says 40%"})
check(out.startswith("REJECTED") and "5 to 80" in out, "too-short passage rejected")
out, _ = t.handle("record_hop", {"source_id": sid, "passage": "News story text, edited later. Crime rose 4.5% in 2023.", "kind": "secondary", "source_name": "Test Source", "compare": "Reports 4.5%", "cites": "https://example.gov/report.pdf", "mutation": "number_changed", "mutation_note": "video says 40%"})
check("Recorded as hop 1" in out, "hop 1 recorded")
out, _ = t.handle("record_hop", {"source_id": sid, "passage": "News story text, edited later. Crime rose 4.5% in 2023.", "kind": "secondary", "source_name": "Test Source", "compare": "dup", "cites": "", "mutation": "none", "mutation_note": ""})
check("already hop 1" in out, "same source twice is flagged (circular check)")
out, done = t.handle("complete_trace", {"ending": "origin_found", "verdict": "stretched", "confidence": "high", "bottom_line": "b", "video_vs_origin": "", "mutations": [], "unknowns": []})
check(done is None and "primary" in out, "origin_found refused without a primary hop")
out, _ = t.handle("record_hop", {"source_id": sid2, "passage": "violent crime rose 4.5% in 2023, mostly in three states", "kind": "primary", "source_name": "Test Source", "compare": "4.5% in three states", "cites": "nothing", "mutation": "scope_widened", "mutation_note": "three states -> nationwide"})
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

print("central claim (v0.6: one claim per video)")
packet = {"transcript": "[0:05] crime is up 40 percent since 2020\n[0:09] and it's the mayor's fault", "caption": "wake up"}
c1 = extract.central_claim(packet, {"title": "t", "checkable": True, "quote": "crime is up 40 percent since 2020", "claim": "Crime rose 40% since 2020",
                                    "timestamp": "0:05", "where": "speech", "type": "statistic", "stated_source": ""})
check(c1 and c1["central"] and c1["status"] == "queued", "one central claim queued for tracing")
check(c1["quote_verified"] and c1["seconds"] == 5, "quote verified against transcript, timestamp parsed")
check(extract.central_claim(packet, {"title": "t", "checkable": False, "why_not": "Only opinions.", "quote": "", "claim": "", "timestamp": "", "where": "speech", "type": "other", "stated_source": ""}) is None,
      "no checkable claim -> nothing traced")
check(extract._pick_frames(list(range(16)), 10) == [0, 2, 3, 5, 7, 8, 10, 12, 13, 15] and len(extract._pick_frames([1, 2], 10)) == 2, "extraction sees at most 10 frames, spread evenly")

print("fixes from the first real check (2026-10-30)")
from backend.factcheck.tracer import coerce_args, Trace as _T
fn = _T.t_find_in_source
args, note = coerce_args(fn, "find_in_source", {"source_id": "x", "terms": 'tarbell-media-network-note", "Media network", "undisclosed", "known;", "Note"]'})
check(args["terms"] == ["tarbell-media-network-note", "Media network", "undisclosed", "known;", "Note"], "broken JSON term list repaired")
args, note = coerce_args(fn, "find_in_source", {"source_id": "x", "terms": ["Guardian.org"], 'Guardian Foundation", "factory farming': "grant"})
check("Guardian Foundation" in args["terms"] and "factory farming" in args["terms"] and "grant" in args["terms"], "terms spilled into keys recovered")
args, note = coerce_args(_T.t_complete_trace, "complete_trace", {"ending": "stopped", "verdict": "no_origin_found", "confidence": "low", "bottom_line": "b", "note": "extra"})
check("note" not in args and "note" in note, "unknown field ignored instead of failing")
clid2 = db.add_claim(cid, 1, quote="q", claim="Group X got $40 million", type="statistic", central=1, rank=1, status="queued")
art, _ = db.save_source("https://advocacy.example.org/tally", "Our analysis finds that donors gave Group X $40 million across forty grants, according to public grant databases we reviewed.", title="Tally")
t3 = tracer.Trace(check_row, db.get_claim(clid2), "", hop_limit=4, read_limit=6, model="test")
out, _ = t3.handle("record_hop", {"source_id": art, "passage": "donors gave Group X $40 million across forty grants", "kind": "primary", "source_type": "advocacy", "source_name": "Test Source", "compare": "s", "cites": "grant databases", "mutation": "none", "mutation_note": ""})
check("hop 1" in out, "article recorded with its source type")
done_args = {"ending": "origin_found", "verdict": "stretched", "confidence": "medium", "bottom_line": "b", "video_vs_origin": "", "mutations": [], "unknowns": []}
out, done = t3.handle("complete_trace", done_args)
check(done is None and out.startswith("NOT YET") and "records" in out, "stopping at an article with budget left is pushed back once")
out, done = t3.handle("complete_trace", {**done_args, "unknowns": ["grant pages unreachable"]})
check(done is not None, "second finish is accepted")
check(tracer.bottom_is_article(clid2), "chain bottoming at an article triggers the Opus retry")
check(not tracer.bottom_is_article(clid), "chain bottoming at a record does not")
# migration: a database made before source_type existed
import sqlite3
old = os.path.join(TMP, "old.db")
con = sqlite3.connect(old); con.execute("create table hops (id integer primary key autoincrement, claim_id text not null, position integer not null, source_id text not null, passage_id integer, kind text default '', says text default '', cites text default '', mutation text default 'none', mutation_note text default '', attempt integer default 1, dropped integer default 0, created_at text not null)"); con.commit(); con.close()
saved = config.DB_PATH; config.DB_PATH = old; db._ready = False; db.init()
con = sqlite3.connect(old); cols = {r[1] for r in con.execute("pragma table_info(hops)")}; con.close()
config.DB_PATH = saved; db._ready = False; db.init()
check("source_type" in cols, "old database gets the new column on startup")
menu = "\n".join(["Home", "About", "Funds", "Grants", "Research", "Careers", "Contact", "[Subscribe](https://x.org/s)"] * 40)
article = "The Guardian.org Foundation received a grant of $886,600 in 2020 to support journalism on factory farming. " * 3 + "\nGrant amount: $886,600\nDate: March 2020\n"
check(web.content_score(menu) < web.THIN <= web.content_score(article), "menu-only page detected as thin, real page is not")
html = '<html><body><div id="root">Loading</div><script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"grant":{"title":"theguardian.org - Journalism on Factory Farming","body":"<p>Open Philanthropy recommended a grant of $886,600 to support reporting on factory farming over two years.</p>","amount":"$886,600"}}},"buildId":"abc"}</script></body></html>'
from bs4 import BeautifulSoup
emb = web._embedded_text(BeautifulSoup(html, "html.parser"))
check("recommended a grant of $886,600" in emb, "content embedded as page data is recovered")
calls_seen = []
saved_j, saved_d = web._jina, web._direct
web._jina = lambda u, engine=None: (calls_seen.append(engine or "plain"), {"title": "Funds", "text": menu, "via": "jina"})[1] if engine is None else (calls_seen.append("browser"), {"title": "Grant", "text": article, "via": "jina-browser"})[1]
res = web.fetch("https://www.openphilanthropy.org/grants/x")
check(res["via"] == "jina-browser" and not res["thin"] and calls_seen == ["plain", "browser"], "thin page re-read with browser rendering, best copy kept")
web._jina, web._direct = saved_j, saved_d


print("v0.6 steps + archive/delete")
t5 = tracer.Trace(check_row, db.get_claim(clid2), "", hop_limit=6, read_limit=6, model="test")
long_passage = " ".join(["word"] * 85)
out, _ = t5.handle("record_hop", {"source_id": sid2, "source_name": "State University", "passage": long_passage, "kind": "primary", "source_type": "study_or_dataset", "compare": "c", "mutation": "none", "cites": ""})
check(out.startswith("REJECTED") and "80" in out, "passages over 80 words rejected (keep quotes short)")
out, _ = t5.handle("record_hop", {"source_id": sid2, "source_name": "State University", "passage": "violent crime rose 4.5% in 2023, mostly in three states", "kind": "primary", "source_type": "study_or_dataset", "compare": " ".join(["x"] * 40), "mutation": "none", "cites": ""})
check(out.startswith("REJECTED") and "25 words" in out, "compare must be one short sentence")
out, _ = t5.handle("complete_trace", {"ending": "dead_end", "verdict": "no_origin_found", "confidence": "low", "bottom_line": " ".join(["y"] * 45), "video_vs_origin": "", "mutations": [], "unknowns": []})
check(out.startswith("REJECTED") and "25 words" in out, "bottom line must be one short sentence")
prog = db.get_claim(clid2)["data"]["progress"]
check(prog["steps"] and prog["steps"][-1]["kind"] in ("found", "note", "read", "search", "library", "fail"), "live activity feed recorded for the phone")

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

r = cl.post(f"/api/check/{cid}/archive", json={"archived": True}); check(r.status_code == 200, "archive a check")
lst = cl.get("/api/checks").get_json(); check(all(x["id"] != cid for x in lst["checks"]) and lst["archived_count"] == 1, "archived check leaves the main list")
check(any(x["id"] == cid for x in cl.get("/api/checks?archived=1").get_json()["checks"]), "and shows in the archived list")
cl.post(f"/api/check/{cid}/archive", json={"archived": False}); check(any(x["id"] == cid for x in cl.get("/api/checks").get_json()["checks"]), "restore (undo) brings it back")
nsrc_before = db.library_stats()["sources"]
r = cl.delete(f"/api/check/{cid}"); check(r.status_code == 200 and db.get_check(cid) is None and not db.claims_for(cid), "delete removes the check and its chain")
check(db.library_stats()["sources"] == nsrc_before, "delete keeps the source library")
check(cl.get("/").status_code == 200, "public homepage untouched")

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nALL {ok} CHECKS PASSED")
