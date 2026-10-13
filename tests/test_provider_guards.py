"""Transport diagnostics, outage/billing halts and the funding preflight; no network."""
import io
import json
from decimal import Decimal
from types import SimpleNamespace

import httpx
import openai
import pytest

from r3e.loop.env import LlmEnvError, ProviderHalt, ThinkingTransport, deepseek_balance
from r3e.providers.openai_compatible import OpenAICompatibleProviderViolation

ENV = {"DEEPSEEK_API_KEY": "sk-SECRET123", "DEEPSEEK_BASE_URL": "https://api.example.test/v1"}
REQ = httpx.Request("POST", "https://api.example.test/v1/chat/completions")


def status_error(code, message):
    response = httpx.Response(code, request=REQ, headers={"x-request-id": "rid-1"})
    return openai.APIStatusError(f"Error code: {code} - echoed prompt text", response=response,
                                 body={"message": message, "type": "invalid_request_error", "code": "x"})


def answer():
    msg = SimpleNamespace(content='{"ok": 1}', refusal=None, reasoning_content="")
    return SimpleNamespace(id="r", choices=[SimpleNamespace(message=msg, finish_reason="length")],
                           usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4))


def transport(monkeypatch, tmp_path, outcomes):
    sent = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            assert kwargs["max_retries"] == 0
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **request):
            sent.append(request)
            item = outcomes.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    t = ThinkingTransport(ENV, key_name="DEEPSEEK_API_KEY", url_name="DEEPSEEK_BASE_URL", thinking="low",
                          timeout_seconds=5, error_log=tmp_path / "errors.jsonl")
    return t, sent


def test_billing_error_halts_at_once_with_sanitized_diagnostics(monkeypatch, tmp_path):
    t, sent = transport(monkeypatch, tmp_path, [status_error(402, "Insufficient Balance sk-SECRET123 see https://x.test/a?q=1")])
    with pytest.raises(ProviderHalt) as halt:
        t(model="m", messages=[])
    assert not isinstance(halt.value, OpenAICompatibleProviderViolation)
    d = halt.value.diagnostics
    assert d["halt"] == "auth_or_billing" and d["http_status"] == 402 and d["request_id"] == "rid-1"
    assert d["cause"] == "APIStatusError" and d["billing"] == "unknown"
    assert d["error_message"] == "Insufficient Balance [redacted] see [url]"
    log = (tmp_path / "errors.jsonl").read_text()
    assert "SECRET" not in log and "echoed prompt" not in log and "x.test" not in log
    with pytest.raises(ProviderHalt):  # no further HTTP attempt after a halt
        t(model="m", messages=[])
    assert len(sent) == 1


def test_three_consecutive_failures_halt_and_a_received_answer_resets(monkeypatch, tmp_path):
    timeout = lambda: openai.APITimeoutError(request=REQ)
    t, sent = transport(monkeypatch, tmp_path, [timeout(), timeout(), answer(),
                                                timeout(), status_error(503, "busy"), openai.APIConnectionError(request=REQ)])
    for _ in range(2):
        with pytest.raises(OpenAICompatibleProviderViolation) as failure:
            t(model="m", messages=[])
        assert failure.value.diagnostics["cause"] == "APITimeoutError"
    assert t(model="m", messages=[])["finish_reason"] == "length"  # length-limited answer is not a transport failure
    with pytest.raises(OpenAICompatibleProviderViolation):
        t(model="m", messages=[])
    with pytest.raises(OpenAICompatibleProviderViolation, match=r"HTTP 503"):
        t(model="m", messages=[])
    with pytest.raises(ProviderHalt, match="consecutive_transport_failures") as halt:
        t(model="m", messages=[])
    assert halt.value.diagnostics["consecutive_failures"] == 3
    with pytest.raises(ProviderHalt):
        t(model="m", messages=[])
    assert len(sent) == 6
    rows = [json.loads(l) for l in (tmp_path / "errors.jsonl").read_text().splitlines()]
    assert [r["consecutive_failures"] for r in rows] == [1, 2, 1, 2, 3] and rows[3]["http_status"] == 503


def test_balance_preflight(monkeypatch):
    seen = []

    def urlopen(req, timeout):
        seen.append(req.full_url)
        body = {"is_available": True, "balance_infos": [{"currency": "CNY", "total_balance": "4.36"}]}
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    assert deepseek_balance(ENV, Decimal("4.00")) == {"is_available": True, "currency": "CNY", "total_balance": "4.36"}
    assert seen == ["https://api.example.test/user/balance"]
    with pytest.raises(LlmEnvError, match="cannot fund") as err:
        deepseek_balance(ENV, Decimal("120"))
    assert "SECRET" not in str(err.value)

    def refused(req, timeout):
        raise OSError("connect failed for sk-SECRET123")

    monkeypatch.setattr("urllib.request.urlopen", refused)
    with pytest.raises(LlmEnvError) as err:
        deepseek_balance(ENV, Decimal("1"))
    assert "SECRET" not in str(err.value)


def test_halt_aborts_collection_instead_of_counting_as_a_provider_failure(tmp_path):
    from experiments.loop_probes import red907_schedule as sched
    from tests.test_red907_schedule import client, plan_for
    from tests.test_repo_blue import WordCounter
    plan = plan_for(tmp_path)
    c, sent = client(plan["max_calls"])
    inner = c.inner._transport

    def failing(**request):
        if len(sent) >= 2:
            sent.append("halted")
            raise ProviderHalt("consecutive_transport_failures", {})
        return inner(**request)

    c.inner._transport = failing
    out = tmp_path / "run"
    with pytest.raises(ProviderHalt):
        sched.collect(plan, out, client=c, counter=WordCounter())
    assert json.loads((out / "collection.json").read_text())["status"] == "aborted"
    assert sent[-1] == "halted" and c.total_calls == 3
