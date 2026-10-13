"""One-attempt screen: fixed slots, no retries, no localization, no 2-of-3 claim."""
import json
from dataclasses import replace
from decimal import Decimal

import pytest

from experiments.loop_probes import red907_schedule as sched, red907_measure as measure, red907_launch as launch
from r3e.loop import repo_blue as rb
from r3e.loop.budget import BudgetedClient
from r3e.loop.env import build_client
from r3e.loop.fakes import _reply
from r3e.loop.red_regression import GENERATE_SYSTEM
from r3e.providers.openai_compatible import OpenAICompatibleProviderViolation
from tests.test_red907_schedule import carriers, client
from tests.test_red907_measure import setup_task, TARGET
from tests.test_repo_blue import WordCounter, runner, scripted


def screen_plan(tmp_path):
    items, packet_path = carriers(tmp_path)
    c, _ = client(19)
    return sched.make_plan(items, packet_path, counter=WordCounter(), client_config=c.config.public_identity,
                           seed=7, mode='screening')


def test_screen_consumes_19_calls_with_equal_slot_allowances_and_no_confirmation(tmp_path):
    plan = screen_plan(tmp_path)
    assert plan['max_calls'] == 19 and plan['arm_caps'] == {'ordinary': 6, 'automatic': 7, 'manual': 6}
    assert all(s['reserved_calls'] == 2 for s in plan['slots'])
    c, sent = client(19)
    out = tmp_path / 'run'
    result = sched.collect(plan, out, client=c, counter=WordCounter())
    assert result['status'] == 'complete' and c.total_calls == len(sent) == 19
    assert {a: r['total_calls'] for a, r in result['per_arm'].items()} == plan['arm_caps']
    assert not list(out.rglob('classification.json'))
    for p in out.rglob('blue_config.json'):
        config = json.loads(p.read_text())
        assert config['encounters'] == config['runner']['budget_k'] == config['reserved_calls'] == 1
        assert config['runner']['max_infra_retries'] == 0 and config['direct_only']
    classified = sched.classify_collection(out)  # synthetic evaluator only
    assert len(classified) == 9
    for row in classified:
        s = row['measurement_summary']
        assert s['categories'] == {'public_pass_hidden_pass': 1}
        assert s['confirmed_target_challenge'] is None and s['primary_signal_candidates'] == 0


def test_screen_refuses_policy_tampering_before_collection(tmp_path):
    plan = screen_plan(tmp_path)
    for key, value in [('max_calls', 21), ('spending_envelope_CNY', '120'),
                       ('collection_policy', {**plan['collection_policy'], 'proposal_calls': 2})]:
        modified = {**plan, key: value}
        with pytest.raises(ValueError, match='policy/caps'):
            sched.validate_plan(modified)
    plan['blue_config']['max_infra_retries'] = 1
    with pytest.raises(ValueError, match='policy/caps'):
        sched.validate_plan(plan)


def test_screen_network_loss_uses_one_call_and_no_replacement_slot(tmp_path):
    plan = screen_plan(tmp_path)
    base, _ = client(19)
    transport = base.inner._transport
    sent = []

    def fail_first(**req):
        sent.append(req)
        if len(sent) == 1:
            raise OpenAICompatibleProviderViolation('connection reset')
        return transport(**req)

    c = BudgetedClient(build_client({'DEEPSEEK_MODEL': 'fake-local'}, transport=fail_first,
                                    maximum_output_tokens=65536), max_calls=19)
    out = tmp_path / 'run'
    result = sched.collect(plan, out, client=c, counter=WordCounter())
    rows = [json.loads(s) for s in (out / 'slots.jsonl').read_text().splitlines()]
    assert result['scheduled_slots'] == len(rows) == 9
    assert rows[0]['status'] == 'red_provider_failure' and rows[0]['calls'] == 1
    assert len(sent) == c.total_calls == 18  # one missing Blue encounter, no retries


