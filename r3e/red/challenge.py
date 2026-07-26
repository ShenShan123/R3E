"""Fixed-seed hardness challenge bound to the active policy."""
from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
from typing import Any, Callable

from r3e.policy.registry_v2 import get_active_policy, load_registry
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import canonical_json, hash_payload, read_json

from .fitness import hardness, hardness_class


class ChallengeViolation(RuntimeError):
    """Raised when challenge seeds, budgets, or policy binding are incomplete."""


def evaluate_challenge(
    policy: PolicyState,
    poison: dict[str, Any],
    *,
    seeds: list[int],
    evaluator: Callable[[PolicyState, dict[str, Any], int], dict[str, Any]],
) -> dict[str, Any]:
    if len(seeds) != len(set(seeds)) or not seeds:
        raise ChallengeViolation("challenge seeds must be non-empty and unique")
    if poison.get("challenged_policy_hash") not in {None, "", policy.policy_hash}:
        raise ChallengeViolation("poison is bound to a different challenged policy")
    results = []
    for seed in seeds:
        row = dict(evaluator(policy, poison, seed))
        if row.get("policy_hash") != policy.policy_hash:
            raise ChallengeViolation("blue result effective policy hash mismatch")
        if int(row.get("seed")) != seed:
            raise ChallengeViolation("blue result seed mismatch")
        results.append(row)
    successes = sum(bool(row.get("oracle_ok")) for row in results)
    result = dict(poison)
    result.update({
        "challenged_policy_id": policy.policy_id,
        "challenged_policy_hash": policy.policy_hash,
        "repair_attempts": len(results),
        "repair_successes": successes,
        "hardness": hardness(successes, len(results)),
        "hardness_class": hardness_class(successes, len(results)),
        "blue_results": results,
        "challenge_budget_hash": hash_payload(policy.budgets),
    })
    result["challenge_result_hash"] = hash_payload(result)
    return result


def _load_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".json":
        payload = read_json(path)
        return payload["rows"] if isinstance(payload, dict) else payload
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy-registry", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--adapter", required=True, help="module:factory")
    parser.add_argument("--adapter-config")
    parser.add_argument("--seeds", default="1,2,3")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    module_name, factory_name = args.adapter.split(":", 1)
    config = read_json(args.adapter_config) if args.adapter_config else {}
    adapter = getattr(importlib.import_module(module_name), factory_name)(config)
    policy = get_active_policy(load_registry(args.policy_registry))
    seeds = [int(value) for value in args.seeds.split(",") if value.strip()]
    results = [
        evaluate_challenge(policy, row, seeds=seeds, evaluator=adapter.evaluate_blue)
        for row in _load_rows(Path(args.manifest))
    ]
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "".join(canonical_json(row) + "\n" for row in results),
        encoding="utf-8",
    )
    print(json.dumps({
        "challenged_policy_id": policy.policy_id,
        "challenged_policy_hash": policy.policy_hash,
        "result_count": len(results),
        "out": str(target),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
