import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
MAX_CHARS = 8000


def fetch_url(url: str) -> dict:
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)
        lines = [line for line in text.splitlines() if line.strip()]
        clean_text = "\n".join(lines)

        return {
            "url": url,
            "title": soup.title.string.strip() if soup.title else "",
            "content": clean_text[:MAX_CHARS],
            "truncated": len(clean_text) > MAX_CHARS,
            "status": response.status_code
        }
    except requests.exceptions.HTTPError as e:
        return {"url": url, "error": f"HTTP {e.response.status_code}", "content": "", "status": e.response.status_code}
    except Exception as e:
        return {"url": url, "error": str(e), "content": "", "status": 0}
