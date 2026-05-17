import os
import sys

# Allow running as `python backend/server.py` from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, request, send_from_directory, abort
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv(override=True)

from backend import storage
from backend.research_pipeline import ResearchQueue

app = Flask(__name__, static_folder="../frontend", static_url_path="")
CORS(app)

research_queue = ResearchQueue()

# --- Static frontend ---

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")

@app.route("/article/<article_id>")
def article_page(article_id):
    return send_from_directory(app.static_folder, "article.html")

# --- API ---

@app.route("/api/articles")
def list_articles():
    articles = storage.list_articles()
    # Return lightweight summary for the list view
    summaries = []
    for a in articles:
        summaries.append({
            "id": a.get("id"),
            "topic": a.get("topic"),
            "status": a.get("status"),
            "date_researched": a.get("date_researched"),
            "excerpt": _excerpt(a.get("executive_summary", "")),
            "source_count": len(a.get("primary_sources", [])),
            "claim_count": len(a.get("key_claims", []))
        })
    return jsonify(summaries)


@app.route("/api/articles/<article_id>")
def get_article(article_id):
    article = storage.get_article(article_id)
    if not article:
        abort(404)
    return jsonify(article)


@app.route("/api/articles/<article_id>", methods=["DELETE"])
def delete_article(article_id):
    if not storage.archive_article(article_id):
        abort(404)
    return jsonify({"id": article_id, "archived": True})


@app.route("/api/research", methods=["POST"])
def start_research():
    data = request.get_json()
    topic = (data or {}).get("topic", "").strip()
    if not topic:
        return jsonify({"error": "topic is required"}), 400

    job = storage.create_job(topic)
    research_queue.submit(topic, job["id"])
    position = research_queue.size()
    return jsonify({"id": job["id"], "status": "queued", "queue_position": position}), 202


@app.route("/api/queue")
def queue_status():
    return jsonify({
        "size": research_queue.size(),
        "current_job_id": research_queue.current_job_id()
    })


@app.route("/api/research/<article_id>/cancel", methods=["POST"])
def cancel_research(article_id):
    article = storage.get_article(article_id)
    if not article:
        abort(404)
    research_queue.cancel(article_id)
    return jsonify({"id": article_id, "status": "cancelled"})


@app.route("/api/research/<article_id>/status")
def research_status(article_id):
    article = storage.get_article(article_id)
    if not article:
        abort(404)
    return jsonify({
        "id": article_id,
        "status": article.get("status"),
        "progress_notes": article.get("progress_notes", [])
    })


def _excerpt(text: str, length: int = 200) -> str:
    if not text:
        return ""
    clean = text.replace("\n", " ").strip()
    return clean[:length] + ("..." if len(clean) > length else "")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", os.environ.get("FLASK_PORT", 5050)))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    print(f"Limbo News running at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=debug)
