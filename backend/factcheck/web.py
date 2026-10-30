"""Reading the web for the tracer. Every page read is saved to the source
library in full (the old pipeline kept only the first 8,000 characters and then
threw them away)."""
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ..tools.web_search import web_search  # Brave when BRAVE_SEARCH_API_KEY is set, else DuckDuckGo
from . import config, db

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
MAX_TEXT = 400_000  # characters kept per source

FACTCHECK_DOMAINS = [
    "politifact.com", "factcheck.org", "snopes.com", "fullfact.org", "leadstories.com", "checkyourfact.com",
    "apnews.com/ap-fact-check", "reuters.com/fact-check", "usatoday.com/story/news/factcheck",
    "washingtonpost.com/politics/fact-checker", "afp.com", "factcheck.afp.com", "healthfeedback.org", "sciencefeedback.co",
]
SKIP_LINK_HOSTS = ("facebook.com", "twitter.com", "x.com", "linkedin.com", "pinterest.com", "reddit.com", "whatsapp.com",
                   "mailto:", "instagram.com/accounts", "google.com/intl", "apple.com/app", "play.google.com", "doubleclick", "ads.")


def _clean_text(s):
    s = re.sub(r"\r", "", s or "")
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def content_score(text):
    """Rough count of real sentences or data lines. Menus, footers and JavaScript shells score near zero."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text or "")  # count link text, not URLs
    sentences = len(re.findall(r"[A-Za-z][^.!?\n]{40,}[.!?]", text))
    data_lines = len(re.findall(r"(?m)^.{0,80}(\$\s?[\d,.]+|\b\d{1,3}(,\d{3})+\b|\b(19|20)\d\d\b).{0,120}$", text))
    return sentences + data_lines // 2


THIN = 4  # below this, the page probably didn't render (built by JavaScript) or is only navigation


def _jina(url, engine=None):
    try:
        headers = {"Accept": "text/plain", "X-Return-Format": "markdown"}
        if engine:
            headers["X-Engine"] = engine          # "browser": render the page's JavaScript first
            headers["X-Timeout"] = "30"
        if os.environ.get("JINA_API_KEY"):
            headers["Authorization"] = "Bearer " + os.environ["JINA_API_KEY"]
        r = requests.get("https://r.jina.ai/" + url, headers=headers, timeout=60 if engine else 45)
        if not r.ok:
            return None
        raw = r.text.strip()
        title = ""
        m = re.match(r"Title:\s*(.*)", raw)
        if m:
            title = m.group(1).strip()
        published = ""
        m = re.search(r"^Published Time:\s*(.*)$", raw, re.M)
        if m:
            published = m.group(1).strip()[:25]
        marker = "Markdown Content:"
        body = raw[raw.index(marker) + len(marker):].strip() if marker in raw else raw
        if len(body) < 200 or re.search(r"(?i)^(access denied|just a moment|attention required|error 40\d)", body):
            return None
        return {"title": title, "text": body, "published_at": published, "via": "jina-browser" if engine else "jina"}
    except Exception:
        return None


def _direct(url):
    r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}, timeout=25, allow_redirects=True)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}")
    ctype = r.headers.get("content-type", "")
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        return _pdf(r.content, url)
    soup = BeautifulSoup(r.text, "html.parser")
    meta_date = ""
    for sel in [("meta", {"property": "article:published_time"}), ("meta", {"name": "date"}), ("meta", {"name": "pubdate"}), ("time", {})]:
        tag = soup.find(*sel)
        if tag:
            meta_date = (tag.get("content") or tag.get("datetime") or "")[:25]
            if meta_date:
                break
    embedded = _embedded_text(soup)
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
        tag.decompose()
    main = soup.find("article") or soup.find("main") or soup.body or soup
    # Keep links inline as markdown so the tracer can follow citations.
    for a in main.find_all("a", href=True):
        href = urljoin(r.url, a["href"])
        if href.startswith("http") and a.get_text(strip=True):
            a.replace_with(f"[{a.get_text(' ', strip=True)}]({href})")
    text = main.get_text("\n", strip=True)
    via = "direct"
    if embedded and content_score(text) < THIN and content_score(embedded) > content_score(text):
        text, via = text + "\n\n" + embedded, "direct+embedded-data"
    return {"title": soup.title.get_text(strip=True) if soup.title else "", "text": text, "published_at": meta_date,
            "final_url": r.url, "via": via}


def _embedded_text(soup):
    """JavaScript-built sites often ship the page's content as JSON inside a script tag
    (Next.js __NEXT_DATA__, JSON-LD, Nuxt). Pull the readable strings out of it."""
    chunks = []
    for tag in soup.find_all("script"):
        t = tag.get("type") or ""
        if tag.get("id") in ("__NEXT_DATA__", "__NUXT_DATA__") or "ld+json" in t or (t == "application/json" and len(tag.string or "") > 500):
            try:
                data = json.loads(tag.string or "")
            except ValueError:
                continue
            _walk_strings(data, chunks)
    seen, out = set(), []
    for c in chunks:
        c = BeautifulSoup(c, "html.parser").get_text(" ", strip=True) if "<" in c else c.strip()
        if c and c not in seen and (len(c) > 30 or re.search(r"\$\s?[\d,]+|\d{4}", c)):
            seen.add(c)
            out.append(c)
    return "\n".join(out)[:200_000]


def _walk_strings(node, out, depth=0):
    if depth > 40 or len(out) > 5000:
        return
    if isinstance(node, str):
        if not re.match(r"^(https?:|/|#|[\w-]+\.(js|css|png|jpg|svg))", node) and " " in node.strip():
            out.append(node)
    elif isinstance(node, dict):
        for k, v in node.items():
            if k in ("buildId", "runtimeConfig", "assetPrefix", "scriptLoader"):
                continue
            _walk_strings(v, out, depth + 1)
    elif isinstance(node, list):
        for v in node:
            _walk_strings(v, out, depth + 1)


def _pdf(content, url):
    path = ""
    try:
        os.makedirs(os.path.join(config.FILES_DIR, "docs"), exist_ok=True)
        path = os.path.join(config.FILES_DIR, "docs", db.text_hash(url) + ".pdf")
        if len(content) <= 30 * 1024 * 1024:
            with open(path, "wb") as f:
                f.write(content)
        else:
            path = ""
    except Exception:
        path = ""
    text = ""
    try:
        from pypdf import PdfReader
        import io
        reader = PdfReader(io.BytesIO(content))
        text = "\n\n".join((p.extract_text() or "") for p in reader.pages[:300])
    except Exception:
        pass
    return {"title": os.path.basename(urlparse(url).path), "text": text, "file_path": path, "via": "pdf"}


def fetch(url):
    """Fetch a page's full text. Returns dict or raises with a readable reason."""
    if not url.lower().startswith(("http://", "https://")):
        raise RuntimeError("not a web address")
    # Try the cheap reader first; if the page comes back as an empty shell (menus only, content built by
    # JavaScript), try rendering it in a browser, then the raw HTML plus any data embedded in it. Keep the best.
    attempts, errors = [], []
    readers = [lambda: _direct(url)] if url.lower().endswith(".pdf") else [lambda: _jina(url), lambda: _jina(url, "browser"), lambda: _direct(url)]
    for read in readers:
        try:
            res = read()
        except Exception as e:
            errors.append(str(e)[:80])
            continue
        if not res:
            continue
        res["text"] = _clean_text(res.get("text", ""))[:MAX_TEXT]
        res["score"] = content_score(res["text"])
        attempts.append(res)
        if res["score"] >= THIN or res.get("via") == "pdf":
            break
    attempts = [a for a in attempts if len(a["text"]) >= 80]
    if not attempts:
        raise RuntimeError("the page had no readable text (it may need a login or block automated readers)"
                           + (f": {'; '.join(errors)}" if errors else ""))
    best = max(attempts, key=lambda a: (a["score"], len(a["text"])))
    best["thin"] = best["score"] < THIN and best.get("via") != "pdf"
    return best


