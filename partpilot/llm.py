"""Provider-agnostic chat client with tool use.

The agent keeps its conversation in Anthropic Messages format (content blocks: text / tool_use / tool_result).
Providers:
  anthropic  Anthropic Messages API                    PARTPILOT_API_KEY (or ANTHROPIC_API_KEY)
  groq       Groq, OpenAI-compatible endpoint          PARTPILOT_API_KEY (or GROQ_API_KEY)
  ollama     local Ollama, OpenAI-compatible endpoint  no key; PARTPILOT_BASE_URL defaults to http://localhost:11434/v1
  openai     any other OpenAI-compatible endpoint      PARTPILOT_BASE_URL + PARTPILOT_API_KEY
  offline    no LLM at all: a deterministic intent router (partpilot/offline.py) calls the same tools
Keys are read from the environment only and are never logged.
"""
from __future__ import annotations

import json
import os
import time

import httpx

DEFAULTS = {
    "anthropic": ("https://api.anthropic.com/v1/messages", "claude-sonnet-5-5"),
    "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    "ollama": ("http://localhost:11434/v1", "qwen2.5:7b"),
    "openai": ("", ""),
}
# USD per 1M tokens (input, output): list-price estimates used for the cost column of the audit log
PRICE = {"claude-haiku-5-5": (1.0, 5.0), "claude-sonnet-5-5": (3.0, 15.0), "claude-opus-5-5": (5.0, 25.0)}


def provider() -> str:
    return (os.getenv("PARTPILOT_LLM_PROVIDER") or "offline").strip().lower()


def model_name() -> str:
    p = provider()
    return os.getenv("PARTPILOT_MODEL") or DEFAULTS.get(p, ("", ""))[1] or p


def cost_usd(model: str, tin: int, tout: int) -> float:
    pin, pout = PRICE.get(model, (0.0, 0.0))
    return round(tin / 1e6 * pin + tout / 1e6 * pout, 5)


def _key(*names):
    for n in names:
        v = os.getenv(n)
        if v:
            return v
    return ""


def _retry_post(url, headers, body, timeout=180):
    last = ""
    for attempt in range(3):
        try:
            r = httpx.post(url, headers=headers, json=body, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}"
            if r.status_code in (429, 500, 502, 503, 529):
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(f"LLM error {r.status_code}: {r.text[:300]}")
        except httpx.HTTPError as e:
            last = type(e).__name__
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"LLM endpoint not reachable after 3 attempts ({last})")


# ---------------------------------------------------------------- Anthropic
def _anthropic(messages, system, tools, model, max_tokens):
    url = os.getenv("PARTPILOT_BASE_URL") or DEFAULTS["anthropic"][0]
    key = _key("PARTPILOT_API_KEY", "ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("PARTPILOT_API_KEY is not set for provider 'anthropic'")
    body = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
    if tools:
        body["tools"] = tools
    r = _retry_post(url, {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, body)
    return {"content": r["content"], "usage": {"input_tokens": r["usage"]["input_tokens"], "output_tokens": r["usage"]["output_tokens"]}}


# ---------------------------------------------------------------- OpenAI-compatible (Groq, Ollama, ...)
def to_openai_messages(messages, system):
    out = [{"role": "system", "content": system}]
    for m in messages:
        c = m["content"]
        if isinstance(c, str):
            out.append({"role": m["role"], "content": c})
            continue
        if m["role"] == "assistant":
            text = "".join(b.get("text", "") for b in c if b["type"] == "text")
            calls = [{"id": b["id"], "type": "function", "function": {"name": b["name"], "arguments": json.dumps(b["input"])}}
                     for b in c if b["type"] == "tool_use"]
            msg = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        else:
            for b in c:
                if b["type"] == "tool_result":
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": b["content"]})
                elif b["type"] == "text":
                    out.append({"role": "user", "content": b["text"]})
    return out


def to_openai_tools(tools):
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
            for t in tools or []]


def from_openai_response(r):
    msg = r["choices"][0]["message"]
    blocks = []
    if msg.get("content"):
        blocks.append({"type": "text", "text": msg["content"]})
    for i, tc in enumerate(msg.get("tool_calls") or []):
        try:
            args = json.loads(tc["function"].get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {"_unparsed_arguments": tc["function"].get("arguments")}
        if not isinstance(args, dict):
            args = {}
        blocks.append({"type": "tool_use", "id": tc.get("id") or f"call_{i}", "name": tc["function"]["name"], "input": args})
    u = r.get("usage") or {}
    return {"content": blocks, "usage": {"input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0)}}


def _openai_compat(messages, system, tools, model, max_tokens, prov):
    base = os.getenv("PARTPILOT_BASE_URL") or DEFAULTS[prov][0]
    if not base:
        raise RuntimeError("PARTPILOT_BASE_URL is not set for provider 'openai'")
    key = _key("PARTPILOT_API_KEY", "GROQ_API_KEY") if prov == "groq" else _key("PARTPILOT_API_KEY")
    if prov in ("groq", "openai") and not key:
        raise RuntimeError(f"PARTPILOT_API_KEY is not set for provider '{prov}'")
    headers = {"content-type": "application/json"}
    if key:
        headers["authorization"] = f"Bearer {key}"
    body = {"model": model, "messages": to_openai_messages(messages, system), "max_tokens": max_tokens, "temperature": 0}
    if tools:
        body["tools"] = to_openai_tools(tools)
    return from_openai_response(_retry_post(base.rstrip("/") + "/chat/completions", headers, body, timeout=300))


def chat(messages, system, tools=None, model=None, max_tokens=3000):
    """One model turn. Returns {'content': [blocks], 'usage': {'input_tokens', 'output_tokens'}}."""
    prov, model = provider(), model or model_name()
    if prov == "anthropic":
        return _anthropic(messages, system, tools, model, max_tokens)
    if prov in ("groq", "ollama", "openai"):
        return _openai_compat(messages, system, tools, model, max_tokens, prov)
    raise RuntimeError(f"provider '{prov}' has no chat endpoint (offline mode uses partpilot/offline.py)")


class MockLLM:
    """Scripted stand-in for tests: each call returns the next scripted turn (a list of blocks or a callable)."""

    def __init__(self, turns):
        self.turns, self.calls = list(turns), []

    def __call__(self, messages, system, tools=None, model=None, max_tokens=3000):
        self.calls.append({"messages": [dict(m) for m in messages], "system": system})
        t = self.turns.pop(0) if self.turns else [{"type": "text", "text": "done"}]
        blocks = t(messages) if callable(t) else t
        return {"content": blocks, "usage": {"input_tokens": 100, "output_tokens": 20}}
