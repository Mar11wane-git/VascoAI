"""Vasco Cloud must keep working when the Pollinations key dies (no network calls here)."""
import json

from backend.agent.llm import FreeCloudAdapter


def _adapter(monkeypatch, history):
    monkeypatch.setenv("POLLINATIONS_API_KEY", "sk_test")
    return FreeCloudAdapter(deep_think=False, system="S" * 9000, history=history, attachments=[])


def test_keyed_adapter_uses_the_keyed_api(monkeypatch):
    a = _adapter(monkeypatch, [{"role": "user", "content": "salam"}])
    assert a.endpoint.startswith("https://gen.pollinations.ai/")
    assert a.headers["Authorization"] == "Bearer sk_test"
    assert a.max_tool_rounds > 1


def test_fallback_switches_to_keyless_api_and_fits_its_limit(monkeypatch):
    long = "kalam twil " * 300
    history = []
    for i in range(20):
        history += [{"role": "user", "content": f"{i} {long}"}, {"role": "assistant", "content": f"{i} {long}"}]
    history.append({"role": "user", "content": "akhir su2al"})
    a = _adapter(monkeypatch, history)
    a._use_classic()
    assert a.endpoint == FreeCloudAdapter.CLASSIC_ENDPOINT
    assert "Authorization" not in a.headers
    assert a.max_tool_rounds == 1
    size = len(json.dumps(a.messages, ensure_ascii=False))
    assert size < FreeCloudAdapter.CLASSIC_MAX_CHARS - 2000
    assert a.messages[0]["role"] == "system"
    assert a.messages[-1]["content"] == "akhir su2al"  # the latest question is always kept


def test_fallback_keeps_a_single_huge_message(monkeypatch):
    a = _adapter(monkeypatch, [{"role": "user", "content": "x" * 50_000}])
    a._use_classic()
    assert len(a.messages) == 2
    assert 0 < len(a.messages[1]["content"]) < 15_000


def test_fallback_never_starts_with_an_orphan_tool_result(monkeypatch):
    a = _adapter(monkeypatch, [{"role": "user", "content": "q"}])
    a.messages += [
        {"role": "assistant", "content": None, "tool_calls": [{"id": "1", "type": "function",
                                                               "function": {"name": "calculator", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "1", "content": "r" * 6000},
        {"role": "tool", "tool_call_id": "2", "content": "small"},
    ]
    a._trim_for_classic()
    assert a.messages[1]["role"] != "tool"
