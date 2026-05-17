import json
import os
import uuid
from datetime import datetime

ARTICLES_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "articles")


def _ensure_dir():
    os.makedirs(ARTICLES_DIR, exist_ok=True)


def _article_path(article_id: str) -> str:
    return os.path.join(ARTICLES_DIR, f"{article_id}.json")


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
    with open(_article_path(article_id), "w") as f:
        json.dump(article, f, indent=2)
    return article


def update_article(article_id: str, data: dict):
    _ensure_dir()
    path = _article_path(article_id)
    existing = {}
    if os.path.exists(path):
        with open(path) as f:
            existing = json.load(f)
    existing.update(data)
    with open(path, "w") as f:
        json.dump(existing, f, indent=2)


def get_article(article_id: str) -> dict | None:
    path = _article_path(article_id)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def list_articles() -> list[dict]:
    _ensure_dir()
    articles = []
    for fname in sorted(os.listdir(ARTICLES_DIR), reverse=True):
        if fname.endswith(".json"):
            with open(os.path.join(ARTICLES_DIR, fname)) as f:
                try:
                    articles.append(json.load(f))
                except json.JSONDecodeError:
                    pass
    return articles


def save_article(article: dict) -> str:
    _ensure_dir()
    if "id" not in article:
        article["id"] = str(uuid.uuid4())[:8]
    with open(_article_path(article["id"]), "w") as f:
        json.dump(article, f, indent=2)
    return article["id"]