def read_into_library(url, max_age_days=30):
    """Return (source_row, from_library). Reuses a recent copy instead of refetching."""
    existing = db.get_source_by_url(url)
    if existing and existing.get("full_text"):
        from datetime import datetime, timezone
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(existing["last_read"])).days
        except Exception:
            age = 999
        if age <= max_age_days:
            return existing, True
    res = fetch(url)
    sid, _ = db.save_source(url, res["text"], title=res.get("title", ""), final_url=res.get("final_url", ""),
                            fetched_via=res.get("via", ""), published_at=res.get("published_at", ""), file_path=res.get("file_path", ""))
    return db.get_source(sid), False


def wayback_lookup(url):
    """Closest Wayback Machine snapshot, or None. Retries when archive.org rate-limits (HTTP 429)."""
    import time
    for attempt in range(3):
        try:
            r = requests.get("https://archive.org/wayback/available", params={"url": url}, timeout=20)
            if r.status_code == 429:
                time.sleep(4 * (attempt + 1))
                continue
            snap = (r.json().get("archived_snapshots") or {}).get("closest") or {}
            if snap.get("available") and snap.get("url"):
                return snap["url"].replace("http://", "https://", 1)
            return None
        except Exception:
            time.sleep(2)
    return None


def fetch_archived(snapshot_url):
    """Read a Wayback snapshot, waiting and retrying when archive.org rate-limits."""
    import time
    last = None
    for attempt in range(3):
        try:
            return fetch(snapshot_url)
        except Exception as e:
            last = e
            if "429" not in str(e):
                break
            time.sleep(5 * (attempt + 1))
    raise last


