"""Stage 2 · Capture. Ported from Content Files (lib/scrape.js, lib/video.js,
lib/transcribe.js) and limbo-video/analyze.py.

Link in -> one evidence packet: caption, creator, post date, stats, top comments,
a timestamped transcript, and still frames taken at every cut plus even spacing.

Post lookup goes through ScrapeCreators when SCRAPECREATORS_API_KEY is set.
Without it (or if it fails) yt-dlp is tried, which works for most TikTok and
YouTube links and for Instagram when a cookies file is available.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs, urlencode

import requests

from . import budget, config

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"
MAX_BYTES = 150 * 1024 * 1024
SC_BASE = "https://api.scrapecreators.com"


# ---------- links ----------

def platform_of(url):
    u = (url or "").lower()
    if "tiktok." in u:
        return "TT"
    if "instagram." in u:
        return "IG"
    if "youtube." in u or "youtu.be" in u:
        return "YT"
    if re.search(r"//(www\.|mobile\.)?(x|twitter)\.com/", u):
        return "X"
    return "WEB"


PLATFORM_NAME = {"TT": "TikTok", "IG": "Instagram", "YT": "YouTube", "X": "X", "WEB": "the web"}


def first_url(text):
    m = re.search(r"https?://[^\s\"'<>]+", text or "")
    return m.group(0) if m else ""


def clean_url(url):
    """Strip tracking parameters so the same video shared twice is recognised."""
    try:
        u = urlparse((url or "").strip())
        if u.scheme not in ("http", "https") or not u.netloc:
            return ""
        keep = {}
        if platform_of(url) == "YT":
            v = parse_qs(u.query).get("v")
            if v:
                keep["v"] = v[0]
        return u._replace(query=urlencode(keep), fragment="").geturl().rstrip("/")
    except Exception:
        return ""


def stamp(sec):
    sec = int(round(sec or 0))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _iso(ts):
    try:
        if ts in (None, ""):
            return ""
        if isinstance(ts, (int, float)) or str(ts).isdigit():
            return datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()
        s = str(ts)
        if re.fullmatch(r"\d{8}", s):
            return f"{s[:4]}-{s[4:6]}-{s[6:]}"
        for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt).date().isoformat()
            except ValueError:
                pass
        return s[:20]
    except Exception:
        return ""


# ---------- ScrapeCreators ----------

def _sc(path, params, timeout=60):
    budget.ensure_lookup()
    budget.record(lookups=1)  # counted up front, so even a failed request counts
    r = requests.get(SC_BASE + path, params={k: v for k, v in params.items() if v not in (None, "")},
                     headers={"x-api-key": config.scrape_key()}, timeout=timeout)
    try:
        j = r.json()
    except ValueError:
        j = None
    if not r.ok or (isinstance(j, dict) and j.get("success") is False):
        msg = (j or {}).get("message") or (j or {}).get("error") or r.text[:160]
        raise RuntimeError(f"{path} -> {r.status_code} {msg}")
    return j or {}


def _settle(tasks):
    from concurrent.futures import ThreadPoolExecutor
    out, errors = {}, {}
    with ThreadPoolExecutor(max_workers=len(tasks)) as ex:
        futs = {name: ex.submit(fn) for name, fn in tasks.items()}
        for name, f in futs.items():
            try:
                out[name] = f.result()
            except Exception as e:
                errors[name] = str(e)
    return out, errors


def _first(*vals):
    for v in vals:
        if v not in (None, "", [], {}):
            return v
    return None


def _url_of(v):
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return _first((v.get("url_list") or [None])[0], v.get("url"), v.get("src"))
    return None


def _vtt_segments(vtt):
    """WebVTT -> [{start, end, text}]."""
    if not vtt or not isinstance(vtt, str):
        return []
    if "-->" not in vtt:
        return [{"start": None, "end": None, "text": vtt.strip()}]
    segs, cur = [], None
    def secs(t):
        p = t.strip().replace(",", ".").split(":")
        p = [float(x) for x in p]
        return p[0] * 3600 + p[1] * 60 + p[2] if len(p) == 3 else p[0] * 60 + p[1]
    for line in vtt.splitlines():
        t = line.strip()
        if "-->" in t:
            a, b = t.split("-->")[:2]
            try:
                cur = {"start": secs(a), "end": secs(b.split()[0]), "text": ""}
                segs.append(cur)
            except ValueError:
                cur = None
        elif t and cur is not None and t != "WEBVTT" and not t.isdigit():
            clean = re.sub(r"<[^>]+>", "", t)
            cur["text"] = (cur["text"] + " " + clean).strip()
    return [s for s in segs if s["text"]]


def _instagram(url):
    out, errors = _settle({
        "post": lambda: _sc("/v1/instagram/post", {"url": url}),
        "comments": lambda: _sc("/v2/instagram/post/comments", {"url": url}),
        "transcript": lambda: _sc("/v2/instagram/media/transcript", {"url": url}, 75),
    })
    p = out.get("post") or {}
    m = (p.get("data") or {}).get("xdt_shortcode_media") or (p.get("data") or {}).get("shortcode_media") or p.get("xdt_shortcode_media") or {}
    comments = [{"user": (c.get("user") or {}).get("username") or (c.get("owner") or {}).get("username") or "", "text": c.get("text") or ""}
                for c in (out.get("comments") or {}).get("comments") or []]
    cap_edges = ((m.get("edge_media_to_caption") or {}).get("edges") or [{}])
    text = " ".join(t.get("text", "") for t in (out.get("transcript") or {}).get("transcripts") or [] if t)
    return {
        "handle": "@" + m["owner"]["username"] if (m.get("owner") or {}).get("username") else "",
        "caption": _first((cap_edges[0].get("node") or {}).get("text") if cap_edges else None, (m.get("caption") or {}).get("text") if isinstance(m.get("caption"), dict) else None, "") or "",
        "cover_url": _first(m.get("thumbnail_src"), m.get("display_url")),
        "video_url": m.get("video_url") or "",
        "duration": float(m.get("video_duration") or 0),
        "posted_at": _iso(m.get("taken_at_timestamp")),
        "stats": {"views": m.get("video_view_count") or m.get("video_play_count"), "likes": (m.get("edge_media_preview_like") or {}).get("count")},
        "segments": [{"start": None, "end": None, "text": text.strip()}] if text.strip() else [],
        "comments": comments, "errors": errors,
        "found": bool(out.get("post")) and bool(m.get("id") or m.get("shortcode") or m.get("video_url")),
    }


def _tiktok(url):
    out, errors = _settle({
        "post": lambda: _sc("/v2/tiktok/video", {"url": url}),
        "comments": lambda: _sc("/v1/tiktok/video/comments", {"url": url}),
        "transcript": lambda: _sc("/v1/tiktok/video/transcript", {"url": url}),
    })
    p = out.get("post") or {}
    a = p.get("aweme_detail") or (p.get("data") or {}).get("aweme_detail") or p
    v = a.get("video") or {}
    dur = float(_first(v.get("duration"), a.get("duration")) or 0)
    st = a.get("statistics") or {}
    return {
        "handle": "@" + a["author"]["unique_id"] if (a.get("author") or {}).get("unique_id") else "",
        "caption": a.get("desc") or "",
        "cover_url": _first(_url_of(v.get("cover_large")), _url_of(v.get("origin_cover")), _url_of(v.get("cover"))),
        "video_url": _first(_url_of(v.get("download_no_watermark_addr")), _url_of(v.get("play_addr")), _url_of(v.get("play_addr_h264")), _url_of(v.get("download_addr"))) or "",
        "duration": dur / 1000 if dur > 1000 else dur,
        "posted_at": _iso(a.get("create_time")),
        "stats": {"views": st.get("play_count"), "likes": st.get("digg_count"), "shares": st.get("share_count")},
        "segments": _vtt_segments((out.get("transcript") or {}).get("transcript")),
        "comments": [{"user": (c.get("user") or {}).get("unique_id") or "", "text": c.get("text") or ""} for c in (out.get("comments") or {}).get("comments") or []],
        "errors": errors,
        "found": bool(out.get("post")) and bool(a.get("aweme_id") or v.get("play_addr")),
    }


def _youtube(url):
    out, errors = _settle({
        "post": lambda: _sc("/v1/youtube/video", {"url": url}),
        "comments": lambda: _sc("/v1/youtube/video/comments", {"url": url, "order": "top"}),
        "transcript": lambda: _sc("/v1/youtube/video/transcript", {"url": url}),
    })
    p = out.get("post") or {}
    tr = out.get("transcript") or {}
    segs = []
    if isinstance(tr.get("transcript"), list):
        for s in tr["transcript"]:
            start = s.get("startMs")
            segs.append({"start": float(start) / 1000 if start not in (None, "") else None,
                         "end": float(s["endMs"]) / 1000 if s.get("endMs") not in (None, "") else None, "text": s.get("text", "")})
    elif tr.get("transcript_only_text"):
        segs = [{"start": None, "end": None, "text": tr["transcript_only_text"]}]
    ch = p.get("channel") or {}
    return {
        "handle": "@" + str(ch["handle"]).lstrip("@") if ch.get("handle") else (ch.get("title") or ""),
        "title": p.get("title") or "",
        "caption": "\n\n".join(x for x in [p.get("title"), p.get("description")] if x),
        "cover_url": p.get("thumbnail") or "",
        "video_url": "",  # YouTube does not hand out a direct file; yt-dlp may get one below
        "duration": float(p.get("durationMs") or 0) / 1000,
        "posted_at": _iso(p.get("publishDate") or p.get("publishDateText")),
        "stats": {"views": p.get("viewCountInt"), "likes": p.get("likeCountInt")},
        "segments": segs,
        "comments": [{"user": str((c.get("author") or {}).get("name", "")).lstrip("@"), "text": c.get("content") or c.get("text") or ""} for c in (out.get("comments") or {}).get("comments") or []],
        "errors": errors,
        "found": bool(out.get("post")) and bool(p.get("title") or p.get("id")),
    }


def _twitter(url):
    out, errors = _settle({"post": lambda: _sc("/v1/twitter/tweet", {"url": url})})
    p = out.get("post") or {}
    t = p if p.get("legacy") else (p.get("data") or p)
    L = t.get("legacy") or {}
    media_list = (L.get("extended_entities") or {}).get("media") or (L.get("entities") or {}).get("media") or []
    media = next((m for m in media_list if m.get("video_info")), media_list[0] if media_list else {})
    variants = sorted([v for v in (media.get("video_info") or {}).get("variants", []) if "mp4" in (v.get("content_type") or "") and v.get("url")],
                      key=lambda v: -(v.get("bitrate") or 0))
    pick = next((v for v in variants if (v.get("bitrate") or 0) <= 2_200_000), variants[-1] if variants else None)
    user = ((t.get("core") or {}).get("user_results") or {}).get("result") or {}
    sn = (user.get("legacy") or {}).get("screen_name") or (user.get("core") or {}).get("screen_name")
    return {
        "handle": "@" + sn if sn else "",
        "caption": L.get("full_text") or "",
        "cover_url": media.get("media_url_https") or "",
        "video_url": (pick or {}).get("url") or "",
        "duration": float((media.get("video_info") or {}).get("duration_millis") or 0) / 1000,
        "posted_at": _iso(L.get("created_at")),
        "stats": {"views": ((t.get("views") or {}).get("count")), "likes": L.get("favorite_count")},
        "segments": [], "comments": [], "errors": errors,
        "found": bool(out.get("post")) and bool(L.get("full_text") is not None or t.get("rest_id")),
    }


def lookup(url):
    fn = {"IG": _instagram, "TT": _tiktok, "YT": _youtube, "X": _twitter}.get(platform_of(url))
    if not fn:
        return {"found": False, "errors": {"post": "not a TikTok, Instagram, YouTube or X link"}}
    return fn(url)


# ---------- yt-dlp fallback ----------

def _cookie_file(tmpdir):
    path = os.environ.get("YTDLP_COOKIES_FILE")
    if path and os.path.exists(path):
        return path
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    files = [f for f in glob.glob(os.path.join(repo, "limbo-video", "*cookies*.txt")) if not os.path.basename(f).startswith(".")]
    if not files:
        return None
    merged = os.path.join(tmpdir, "cookies.txt")
    with open(merged, "w") as out:
        out.write("# Netscape HTTP Cookie File\n")
        for f in files:
            with open(f, errors="ignore") as src:
                out.writelines(l for l in src if not l.startswith("# Netscape"))
            out.write("\n")
    return merged


def ytdlp_fetch(url, tmpdir):
    """Download with yt-dlp. Returns (post_dict, video_path) or raises."""
    import yt_dlp  # optional dependency
    opts = {
        "outtmpl": os.path.join(tmpdir, "video.%(ext)s"),
        "format": "bv*[height<=720]+ba/b[height<=720]/b",
        "merge_output_format": "mp4",
        "noplaylist": True, "quiet": True, "no_warnings": True,
        "ffmpeg_location": ffmpeg_bin(),
        "max_filesize": MAX_BYTES,
        "socket_timeout": 30,
    }
    cookies = _cookie_file(tmpdir)
    if cookies:
        opts["cookiefile"] = cookies
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    vids = [f for f in glob.glob(os.path.join(tmpdir, "video.*")) if not f.endswith(".part")]
    handle = info.get("uploader_id") or info.get("channel") or info.get("uploader") or ""
    post = {
        "handle": ("@" + handle.lstrip("@")) if handle else "",
        "title": info.get("title") or "",
        "caption": info.get("description") or info.get("title") or "",
        "cover_url": info.get("thumbnail") or "",
        "video_url": "",
        "duration": float(info.get("duration") or 0),
        "posted_at": _iso(info.get("upload_date") or info.get("timestamp")),
        "stats": {"views": info.get("view_count"), "likes": info.get("like_count")},
        "segments": [], "comments": [], "errors": {}, "found": True,
    }
    return post, (vids[0] if vids else None)


# ---------- video file ----------

def ffmpeg_bin():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg") or "ffmpeg"


def _run(args, timeout=60):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stderr
    except subprocess.TimeoutExpired:
        return -1, "timeout"
    except Exception as e:
        return -1, str(e)


def _public_https(url):
    try:
        u = urlparse(url)
        h = (u.hostname or "").lower()
        return u.scheme == "https" and h and h != "localhost" and not h.endswith((".local", ".internal")) and not re.fullmatch(r"[\d.]+", h)
    except Exception:
        return False


def download(url, tmpdir, referer=""):
    if not _public_https(url):
        raise RuntimeError("the video address is not a public https link")
    path = os.path.join(tmpdir, "video.mp4")
    with requests.get(url, headers={"user-agent": UA, **({"referer": referer} if referer else {})}, stream=True, timeout=90) as r:
        if not r.ok:
            raise RuntimeError(f"video download failed ({r.status_code})")
        if re.search(r"text/html|application/json", r.headers.get("content-type", "")):
            raise RuntimeError("the video address returned a web page instead of a file")
        total = 0
        with open(path, "wb") as f:
            for chunk in r.iter_content(256 * 1024):
                total += len(chunk)
                if total > MAX_BYTES:
                    raise RuntimeError("video is larger than 150 MB")
                f.write(chunk)
    if total < 20_000:
        raise RuntimeError("the downloaded file is too small to be a video")
    return path


def probe_duration(path):
    _, err = _run([ffmpeg_bin(), "-hide_banner", "-i", path], 30)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err or "")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


def scene_cuts(path, threshold=0.3):
    _, err = _run([ffmpeg_bin(), "-hide_banner", "-i", path, "-vf", f"select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-"], 120)
    return [float(m) for m in re.findall(r"pts_time:([\d.]+)", err or "")]


def pick_times(dur, cuts, max_frames=config.MAX_FRAMES):
    """The opening (the hook), every cut, and even coverage, thinned to max_frames."""
    if dur <= 0:
        return [0.0]
    end = max(0.0, dur - 0.25)
    opening = [t for t in (0.0, 1.0, 2.5) if t < end]

    def spread(items, n):
        if n <= 0 or not items:
            return []
        if len(items) <= n:
            return list(items)
        return [items[round(i * (len(items) - 1) / max(1, n - 1))] for i in range(n)]

    def far(t, chosen, gap=0.5):
        return all(abs(t - c) >= gap for c in chosen)

    chosen = list(opening)
    # Cuts come first: a new shot often brings a new claim, chart or headline on screen.
    cuts_ok = sorted({round(min(c + 0.15, end), 2) for c in cuts})
    cuts_ok = [t for t in cuts_ok if far(t, chosen)]
    room = max_frames - len(chosen)
    chosen += spread(cuts_ok, max(room - max(2, room // 3), 0) if len(cuts_ok) > room else room)
    # Fill what's left with even coverage so long talking-head stretches aren't skipped.
    room = max_frames - len(chosen)
    if room > 0:
        step = max(0.5, end / (room + 1))
        even = [round(step * (i + 1), 2) for i in range(room * 3) if step * (i + 1) < end]
        even = [t for t in even if far(t, chosen, max(0.5, step / 2))]
        chosen += spread(even, room)
    return sorted(set(chosen))[:max_frames]


def extract_frames(path, duration, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    cuts = scene_cuts(path) if duration <= config.MAX_VIDEO_SECONDS else []
    frames = []
    for i, t in enumerate(pick_times(duration, cuts)):
        name = f"frame_{i:02d}_{int(t * 100):06d}.jpg"
        fp = os.path.join(out_dir, name)
        code, _ = _run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
                        "-vf", "scale='min(640,iw)':-2", "-q:v", "5", "-y", fp], 30)
        if code == 0 and os.path.exists(fp) and os.path.getsize(fp) > 800:
            with open(fp, "rb") as f:
                frames.append({"t": t, "name": name, "jpeg": f.read()})
    return frames, len(cuts)


def extract_audio(path, tmpdir):
    out = os.path.join(tmpdir, "audio.mp3")
    code, _ = _run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-i", path, "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", "-y", out], 120)
    if code != 0 or not os.path.exists(out) or os.path.getsize(out) < 2000:
        return None
    return out


def transcribe_groq(audio_path, seconds):
    """Groq Whisper with timestamps. Returns [{start, end, text}]."""
    budget.ensure_ai()
    with open(audio_path, "rb") as f:
        r = requests.post(
            os.environ.get("GROQ_API_BASE", "https://api.groq.com/openai/v1") + "/audio/transcriptions",
            headers={"authorization": f"Bearer {config.groq_key()}"},
            files={"file": ("audio.mp3", f, "audio/mpeg")},
            data={"model": os.environ.get("GROQ_STT_MODEL", "whisper-large-v3-turbo"), "response_format": "verbose_json", "temperature": "0"},
            timeout=120)
    if not r.ok:
        raise RuntimeError(f"Groq {r.status_code}: {r.text[:200]}")
    budget.record(usd=budget.groq_cost(seconds))
    j = r.json()
    segs = [{"start": s.get("start"), "end": s.get("end"), "text": (s.get("text") or "").strip()} for s in j.get("segments") or []]
    words = " ".join(s["text"] for s in segs).split()
    return [] if len(words) <= 3 else segs  # Whisper invents "Thank you." over music


def transcribe_local(path):
    """faster-whisper if installed (local development on a Mac)."""
    from faster_whisper import WhisperModel
    model = WhisperModel(os.environ.get("WHISPER_MODEL", "small"), device="auto", compute_type="int8")
    segs, _ = model.transcribe(path, vad_filter=True)
    return [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()} for s in segs]


def transcript_text(segments):
    lines = []
    for s in segments:
        lines.append(f"[{stamp(s['start'])}] {s['text']}" if s.get("start") is not None else s["text"])
    return "\n".join(lines)


# ---------- the whole stage ----------

def capture(url, frames_dir, progress=lambda step: None, on_update=lambda **kw: None):
    """Returns the evidence packet. Never raises for partial failures; they go in warnings.
    on_update(...) is called as soon as each piece is known (creator, caption, transcript),
    so the phone can show the transcript before the video has been watched."""
    warnings = []
    platform = platform_of(url)
    post = {"found": False, "segments": [], "comments": [], "errors": {}}
    tmpdir = tempfile.mkdtemp(prefix="fc-")
    video = None
    try:
        progress("Fetching the post")
        if config.scrape_key() and platform != "WEB":
            try:
                post = lookup(url)
            except budget.BudgetError:
                raise
            except Exception as e:
                post["errors"]["post"] = str(e)
            if not post.get("found"):
                warnings.append("The post lookup failed (" + str(post.get("errors", {}).get("post", "private or deleted?"))[:90] + ").")

        if post.get("found"):
            segs0 = post.get("segments") or []
            on_update(creator=post.get("handle") or "", caption=(post.get("caption") or "")[:6000], posted_at=post.get("posted_at") or "",
                      title=post.get("title") or "", transcript=transcript_text(segs0)[:30000] if segs0 else None,
                      transcript_source="platform" if segs0 else None)

        if post.get("video_url"):
            progress("Downloading the video")
            if post.get("duration") and post["duration"] > config.MAX_VIDEO_SECONDS:
                warnings.append("The video is longer than 15 minutes, so only the transcript and caption were used.")
            else:
                try:
                    video = download(post["video_url"], tmpdir, referer=url)
                except Exception as e:
                    warnings.append(f"The video file could not be downloaded ({str(e)[:90]}).")

        if not video and platform != "WEB":
            try:
                progress("Downloading the video")
                y_post, video = ytdlp_fetch(url, tmpdir)
                if not post.get("found"):
                    post = {**y_post, "comments": post.get("comments", [])}
                else:
                    for k, v in y_post.items():
                        if not post.get(k) and v:
                            post[k] = v
            except ImportError:
                if not post.get("found"):
                    warnings.append("No way to fetch this video: add SCRAPECREATORS_API_KEY in Railway.")
            except Exception as e:
                if not post.get("found"):
                    warnings.append(f"The video could not be fetched ({str(e)[:120]}). It may be private, deleted, or need a login.")

        duration = post.get("duration") or 0
        frames = []
        if video:
            progress("Watching the video")
            duration = probe_duration(video) or duration
            frames, cuts = extract_frames(video, duration, frames_dir)
            if not frames:
                warnings.append("The video downloaded but no frames could be read from it.")

        segments = post.get("segments") or []
        source = "platform" if segments else ""
        timed = any(s.get("start") is not None for s in segments)
        if video and not timed:
            progress("Transcribing the audio")
            audio = extract_audio(video, tmpdir)
            if audio:
                try:
                    if config.groq_key():
                        segs = transcribe_groq(audio, duration)
                    else:
                        segs = transcribe_local(video)
                    if segs:
                        segments, source = segs, "audio"
                        on_update(transcript=transcript_text(segs)[:30000], transcript_source="audio")
                except ImportError:
                    if not segments:
                        warnings.append("No transcript: add GROQ_API_KEY in Railway to transcribe videos the platform gives no transcript for.")
                except budget.BudgetError:
                    raise
                except Exception as e:
                    warnings.append(f"The audio could not be transcribed ({str(e)[:90]}).")

        return {
            "found": bool(post.get("found")),
            "platform": platform,
            "creator": post.get("handle") or "",
            "title": post.get("title") or "",
            "caption": (post.get("caption") or "")[:6000],
            "posted_at": post.get("posted_at") or "",
            "stats": {k: v for k, v in (post.get("stats") or {}).items() if v not in (None, "")},
            "comments": [c for c in (post.get("comments") or []) if c.get("text")][:8],
            "duration": duration,
            "segments": segments,
            "transcript": transcript_text(segments)[:30000],
            "transcript_source": source,
            "frames": frames,
            "warnings": warnings,
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
