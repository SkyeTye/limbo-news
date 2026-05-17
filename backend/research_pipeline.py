import json
import os
import queue
import threading
import traceback
import anthropic
from dotenv import load_dotenv
from datetime import datetime

load_dotenv(override=True)

from .tools.web_search import web_search
from .tools.url_fetcher import fetch_url
from . import storage

TOOLS = [
    {
        "name": "web_search",
        "description": "Search the web for current information. Returns titles, snippets, and URLs. Use targeted, specific queries.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"},
                "max_results": {"type": "integer", "description": "Number of results (default 5, max 10)"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "fetch_url",
        "description": "Fetch the full text content of a specific URL. Use this to read the actual content of documents, articles, hearing transcripts, and official pages.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch"}
            },
            "required": ["url"]
        }
    },
    {
        "name": "complete_research",
        "description": "Call this when you have finished all research for this phase and are ready to return your structured findings.",
        "input_schema": {
            "type": "object",
            "properties": {
                "result": {
                    "type": "object",
                    "description": "Your complete structured research result as a JSON object"
                }
            },
            "required": ["result"]
        }
    }
]

PHASE1_SYSTEM = """You are an investigative journalist doing Phase 1 research: broad fact-gathering.

Your job is to identify all key claims, actors, documents, and leads in the given topic. Search broadly. Fetch the key pages. Build a clear picture of who did what, when, and based on what evidence.

Research approach:
1. Start with 3-4 broad searches on the topic
2. Fetch the most relevant results (not just read snippets)
3. Identify all specific claims being made
4. Identify all people involved with their roles
5. Build a chronological timeline
6. Note every document, testimony, or source mentioned
7. Generate specific follow-up search queries for Phase 2

When you have gathered enough information, call complete_research with this structure:
{
  "key_claims": [{"claim": "...", "source": "...", "url": "...", "direct_quote": "..."}],
  "actors": [{"name": "...", "role": "...", "affiliation": "...", "key_statements": [...]}],
  "timeline": [{"date": "...", "event": "...", "source": "..."}],
  "initial_sources": [{"title": "...", "url": "...", "type": "primary|secondary|tertiary", "summary": "..."}],
  "leads_to_trace": ["specific search query to trace lead 1", "..."],
  "process_notes": ["note 1", "note 2"]
}"""

PHASE2_SYSTEM = """You are a primary source researcher doing Phase 2: tracing every lead to its origin.

You have Phase 1 research findings. Your job is to trace each claim to its PRIMARY source:
- Congressional testimony -> find the hearing transcript, committee name, date, direct quotes
- News stories -> find the original document, press release, or primary record they cite
- Quotes -> find where the person actually said it (not just who's reporting it)
- Statistics or data -> find the original study, report, or database
- Financial claims -> find the actual grant documents, financial records

For each item in leads_to_trace, conduct targeted searches. Fetch primary source documents directly.
Note every URL you attempted and whether it was accessible.

When done, call complete_research with:
{
  "traced_claims": [
    {
      "claim": "...",
      "primary_source_found": true/false,
      "primary_source": {"title": "...", "url": "...", "date": "...", "type": "...", "key_content": "...", "accessible": true/false},
      "verified": "confirmed|disputed|unverified",
      "direct_quote": "exact quote if found",
      "notes": "..."
    }
  ],
  "primary_sources": [{"title": "...", "url": "...", "type": "...", "date": "...", "key_content": "...", "accessible": true/false, "access_notes": "..."}],
  "information_gaps": [{"gap": "...", "why_unknown": "...", "significance": "..."}],
  "research_tree": {"node": "root topic", "children": [{"node": "...", "type": "investigation|finding|gap", "source_url": "...", "status": "verified|unverified|gap", "children": [...]}]},
  "process_notes": ["..."]
}"""

PHASE3_SYSTEM = """You are a political and media analyst doing Phase 3: opinion mapping.

You have research findings. Your job is to map the FULL RANGE of responses to the core evidence across different perspectives. You need to find actual named people making actual statements - not just your own characterization.

Search for:
- Scientists and researchers commenting on the findings
- Politicians from both parties who have spoken on this
- Former government officials
- Journalists and commentators across the political spectrum
- International perspectives if relevant

For each position you identify, find direct quotes from named people. Note what evidence they cite and what they ignore.

When done, call complete_research with:
{
  "core_question": "What is the central factual question being disputed?",
  "opinion_map": [
    {
      "label": "Position name",
      "spectrum_position": 0-100 (0=fully dismisses, 100=fully confirms the core allegation),
      "summary": "What this position holds",
      "key_voices": [{"name": "...", "affiliation": "...", "quote": "...", "source_url": "..."}],
      "evidence_cited": ["what this side points to"],
      "evidence_ignored": ["what this side doesn't address"],
      "logical_weaknesses": ["..."]
    }
  ],
  "process_notes": ["..."]
}"""

SYNTHESIS_SYSTEM = """You are an editor synthesizing research into a clear, readable report.

Write with extreme clarity. No em-dashes. No oddly formal sentence structure. Direct sentences. Use direct quotes where available. State what is known, what is disputed, and what is unknown.

Your output must be called via complete_research with the full article structure. The executive_summary should be 3-4 clear paragraphs that a smart non-expert can read in 2 minutes and understand exactly what happened, what is proven, and what is still uncertain."""


