import json
import os
import shutil
import uuid
from datetime import datetime

# In production, set DATA_DIR to a Railway persistent volume path (e.g. /app/userdata).
# In development, falls back to data/ in the repo root.
_DATA_ENV = os.environ.get("DATA_DIR")
_REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")

if _DATA_ENV:
    ARTICLES_DIR = os.path.join(_DATA_ENV, "articles")
    ARCHIVE_DIR = os.path.join(_DATA_ENV, "archive")
else:
    ARTICLES_DIR = os.path.join(_REPO_ROOT, "data", "articles")
    ARCHIVE_DIR = os.path.join(_REPO_ROOT, "data", "archive")

# Git-committed seed articles — always available regardless of DATA_DIR.
# In production these are the articles shipped with the repo.
_SEED_DIR = os.path.join(_REPO_ROOT, "data", "articles")


def _ensure_dir():
    os.makedirs(ARTICLES_DIR, exist_ok=True)


def _find_article_path(article_id: str) -> str | None:
    """Return the path to article_id.json, checking active dirs then archive."""
    for d in _search_dirs() + [ARCHIVE_DIR]:
        path = os.path.join(d, f"{article_id}.json")
        if os.path.exists(path):
            return path
    return None


def _search_dirs() -> list[str]:
    """Directories to search when reading articles (deduped)."""
    dirs = [ARTICLES_DIR]
    if os.path.isdir(_SEED_DIR) and os.path.abspath(_SEED_DIR) != os.path.abspath(ARTICLES_DIR):
        dirs.append(_SEED_DIR)
    return dirs


def create_job(topic: str) -> dict:
    _ensure_dir()
    article_id = str(uuid.uuid4())[:8]
    article = {
        "id": article_id,
        "topic": topic,
        "status": "researching",
        "date_researched": datetime.utcnow().isoformat(),
        "progress_notes": []
    }
    with open(os.path.join(ARTICLES_DIR, f"{article_id}.json"), "w") as f:
        json.dump(article, f, indent=2)
    return article


def update_article(article_id: str, data: dict):
    _ensure_dir()
    # Always write updates to the runtime ARTICLES_DIR
    path = os.path.join(ARTICLES_DIR, f"{article_id}.json")
    existing = {}
    existing_path = _find_article_path(article_id)
    if existing_path:
        with open(existing_path) as f:
            existing = json.load(f)
    existing.update(data)
    with open(path, "w") as f:
        json.dump(existing, f, indent=2)


def get_article(article_id: str) -> dict | None:
    path = _find_article_path(article_id)
    if not path:
        return None
    with open(path) as f:
        return json.load(f)


def list_articles() -> list[dict]:
    _ensure_dir()
    seen = set()
    articles = []
    all_files = []

    for d in _search_dirs():
        if not os.path.isdir(d):
            continue
        for fname in os.listdir(d):
            if fname.endswith(".json") and fname not in seen:
                seen.add(fname)
                all_files.append(os.path.join(d, fname))

    for fpath in sorted(all_files, reverse=True):
        with open(fpath) as f:
            try:
                articles.append(json.load(f))
            except json.JSONDecodeError:
                pass
    return articles


def archive_article(article_id: str) -> bool:
    src = _find_article_path(article_id)
    if not src:
        return False
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    shutil.move(src, os.path.join(ARCHIVE_DIR, f"{article_id}.json"))
    return True


def list_archived_articles() -> list[dict]:
    if not os.path.isdir(ARCHIVE_DIR):
        return []
    articles = []
    for fname in sorted(os.listdir(ARCHIVE_DIR), reverse=True):
        if fname.endswith(".json"):
            with open(os.path.join(ARCHIVE_DIR, fname)) as f:
                try:
                    articles.append(json.load(f))
                except json.JSONDecodeError:
                    pass
    return articles


def save_article(article: dict) -> str:
    _ensure_dir()
    if "id" not in article:
        article["id"] = str(uuid.uuid4())[:8]
    with open(os.path.join(ARTICLES_DIR, f"{article['id']}.json"), "w") as f:
        json.dump(article, f, indent=2)
    return article["id"]
