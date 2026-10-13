"""Launch a frozen 907 development or screening plan; `check` is offline and never reads credentials."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys


class UsageStop(RuntimeError):
    """Fatal accounting stop, deliberately not a retriable provider error."""

    def __init__(self, reason, diagnostics):
        super().__init__(reason)
        self.diagnostics = diagnostics


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    with Path(path).open('x') as f:
        json.dump(value, f, indent=2)
        f.write('\n')


def checked_path(root, rel):
    p = Path(rel)
    if p.is_absolute() or str(p) != rel or '..' in p.parts:
        raise ValueError('non-normalized archive path')
    dest = root / p
    if root.resolve() not in dest.resolve().parents or dest.is_symlink():
        raise ValueError('archive path escapes its root')
    return dest


def check_archive(directory, repo):
    """Check bytes before importing repo code; do not change the approved plan."""
    manifest = read(directory / 'delivery_manifest.json')
    launch = read(directory / 'launch_manifest.json')
    for files in (manifest['files_sha256'], launch['files_sha256']):
        for rel, expected in files.items():
            if sha(checked_path(directory, rel)) != expected:
                raise ValueError(f'bundle file changed: {rel}')
    plan = read(directory / 'plan.json')
    runtime = plan['runtime']
    executable = Path(sys.executable).resolve()
    if (str(executable) != runtime['executable'] or sha(executable) != runtime['executable_sha256']
            or sys.version != runtime['python_version']):
        raise ValueError('use the exact plan interpreter')
    snapshot = directory / 'code_snapshot'
    expected = plan['code']
    if read(directory / 'code_snapshot_manifest.json')['files_sha256'] != expected:
        raise ValueError('snapshot manifest differs from plan code')
    present = {str(p.relative_to(snapshot)) for p in snapshot.rglob('*') if p.is_file()}
    if present != set(expected):
        raise ValueError('snapshot file set changed')
    for rel, digest in expected.items():
        if sha(checked_path(snapshot, rel)) != digest or sha(checked_path(repo, rel)) != digest:
            raise ValueError(f'frozen code differs: {rel}')
    sys.path.insert(0, str(repo.resolve()))
    from r3e.protocol.hashing import hash_payload
    if hash_payload(plan) != read(directory / 'plan_hash.json')['hash']:
        raise ValueError('plan hash mismatch')
    return plan


def check_bundle(directory, repo):
    plan = check_archive(directory, repo)
    from experiments.loop_probes.red907_schedule import validate_plan
    from r3e.loop.token_count import TokenCounter
    validate_plan(plan)
    counter = TokenCounter()
    if counter.identity != plan['tokenizer']:
        raise ValueError('tokenizer changed')
    expected_cap = 19 if plan['mode'] == 'screening' else 237
    if len(plan['carriers']) != 3 or plan['max_calls'] != expected_cap:
        raise ValueError('launch scope differs from the frozen three-carrier protocol')
    if (plan['red_output_tokens'] != 65536 or plan['abstraction_output_tokens'] != 32768
            or plan['blue_config']['output_reserve'] != 32768 or plan['context_tokens'] != 131072
            or plan['client_config']['maximum_output_tokens'] != 65536):
        raise ValueError('launch stage limits differ from v2 protocol')
    return plan, counter


class UsageGuard:
    """Record every HTTP attempt and stop on the first usage-bound violation.

    The journal includes failed requests with unknown usage. A provider error is
    retried only by the existing scheduler policy. UsageStop aborts collection.
    """

    def __init__(self, transport, counter, directory, *, envelope=Decimal("120"), direct_only=False,
                 allowed_systems=None):
        self.transport, self.counter, self.directory = transport, counter, Path(directory)
        self.calls = 0
        self.stopped = False
        self.known_cost = Decimal(0)
        self.unknown_cost_reserve = Decimal(0)
        self.envelope = Decimal(envelope)
        self.direct_only = direct_only
        self.allowed_systems = dict(allowed_systems) if allowed_systems is not None else None

    def append(self, row):
        with (self.directory / 'usage_guard.jsonl').open('a') as f:
            f.write(json.dumps(row) + '\n')
            f.flush()

    def __call__(self, **request):
        if self.stopped:
            raise UsageStop('guard already stopped; no resume', {})
        from r3e.loop.red_regression import GENERATE_SYSTEM, ABSTRACT_SYSTEM
        from r3e.loop.repo_blue import SYSTEM_SELECT, SYSTEM_REPAIR
        system = request['messages'][0]['content']
        limits = {GENERATE_SYSTEM: 65536, ABSTRACT_SYSTEM: 32768,
                  SYSTEM_SELECT: 32768, SYSTEM_REPAIR: 32768}
        if self.direct_only:
            limits.pop(SYSTEM_SELECT)
        if self.allowed_systems is not None:
            limits = self.allowed_systems
        output_limit = limits.get(system)
        if (output_limit is None or request.get('model') != 'deepseek-v4-flash'
                or request.get('max_tokens') != output_limit):
            raise UsageStop('unexpected model or output limit before HTTP', {})
        counted = self.counter.request(request['messages'])
        if counted + output_limit > 131072:
            raise UsageStop('request exceeds frozen context before HTTP', {})
        # Reserve the full request exposure, including input-count tolerance,
        # before HTTP. A failed request keeps this reservation because billing is unknown.
        exposure = (Decimal((counted * 105) // 100 + 128) * 2 + Decimal(output_limit) * 8) / 1_000_000
        if self.known_cost + self.unknown_cost_reserve + exposure > self.envelope:
            self.stopped = True
            raise UsageStop('insufficient monetary reserve before HTTP', {})
        self.calls += 1
        # Integer rule: tolerate at most 5% plus 128 tokens per response.
        input_limit = (counted * 105) // 100 + 128
        row = {'http_attempt': self.calls, 'counted_request': counted,
               'provider_input_stop_above': input_limit,
               'requested_output_limit': output_limit,
               'time_utc': datetime.now(timezone.utc).isoformat()}
        self.append({**row, 'event': 'started'})
        try:
            response = self.transport(**request)
        except Exception as exc:
            self.unknown_cost_reserve = self.unknown_cost_reserve + exposure
            self.append({**row, 'event': 'transport_error', 'error_type': type(exc).__name__,
                         'usage_known': False, 'reserved_unknown_CNY': str(exposure), 'diagnostics': getattr(exc, 'diagnostics', {})})
            raise
        inp, out = response.get('input_tokens'), response.get('output_tokens')
        reason = None
        if any(type(v) is not int or v < 0 for v in (inp, out)) or inp == 0:
            reason = 'missing_or_invalid_provider_usage'
        elif out > output_limit:
            reason = 'provider_output_exceeds_requested_limit'
        elif inp > input_limit:
            reason = 'provider_input_exceeds_count_by_5pct_plus_128'
        known = all(type(v) is int and v >= 0 for v in (inp, out))
        if known:
            self.known_cost += (Decimal(inp) * 2 + Decimal(out) * 8) / 1_000_000
        if self.known_cost > self.envelope:
            reason = reason or 'observed_peak_uncached_cost_exceeds_envelope'
        self.append({**row, 'event': 'response', 'input_tokens': inp, 'output_tokens': out,
                     'provider_request_id': response.get('provider_request_id'),
                     'finish_reason': response.get('finish_reason'), 'stop_reason': reason,
                     'known_peak_uncached_CNY': str(self.known_cost),
                     'note': 'Conditional tariff calculation; transport errors may have unknown usage.'})
        if reason:
            self.stopped = True
            # Preserve the returned answer before refusing it. It must not become
            # an unarchived Blue repair or a retriable formatting failure.
            save(self.directory / 'usage_stop_response.json', response)
            save(self.directory / 'usage_stop.json', {'reason': reason, 'http_attempt': self.calls})
            diagnostics = {k: response[k] for k in ('input_tokens', 'output_tokens')
                           if type(response.get(k)) is int and response[k] >= 0}
            diagnostics['finish_reason'] = response.get('finish_reason')
            raise UsageStop(reason, diagnostics)
        return response


def build_launch_client(env, plan, guard):
    from r3e.loop.env import build_client
    from r3e.loop.budget import BudgetedClient
    # Never inherit the model from the credential file, even when its last entry
    # is Pro. Leave the credential file itself unchanged.
    inner = build_client(env, model_override='deepseek-v4-flash', thinking='low',
                         timeout_seconds=1500, maximum_output_tokens=65536,
                         temperature=0.2, require_seed=False, transport=guard)
    if inner.config.public_identity != plan['client_config']:
        raise ValueError('launch client differs from frozen plan')
    return BudgetedClient(inner, max_calls=plan["max_calls"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, required=True)
    parser.add_argument('--plan-dir', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('action', choices=('check', 'collect'))
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--run-dir', type=Path, help='new parent; collection is written beneath it')
    args = parser.parse_args()
    plan, counter = check_bundle(args.plan_dir, args.repo_root)
    if args.action == 'check':
        print(json.dumps({'status': 'PASS', 'model_calls': 0, 'snapshot_files': len(plan['code']),
                          'model': plan['client_config']['model_id'], 'max_calls': plan['max_calls']}))
        return 0
    if args.env_file is None or args.run_dir is None:
        parser.error('collect requires --env-file and a fresh --run-dir')
    from experiments.loop_probes.red907_schedule import collect
    return run_collection(args, plan, counter, collect,
                          direct_only=plan['collection_policy']['direct_only'])


def run_collection(args, plan, counter, collect, *, direct_only=True, allowed_systems=None, entrypoint=None):
    """Shared funded launch path; callers supply their frozen protocol and prompt allowlist."""
    from r3e.loop.env import load_llm_env, ThinkingTransport, deepseek_balance
    if args.run_dir.exists():
        raise ValueError('fresh run directory required; no resume')
    env = load_llm_env(args.env_file, names=('DEEPSEEK_API_KEY', 'DEEPSEEK_BASE_URL', 'DEEPSEEK_MODEL'))
    if not env.get('DEEPSEEK_API_KEY'):
        raise ValueError('missing DEEPSEEK_API_KEY')
    if env.get('DEEPSEEK_BASE_URL', '').rstrip('/') not in (
            'https://api.deepseek.com', 'https://api.deepseek.com/v1'):
        raise ValueError('launch requires the official direct billing endpoint')
    balance = deepseek_balance(env, Decimal(plan['spending_envelope_CNY']))
    args.run_dir.mkdir(parents=True, exist_ok=False)
    transport = ThinkingTransport(env, key_name='DEEPSEEK_API_KEY', url_name='DEEPSEEK_BASE_URL',
                                  thinking='low', timeout_seconds=1500,
                                  failure_dir=args.run_dir / 'unparseable',
                                  error_log=args.run_dir / 'provider_errors.jsonl')
    guard = UsageGuard(transport, counter, args.run_dir, envelope=plan['spending_envelope_CNY'],
                       direct_only=direct_only, allowed_systems=allowed_systems)
    client = build_launch_client(env, plan, guard)
    save(args.run_dir / 'launch_identity.json', {
        'client': client.config.public_identity, 'max_calls': plan['max_calls'], 'balance_precheck': balance,
        'plan_hash': read(args.plan_dir / 'plan_hash.json')['hash'],
        'snapshot_manifest_sha256': sha(args.plan_dir / 'code_snapshot_manifest.json'),
        'launcher_sha256': sha(entrypoint or Path(__file__)), 'launch_manifest': read(args.plan_dir / 'launch_manifest.json'),
        'input_usage_guard': 'stop above floor(counted * 1.05) + 128',
        'output_usage_guard': 'stop above the requested stage cap; exact caps are recorded per request',
        'allowed_system_hashes': {hashlib.sha256(k.encode()).hexdigest(): v for k, v in allowed_systems.items()}
            if allowed_systems is not None else None, 'implicit_resume': False})
    try:
        result = collect(plan, args.run_dir / 'collection', client=client, counter=counter)
    except Exception as exc:
        save(args.run_dir / 'launch_stop.json', {'error_type': type(exc).__name__,
             'ledger_calls': client.total_calls, 'http_attempts': guard.calls,
             'usage_stop': guard.stopped, 'resume_allowed': False,
             'diagnostics': getattr(exc, 'diagnostics', {})})
        raise
    print(json.dumps({'status': result['status'], 'calls': client.total_calls,
                      'http_attempts': guard.calls, 'classification': 'separate offline step'}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