class ResearchPipeline:
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    def _execute_tool(self, name: str, inputs: dict):
        if name == "web_search":
            return web_search(inputs["query"], inputs.get("max_results", 5))
        elif name == "fetch_url":
            return fetch_url(inputs["url"])
        return {"error": f"Unknown tool: {name}"}

    def _run_agent(self, system: str, user_prompt: str, max_turns: int = 60) -> dict:
        messages = [{"role": "user", "content": user_prompt}]

        for _ in range(max_turns):
            response = self.client.messages.create(
                model="claude-opus-4-7",
                max_tokens=8192,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                tools=TOOLS,
                messages=messages
            )

            tool_results = []
            complete_result = None

            for block in response.content:
                if block.type == "tool_use":
                    if block.name == "complete_research":
                        complete_result = block.input.get("result", {})
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": "Research recorded."
                        })
                    else:
                        output = self._execute_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(output, ensure_ascii=False)[:4000]
                        })

            if complete_result is not None:
                # Claude sometimes returns result as a JSON string instead of a dict
                if isinstance(complete_result, str):
                    try:
                        complete_result = json.loads(complete_result)
                    except Exception:
                        complete_result = {"raw_output": complete_result}
                return complete_result if isinstance(complete_result, dict) else {}

            if response.stop_reason == "end_turn":
                for block in response.content:
                    if hasattr(block, "text") and block.text:
                        try:
                            parsed = json.loads(block.text)
                            return parsed if isinstance(parsed, dict) else {"raw_output": block.text}
                        except Exception:
                            return {"raw_output": block.text}
                return {}

            # Serialize Pydantic content blocks to plain dicts before passing back to API
            serialized_content = [
                block.model_dump() if hasattr(block, "model_dump") else block
                for block in response.content
            ]
            messages.append({"role": "assistant", "content": serialized_content})
            if tool_results:
                messages.append({"role": "user", "content": tool_results})

        return {}

    def _synthesize(self, topic: str, phase1: dict, phase2: dict, phase3: dict) -> dict:
        prompt = f"""Topic: {topic}

PHASE 1 FINDINGS (broad research):
{json.dumps(phase1, indent=2)[:6000]}

PHASE 2 FINDINGS (source tracing):
{json.dumps(phase2, indent=2)[:6000]}

PHASE 3 FINDINGS (opinion mapping):
{json.dumps(phase3, indent=2)[:4000]}

Synthesize all of this into a complete article structure. Call complete_research with:
{{
  "executive_summary": "3-4 clear paragraphs",
  "key_claims": [...from phase2 traced_claims],
  "actors": [...from phase1],
  "timeline": [...from phase1],
  "primary_sources": [...from phase2],
  "research_tree": {{...from phase2}},
  "opinion_map": {{
    "core_question": "...",
    "positions": [...from phase3 opinion_map]
  }},
  "information_gaps": [...from phase2],
  "financial_trail": {{
    "grants": [...any grant data found],
    "notes": "..."
  }},
  "process_notes": [...combined from all phases],
  "full_report": "A long-form, clear, well-organized markdown article covering the full story with all evidence and context"
}}"""

        return self._run_agent(SYNTHESIS_SYSTEM, prompt, max_turns=10)

    def run(self, topic: str, article_id: str):
        """Run the full pipeline synchronously. Called by ResearchQueue worker."""
        try:
            storage.update_article(article_id, {
                "status": "researching",
                "progress_notes": ["Phase 1: Broad research started..."]
            })

            phase1 = self._run_agent(
                PHASE1_SYSTEM,
                f"Research this topic thoroughly: {topic}"
            )

            storage.update_article(article_id, {
                "progress_notes": ["Phase 1 complete.", "Phase 2: Tracing primary sources..."]
            })

            phase2 = self._run_agent(
                PHASE2_SYSTEM,
                f"Topic: {topic}\n\nPhase 1 findings to trace:\n{json.dumps(phase1, indent=2)[:8000]}"
            )

            storage.update_article(article_id, {
                "progress_notes": ["Phase 1 complete.", "Phase 2 complete.", "Phase 3: Mapping opinion spectrum..."]
            })

            phase3 = self._run_agent(
                PHASE3_SYSTEM,
                f"Topic: {topic}\n\nResearch findings:\n{json.dumps({**phase1, **phase2}, indent=2)[:8000]}"
            )

            storage.update_article(article_id, {
                "progress_notes": ["Phase 1 complete.", "Phase 2 complete.", "Phase 3 complete.", "Synthesizing..."]
            })

            final = self._synthesize(topic, phase1, phase2, phase3)
            if not isinstance(final, dict):
                final = {}
            final["status"] = "complete"
            final["topic"] = topic
            final["id"] = article_id
            final["date_researched"] = datetime.utcnow().isoformat()

            storage.update_article(article_id, final)

        except Exception as e:
            storage.update_article(article_id, {
                "status": "failed",
                "error": str(e),
                "traceback": traceback.format_exc()
            })


class ResearchQueue:
    def __init__(self):
        self._pipeline = ResearchPipeline()
        self._queue: queue.Queue = queue.Queue()
        self._current_id: str | None = None
        self._lock = threading.Lock()
        worker = threading.Thread(target=self._worker, daemon=True)
        worker.start()

    def submit(self, topic: str, article_id: str):
        self._queue.put((topic, article_id))

    def size(self) -> int:
        return self._queue.qsize()

    def current_job_id(self) -> str | None:
        with self._lock:
            return self._current_id

    def _worker(self):
        while True:
            topic, article_id = self._queue.get()
            with self._lock:
                self._current_id = article_id
            try:
                self._pipeline.run(topic, article_id)
            finally:
                with self._lock:
                    self._current_id = None
                self._queue.task_done()
