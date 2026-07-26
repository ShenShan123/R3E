"""Guard that the next red round attacks the newly active policy."""
from __future__ import annotations

import argparse
import json

from r3e.policy.registry_v2 import get_active_policy, load_registry


def assert_renewed_challenge_binding(
    registry_path: str,
    expected_policy_hash: str,
) -> dict:
    registry = load_registry(registry_path)
    active = get_active_policy(registry)
    if active.policy_hash != expected_policy_hash:
        raise RuntimeError("renewed challenge expected policy hash is stale")
    return {
        "challenged_policy_id": active.policy_id,
        "challenged_policy_hash": active.policy_hash,
        "registry_hash": registry["registry_hash"],
    }


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy-registry", required=True)
    parser.add_argument("--expected-policy-hash", required=True)
    parser.add_argument("--round-id")
    args = parser.parse_args()
    result = assert_renewed_challenge_binding(
        args.policy_registry, args.expected_policy_hash
    )
    if args.round_id:
        result["round_id"] = args.round_id
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
