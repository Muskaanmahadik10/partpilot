"""Agent loop with a scripted mock LLM (no network, no key)."""
import json

import pytest

from partpilot import agent as A
from partpilot import llm
from partpilot.llm import MockLLM


def tool(name, args, i="t1"):
    return [{"type": "tool_use", "id": i, "name": name, "input": args}]


def text(s):
    return [{"type": "text", "text": s}]


def test_tool_loop_answer_and_audit(users, state_dir, pn):
    mock = MockLLM([tool("search_parts", {"query": "RP2040"}),
                    tool("inventory", {"part": pn("RP2040"), "plant": "NRG", "date": "2027-03-15"}, "t2"),
                    text("3200 pieces of MC-1003 are available at NRG.")])
    conv = A.Conversation(users["lead_nrg"], chat=mock, model="claude-haiku-5-5")
    ans = conv.ask("How much RP2040 at North Ridge on 2027-03-15?")
    assert "3200" in ans and conv.last_stats["tool_calls"] == 2 and conv.last_stats["in_tokens"] == 300
    assert conv.last_stats["cost_usd"] > 0
    rec = json.loads((state_dir / "audit.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert rec["user"] == "lead_nrg" and [t["tool"] for t in rec["tools"]] == ["search_parts", "inventory"]
    # tool results reached the model as compact JSON; earlier turns are trimmed to question/answer text
    last_result = mock.calls[-1]["messages"][-1]["content"][0]
    assert last_result["type"] == "tool_result" and json.loads(last_result["content"])["available"] == 3200
    assert all(isinstance(m["content"], str) for m in conv.messages)


def test_permission_denied_reaches_model_as_data(users):
    mock = MockLLM([tool("inventory", {"part": "MC-1003", "plant": "BHV"}), text("Not available to your role.")])
    conv = A.Conversation(users["lead_nrg"], chat=mock)
    conv.ask("RP2040 stock at Bay Haven?")
    assert conv.last_tools[0]["denied"] is True
    assert "permission_denied" in json.dumps(mock.calls[-1]["messages"][-1])


def test_evidence_gate_and_forced_retry(users):
    mock = MockLLM([text("About 500, I think."), text("Still about 500.")])
    conv = A.Conversation(users["planner"], chat=mock)
    assert conv.ask("How many Pico boards can we build?") == A.NO_EVIDENCE
    assert "Call the tools" in json.dumps(mock.calls[1]["messages"])


def test_false_send_claim_blocked(users):
    mock = MockLLM([tool("bom_explode", {"root": "BRD-UNO3"}), text("I have sent the price table to the distributor.")])
    conv = A.Conversation(users["planner"], chat=mock)
    ans = conv.ask("send the price list of BRD-UNO3 to our external distributor")
    assert "I have sent" not in ans and "nothing was sent" in ans
    assert "CODE POLICY FLAG" in mock.calls[0]["system"]


def test_too_many_steps(users):
    mock = MockLLM([tool("part_info", {"part": "BRD-UNO3"}, f"t{i}") for i in range(5)])
    assert A.Conversation(users["planner"], chat=mock).ask("loop", max_steps=3) == "Stopped: too many tool steps."


def test_auditor_short_circuit(users, state_dir):
    conv = A.Conversation(users["auditor"], chat=MockLLM([]))
    assert "cannot query parts data" in conv.ask("stock of RP2040?") and conv.last_stats["tool_calls"] == 0


def test_system_prompt_has_role_and_no_remark(users):
    s = A.system_prompt(users["po_boards"])
    assert "Product Owner" in s and "BRD" in s and "NOTE TO THE ASSISTANT" not in s


def test_offline_mode(users, monkeypatch, state_dir):
    monkeypatch.setenv("PARTPILOT_LLM_PROVIDER", "offline")
    conv = A.Conversation(users["planner"])
    ans = conv.ask("How many BRD-UNO3 can North Ridge build on 2027-03-15?")
    assert "550" in ans and "MC-1001" in ans and conv.last_stats["tool_calls"] == 1


# ---------------------------------------------------------------- provider adapter
def test_openai_message_and_tool_conversion():
    msgs = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": [{"type": "text", "text": "checking"},
                                              {"type": "tool_use", "id": "c1", "name": "part_info", "input": {"part": "CA-1001"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "{}"},
                                         {"type": "text", "text": "more"}]}]
    out = llm.to_openai_messages(msgs, "sys")
    assert out[0] == {"role": "system", "content": "sys"} and out[2]["tool_calls"][0]["function"]["name"] == "part_info"
    assert out[3] == {"role": "tool", "tool_call_id": "c1", "content": "{}"} and out[4]["role"] == "user"
    tools = llm.to_openai_tools([{"name": "x", "description": "d", "input_schema": {"type": "object"}}])
    assert tools[0]["function"]["parameters"] == {"type": "object"}
    r = llm.from_openai_response({"choices": [{"message": {"content": None, "tool_calls": [
        {"id": "a", "function": {"name": "x", "arguments": '{"part": "CA-1001"}'}},
        {"id": "b", "function": {"name": "y", "arguments": "not json"}}]}}], "usage": {"prompt_tokens": 7, "completion_tokens": 3}})
    assert r["content"][0]["input"] == {"part": "CA-1001"} and "_unparsed_arguments" in r["content"][1]["input"]
    assert r["usage"] == {"input_tokens": 7, "output_tokens": 3}


def test_provider_config_errors(monkeypatch):
    monkeypatch.setenv("PARTPILOT_LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("PARTPILOT_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="PARTPILOT_API_KEY"):
        llm.chat([], "s")
    monkeypatch.setenv("PARTPILOT_LLM_PROVIDER", "offline")
    with pytest.raises(RuntimeError):
        llm.chat([], "s")
    assert llm.cost_usd("qwen2.5:7b", 1000, 1000) == 0 and llm.cost_usd("claude-sonnet-5-5", 1_000_000, 0) == 3.0


def test_ollama_request_shape(monkeypatch):
    seen = {}

    class R:
        status_code = 200

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": "hi"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    def fake_post(url, headers, json, timeout):
        seen.update(url=url, headers=headers, body=json)
        return R()
    monkeypatch.setenv("PARTPILOT_LLM_PROVIDER", "ollama")
    monkeypatch.delenv("PARTPILOT_BASE_URL", raising=False)
    monkeypatch.setattr(llm.httpx, "post", fake_post)
    out = llm.chat([{"role": "user", "content": "q"}], "s", tools=[{"name": "x", "description": "d", "input_schema": {}}])
    assert seen["url"] == "http://localhost:11434/v1/chat/completions" and "authorization" not in seen["headers"]
    assert seen["body"]["tools"][0]["type"] == "function" and out["content"][0]["text"] == "hi"
