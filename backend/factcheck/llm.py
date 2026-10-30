"""Claude calls for the fact-checker: one-shot structured calls and a tool-using
agent loop with budgets, prompt caching and cost tracking."""
import copy
import json
import time

import anthropic

from . import budget, config

_client = None


def client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.anthropic_key(), max_retries=2)
    return _client


def create(**kwargs):
    """messages.create with backoff on overload/rate limits, plus cost recording."""
    budget.ensure_ai()
    last = None
    for attempt in range(5):
        try:
            resp = client().messages.create(**kwargs)
            budget.record(usd=budget.claude_cost(resp.model or kwargs.get("model"), resp.usage))
            return resp
        except anthropic.APIStatusError as e:
            last = e
            if e.status_code in (429, 500, 502, 503, 529) and attempt < 4:
                time.sleep(8 * (2 ** attempt))
                continue
            raise
        except anthropic.APIConnectionError as e:
            last = e
            if attempt < 4:
                time.sleep(5 * (attempt + 1))
                continue
            raise
    raise last


def structured(model, system, content, tool_name, schema, max_tokens=4000):
    """Force Claude to answer by calling one tool; return that tool's input."""
    resp = create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        tools=[{"name": tool_name, "description": "Record your answer.", "input_schema": schema}],
        tool_choice={"type": "tool", "name": tool_name},
        messages=[{"role": "user", "content": content}],
    )
    for b in resp.content:
        if b.type == "tool_use" and b.name == tool_name:
            return b.input
    raise RuntimeError("Claude did not return a structured answer")


def _mark_cache(messages):
    """Keep exactly one rolling cache breakpoint: the last block of the last message."""
    for m in messages:
        if isinstance(m["content"], list):
            for b in m["content"]:
                if isinstance(b, dict):
                    b.pop("cache_control", None)
    last = messages[-1]
    if isinstance(last["content"], list) and last["content"]:
        blk = last["content"][-1]
        if isinstance(blk, dict):
            blk["cache_control"] = {"type": "ephemeral"}


def run_agent(model, system, user_prompt, tools, handle_tool, finish_tool, max_turns=30, nudge_at=6, max_tokens=6000, on_turn=None):
    """Generic loop. handle_tool(name, input) -> (result_text, finished_payload_or_None).
    Stops when the finish tool is accepted, or after max_turns (returns None)."""
    messages = [{"role": "user", "content": [{"type": "text", "text": user_prompt}]}]
    sys_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    for turn in range(max_turns):
        _mark_cache(messages)
        resp = create(model=model, max_tokens=max_tokens, system=sys_blocks, tools=tools, messages=messages)
        if on_turn:
            on_turn(turn)
        assistant = [b.model_dump(exclude_none=True) for b in resp.content]
        messages.append({"role": "assistant", "content": assistant})
        results, finished = [], None
        for b in resp.content:
            if b.type != "tool_use":
                continue
            try:
                text, payload = handle_tool(b.name, b.input or {})
            except Exception as e:  # a tool bug should not kill the trace
                text, payload = f"ERROR: {e}", None
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": str(text)})
            if payload is not None:
                finished = payload
        if finished is not None:
            return finished
        if not results:
            # The model stopped talking without finishing. Ask it to finish.
            messages.append({"role": "user", "content": [{"type": "text", "text": f"Call {finish_tool} now with what you have."}]})
            continue
        left = max_turns - turn - 1
        if left <= nudge_at:
            results.append({"type": "text", "text": f"About {left} turns left. Record any hop you have verified, then call {finish_tool}."})
        messages.append({"role": "user", "content": results})
    return None


def images_content(frames, label_fn):
    """frames: [{'t': seconds, 'jpeg': bytes}] -> Claude image blocks, each preceded by its timestamp."""
    import base64
    out = []
    for f in frames:
        out.append({"type": "text", "text": f"Frame at {label_fn(f['t'])}"})
        out.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(f["jpeg"]).decode()}})
    return out