def wayback_save(url):
    """Ask the Wayback Machine to keep a copy, so public receipts survive deletions. Best effort."""
    try:
        r = requests.get("https://web.archive.org/save/" + url, headers={"User-Agent": UA}, timeout=60, allow_redirects=True)
        loc = r.headers.get("content-location") or ""
        if loc.startswith("/web/"):
            return "https://web.archive.org" + loc
        if "/web/" in r.url and r.ok:
            return r.url
    except Exception:
        pass
    return wayback_lookup(url)


# ---------- other fact-checkers: links only, verdict withheld ----------

def _is_factcheck(url):
    u = url.lower()
    return any(d in u for d in FACTCHECK_DOMAINS)


def _google_factcheck(query):
    key = config.factcheck_api_key()
    if not key:
        return []
    try:
        r = requests.get("https://factchecktools.googleapis.com/v1alpha1/claims:search",
                         params={"query": query[:200], "key": key, "pageSize": 8, "languageCode": "en"}, timeout=20)
        out = []
        for c in r.json().get("claims", []):
            for rev in c.get("claimReview", []):
                out.append({"url": rev.get("url"), "publisher": (rev.get("publisher") or {}).get("name") or (rev.get("publisher") or {}).get("site", ""),
                            "title": rev.get("title", ""), "rating": rev.get("textualRating", ""), "date": rev.get("reviewDate", "")[:10]})
        return out
    except Exception:
        return []


def _outbound_links(article_url):
    r = requests.get(article_url, headers={"User-Agent": UA}, timeout=25)
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["nav", "footer", "header", "aside", "script", "style", "form"]):
        tag.decompose()
    main = soup.find("article") or soup.find("main") or soup.body or soup
    own = urlparse(article_url).hostname or ""
    own = own.replace("www.", "")
    links, seen = [], set()
    for a in main.find_all("a", href=True):
        href = urljoin(article_url, a["href"]).split("#")[0]
        host = (urlparse(href).hostname or "").replace("www.", "")
        if not href.startswith("http") or own in host or any(s in href for s in SKIP_LINK_HOSTS) or href in seen:
            continue
        if _is_factcheck(href):
            continue
        seen.add(href)
        text = a.get_text(" ", strip=True)[:120]
        links.append({"url": href, "link_text": text})
    return links[:25]


def factcheck_leads(query):
    """Find existing fact-check articles on this claim and return ONLY the links they cite.
    Their titles and ratings are kept aside (returned separately) for the second-opinions stage."""
    found = _google_factcheck(query)
    if len(found) < 2:
        sites = " OR ".join(f"site:{d.split('/')[0]}" for d in FACTCHECK_DOMAINS[:8])
        for r in web_search(f"{query} ({sites})", 8):
            if r.get("url") and _is_factcheck(r["url"]) and not any(f["url"] == r["url"] for f in found):
                found.append({"url": r["url"], "publisher": urlparse(r["url"]).hostname.replace("www.", ""), "title": r.get("title", ""), "rating": "", "date": ""})
    found = found[:4]
    leads = []

    def grab(f):
        try:
            return f, _outbound_links(f["url"])
        except Exception:
            return f, []
    with ThreadPoolExecutor(max_workers=4) as ex:
        for f, links in ex.map(grab, found):
            for l in links:
                if not any(x["url"] == l["url"] for x in leads):
                    leads.append(l)
    return found, leads[:30]
