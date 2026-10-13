"""No-network tests of the continuous pilot's launch scope and cost guard."""
from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import pytest

from experiments.loop_probes import continuous_launch as launch, red907_launch as shared
from r3e.loop import env, red_continuous as red, repo_blue as rb
from r3e.loop.red_regression import ABSTRACT_SYSTEM, GENERATE_SYSTEM
from tests.test_red907_launch import request
from tests.test_repo_blue import WordCounter


def test_stage_allowlist_and_worst_case_cost(tmp_path):
    class MaximumCounter:
        def request(self, messages):
            return 65536 if messages[0]['content'] == red.SYSTEM else 98304

    sent = []
    counter = MaximumCounter()

    def transport(**req):
        sent.append(req)
        return {'input_tokens': counter.request(req['messages']) * 105 // 100 + 128,
                'output_tokens': req['max_tokens']}

    guard = shared.UsageGuard(transport, counter, tmp_path, envelope='12', allowed_systems=launch.SYSTEM_LIMITS)
    for system, limit in [(red.SYSTEM, 65536)] * 3 + [(rb.SYSTEM_REPAIR, 32768)] * 18:
        guard(**request(system, limit))
    assert len(sent) == 21
    assert guard.known_cost == Decimal(launch.budget()['maximum_CNY']) == Decimal('10.425588')
    for system, cap in [(rb.SYSTEM_SELECT, 32768), (ABSTRACT_SYSTEM, 32768),
                        (GENERATE_SYSTEM, 65536), (rb.SYSTEM_REPAIR, 65536)]:
        with pytest.raises(shared.UsageStop):
            guard(**request(system, cap))
    assert len(sent) == 21
    # The new system must not silently enter the old 907 protocol.
    with pytest.raises(shared.UsageStop):
        shared.UsageGuard(transport, counter, tmp_path)(**request(red.SYSTEM, 65536))


def test_frozen_scope_refuses_expansion_or_model_change():
    plan = dict(carrier_mode='fixed', fixed_carrier_index=0, carriers=[{}, {}, {}],
                anchors=[{'carrier_index': i} for i in range(3)],
                identities=[{'carrier_id': 'zipcpu_wb2axip_axilfetch_32x4'}],
                rounds=3, slots_per_round=1, max_calls=21, client_config=launch.client_identity(),
                cost_bound=launch.budget(), spending_envelope_CNY='12')
    launch.check_scope(plan)
    for key, value in [('max_calls', 22), ('rounds', 4), ('spending_envelope_CNY', '120'),
                       ('carrier_mode', 'rotate'), ('fixed_carrier_index', 1)]:
        with pytest.raises(ValueError, match='scope'):
            launch.check_scope({**plan, key: value})
    changed = deepcopy(plan)
    changed['client_config']['model_id'] = 'deepseek-v4-pro'
    with pytest.raises(ValueError, match='scope'):
        launch.check_scope(changed)


def test_funding_before_creating_continuous_run(tmp_path, monkeypatch):
    args = SimpleNamespace(env_file=tmp_path / 'unused', run_dir=tmp_path / 'run')
    monkeypatch.setattr(env, 'load_llm_env', lambda *a, **kw: {
        'DEEPSEEK_API_KEY': 'fake', 'DEEPSEEK_BASE_URL': 'https://api.deepseek.com'})

    def balance(e, amount):
        assert amount == Decimal('12') and not args.run_dir.exists()
        raise env.LlmEnvError('insufficient balance')

    monkeypatch.setattr(env, 'deepseek_balance', balance)
    monkeypatch.setattr(env, 'ThinkingTransport', lambda *a, **k: pytest.fail('transport before funding'))
    with pytest.raises(env.LlmEnvError):
        shared.run_collection(args, {'spending_envelope_CNY': '12'}, WordCounter(),
                              lambda *a, **kw: pytest.fail('collection before funding'),
                              allowed_systems=launch.SYSTEM_LIMITS)
    assert not args.run_dir.exists()
