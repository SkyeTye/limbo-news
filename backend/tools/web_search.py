from duckduckgo_search import DDGS


def web_search(query: str, max_results: int = 5) -> list[dict]:
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return [
            {
                "title": r.get("title", ""),
                "snippet": r.get("body", ""),
                "url": r.get("href", "")
            }
            for r in results
        ]
    except Exception as e:
        return [{"error": str(e), "title": "", "snippet": "", "url": ""}]
