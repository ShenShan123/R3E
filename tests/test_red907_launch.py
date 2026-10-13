"""Frozen launcher safeguards; all transports and balance queries are local fakes."""
import json
import sys
from decimal import Decimal

import pytest

from experiments.loop_probes import red907_launch as launch
from r3e.loop import env
from r3e.loop.red_regression import GENERATE_SYSTEM, ABSTRACT_SYSTEM
from r3e.loop.repo_blue import SYSTEM_SELECT, SYSTEM_REPAIR
from tests.test_repo_blue import WordCounter


def request(system, limit):
    return dict(model='deepseek-v4-flash', max_tokens=limit,
                messages=[{'role': 'system', 'content': system}])


def test_guard_separates_stage_caps_and_preserves_error_diagnostics(tmp_path):
    sent = []
    diag = {'http_status': 402, 'billing': 'unknown', 'error_message': 'Insufficient Balance'}

    def transport(**req):
        sent.append(req)
        if len(sent) == 5:
            raise env.ProviderHalt('auth_or_billing', diag)
        return {'input_tokens': 1, 'output_tokens': req['max_tokens'], 'finish_reason': 'length'}

    g = launch.UsageGuard(transport, WordCounter(), tmp_path)
    for system, cap in [(GENERATE_SYSTEM, 65536), (ABSTRACT_SYSTEM, 32768),
                        (SYSTEM_SELECT, 32768), (SYSTEM_REPAIR, 32768)]:
        g(**request(system, cap))
    with pytest.raises(launch.UsageStop):
        g(**request(SYSTEM_REPAIR, 65536))
    assert len(sent) == 4
    with pytest.raises(env.ProviderHalt):
        g(**request(GENERATE_SYSTEM, 65536))
    rows = [json.loads(s) for s in (tmp_path / 'usage_guard.jsonl').read_text().splitlines()]
    assert rows[-1]['diagnostics'] == diag and rows[-1]['error_type'] == 'ProviderHalt'
    assert rows[-1]['usage_known'] is False


@pytest.mark.parametrize("envelope", ["120", "12"])
def test_unfunded_launch_creates_no_directory_or_transport(tmp_path, monkeypatch, envelope):
    run = tmp_path / 'run'
    monkeypatch.setattr(launch, 'check_bundle', lambda *_: (
        {"spending_envelope_CNY": envelope, "collection_policy": {"direct_only": True}}, WordCounter()))
    monkeypatch.setattr(env, 'load_llm_env', lambda *a, **k: {
        'DEEPSEEK_API_KEY': 'fake', 'DEEPSEEK_BASE_URL': 'https://api.deepseek.com'})
    calls = []

    def balance(e, amount):
        calls.append(amount)
        assert not run.exists()
        raise env.LlmEnvError('insufficient balance')

    monkeypatch.setattr(env, 'deepseek_balance', balance)
    monkeypatch.setattr(env, 'ThinkingTransport', lambda *a, **k: pytest.fail('transport created before funding'))
    monkeypatch.setattr(sys, 'argv', ['launch', '--repo-root', str(tmp_path), 'collect',
                                    '--env-file', 'unused', '--run-dir', str(run)])
    with pytest.raises(env.LlmEnvError):
        launch.main()
    assert calls == [Decimal(envelope)] and not run.exists()


def test_launcher_explicit_model_and_larger_client_authority():
    cfg = env.build_client({}, model_override='deepseek-v4-flash', thinking='low', timeout_seconds=1500,
                           maximum_output_tokens=65536, temperature=0.2, require_seed=False,
                           transport=lambda **k: None).config.public_identity
    client = launch.build_launch_client({'DEEPSEEK_MODEL': 'deepseek-v4-pro'}, {'client_config': cfg, 'max_calls': 237}, lambda **k: None)
    assert client.config.model_id == 'deepseek-v4-flash' and client.max_calls == 237
    assert client.config.maximum_output_tokens == 65536
