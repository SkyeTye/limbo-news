import os
import requests

_BRAVE_KEY = os.environ.get("BRAVE_SEARCH_API_KEY", "")
_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


def web_search(query: str, max_results: int = 5) -> list[dict]:
    if _BRAVE_KEY:
        return _brave_search(query, max_results)
    return _ddg_search(query, max_results)


def _brave_search(query: str, max_results: int) -> list[dict]:
    try:
        res = requests.get(
            _BRAVE_ENDPOINT,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": _BRAVE_KEY,
            },
            params={"q": query, "count": min(max_results, 20)},
            timeout=10,
        )
        res.raise_for_status()
        results = res.json().get("web", {}).get("results", [])
        return [
            {
                "title": r.get("title", ""),
                "snippet": r.get("description", ""),
                "url": r.get("url", ""),
            }
            for r in results
        ]
    except Exception as e:
        return [{"error": str(e), "title": "", "snippet": "", "url": ""}]


def _ddg_search(query: str, max_results: int) -> list[dict]:
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return [
            {
                "title": r.get("title", ""),
                "snippet": r.get("body", ""),
                "url": r.get("href", ""),
            }
            for r in results
        ]
    except Exception as e:
        return [{"error": str(e), "title": "", "snippet": "", "url": ""}]
