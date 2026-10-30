"""Reading the web for the tracer. Every page read is saved to the source
library in full (the old pipeline kept only the first 8,000 characters and then
threw them away)."""
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


def _jina(url):
    try:
        headers = {"Accept": "text/plain", "X-Return-Format": "markdown"}
        if os.environ.get("JINA_API_KEY"):
            headers["Authorization"] = "Bearer " + os.environ["JINA_API_KEY"]
        r = requests.get("https://r.jina.ai/" + url, headers=headers, timeout=45)
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
        return {"title": title, "text": body, "published_at": published, "via": "jina"}
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
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
        tag.decompose()
    main = soup.find("article") or soup.find("main") or soup.body or soup
    # Keep links inline as markdown so the tracer can follow citations.
    for a in main.find_all("a", href=True):
        href = urljoin(r.url, a["href"])
        if href.startswith("http") and a.get_text(strip=True):
            a.replace_with(f"[{a.get_text(' ', strip=True)}]({href})")
    text = main.get_text("\n", strip=True)
    return {"title": soup.title.get_text(strip=True) if soup.title else "", "text": text, "published_at": meta_date,
            "final_url": r.url, "via": "direct"}


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
    res = _jina(url) if not url.lower().endswith(".pdf") else None
    if res is None:
        res = _direct(url)
    res["text"] = _clean_text(res.get("text", ""))[:MAX_TEXT]
    if len(res["text"]) < 80:
        raise RuntimeError("the page had no readable text (it may need a login or block automated readers)")
    return res


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
    """Closest Wayback Machine snapshot, or None."""
    try:
        r = requests.get("https://archive.org/wayback/available", params={"url": url}, timeout=20)
        snap = (r.json().get("archived_snapshots") or {}).get("closest") or {}
        if snap.get("available") and snap.get("url"):
            return snap["url"].replace("http://", "https://", 1)
    except Exception:
        pass
    return None


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