def test_screen_context_route_refused_before_blue_and_collection_continues(tmp_path, monkeypatch):
    plan = screen_plan(tmp_path)
    original = rb.setup

    def too_large(*a, **k):
        return replace(original(*a, **k), route='select', rtl_tokens=40001)

    monkeypatch.setattr(rb, 'setup', too_large)
    c, sent = client(19)
    out = tmp_path / 'run'
    result = sched.collect(plan, out, client=c, counter=WordCounter())
    rows = [json.loads(s) for s in (out / 'slots.jsonl').read_text().splitlines()]
    assert result['status'] == 'complete' and len(rows) == 9 and c.total_calls == 10
    assert all(r['status'] == 'blue_context_limit' and r['blue_calls'] == 0 for r in rows)
    assert not any('files' in user for user in sent)


@pytest.mark.parametrize('kind', ['target', 'hidden', 'untouched', 'no_answer', 'provider'])
def test_screen_one_attempt_endpoint_signal_needs_review(tmp_path, kind):
    task, ev = setup_task(tmp_path)
    reply = {'target': TARGET,
             'hidden': {'edits': [{'file': 'rtl/toy.v', 'find': 'assign y = a;', 'replace': "assign y = 4'd6;"}], 'edit': 'e'},
             'untouched': {'edits': [{'file': 'rtl/toy.v', 'find': 'assign y = a;', 'replace': 'assign y = a;'}], 'edit': 'e'},
             'no_answer': 'NO_ANSWER',
             'provider': OpenAICompatibleProviderViolation('connection reset')}[kind]
    transport, sent = scripted([reply])
    _, c = runner(transport, max_calls=1)
    out = tmp_path / 'blue'
    measure.run_blue(task, out, client=c, counter=WordCounter(), cfg=rb.RepoBlueConfig(budget_k=1, max_infra_retries=0),
                     seed=7, encounters=1, direct_only=True, endpoint_protocol='screening')
    classified = measure.classify(out, task, ev, tmp_path)
    assert len(sent) == 1 and classified['summary']['confirmed_target_challenge'] is None
    row = classified['encounters'][0]
    assert row['primary_signal_candidate'] == (kind == 'target')
    if kind == 'target':
        assert row['category'] == 'public_unrepaired_after_one_attempt'
        assert row['review']['decision'] == 'PENDING'
        assert classified['summary']['reviewed_primary_signals'] == 0
        items = json.loads((out / 'review_items.json').read_text())
        (out / 'review_decisions.json').write_text(json.dumps({items[0]['item']: {'decision': 'TARGET'}}))
        reviewed = measure.classify(out, task, ev, tmp_path)['summary']
        assert reviewed['reviewed_primary_signals'] == 1 and reviewed['confirmed_target_challenge'] is None
    elif kind == 'hidden':
        assert row['category'] == 'public_pass_hidden_fail'
    elif kind == 'provider':
        assert row['category'] == 'infrastructure'


def test_screen_guard_forbids_selection_and_reserves_unknown_billing(tmp_path):
    from r3e.loop.repo_blue import SYSTEM_SELECT
    calls = []

    def transport(**req):
        calls.append(req)
        raise OpenAICompatibleProviderViolation('connection reset')

    g = launch.UsageGuard(transport, WordCounter(), tmp_path, envelope=Decimal('0.53'), direct_only=True)
    def req(system, cap):
        return dict(model='deepseek-v4-flash', max_tokens=cap, messages=[{'role': 'system', 'content': system}])
    with pytest.raises(launch.UsageStop):
        g(**req(SYSTEM_SELECT, 32768))
    assert not calls
    with pytest.raises(OpenAICompatibleProviderViolation):
        g(**req(GENERATE_SYSTEM, 65536))
    assert g.unknown_cost_reserve > 0
    with pytest.raises(launch.UsageStop, match='monetary reserve'):
        g(**req(GENERATE_SYSTEM, 65536))
    assert len(calls) == 1
