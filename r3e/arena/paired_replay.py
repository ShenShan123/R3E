"""Execute parent/candidate on identical frozen replay tuples."""
from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
from typing import Any, Callable

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import canonical_json, read_json

from .manifests import verify_manifest


class ReplayViolation(RuntimeError):
    """Raised when replay results are not paired under identical conditions."""


def paired_replay(
    parent: PolicyState,
    candidate: PolicyState,
    *,
    target_manifest: dict[str, Any],
    non_target_manifest: dict[str, Any],
    seeds: list[int],
    evaluator: Callable[[PolicyState, dict[str, Any], int], dict[str, Any]],
) -> list[dict[str, Any]]:
    verify_manifest(target_manifest)
    verify_manifest(non_target_manifest)
    if target_manifest.get("split") != "target":
        raise ReplayViolation("target manifest has wrong split")
    if non_target_manifest.get("split") != "non_target":
        raise ReplayViolation("non-target manifest has wrong split")
    if not seeds or len(seeds) != len(set(seeds)):
        raise ReplayViolation("promotion seeds must be unique and non-empty")
    target_designs = {
        str(row.get("design") or row.get("design_id")) for row in target_manifest["rows"]
    }
    non_target_designs = {
        str(row.get("design") or row.get("design_id"))
        for row in non_target_manifest["rows"]
    }
    if target_designs & non_target_designs:
        raise ReplayViolation("target/non-target design overlap")
    results = []
    for split, manifest in (("target", target_manifest), ("non_target", non_target_manifest)):
        for case in manifest["rows"]:
            case_id = str(case.get("case_id") or case.get("poison_id") or "")
            design = str(case.get("design") or case.get("design_id") or "")
            if not case_id or not design:
                raise ReplayViolation("replay case must bind case_id and design")
            for seed in seeds:
                pair = {}
                for arm, policy in (("parent", parent), ("candidate", candidate)):
                    row = dict(evaluator(policy, case, seed))
                    if row.get("policy_hash") != policy.policy_hash:
                        raise ReplayViolation("evaluator returned wrong effective policy hash")
                    row.update({
                        "case_id": case_id,
                        "design": design,
                        "seed": seed,
                        "split": split,
                        "arm": arm,
                    })
                    pair[arm] = row
                for field in ("model_id", "budget_hash", "verifier_hash"):
                    if pair["parent"].get(field) != pair["candidate"].get(field):
                        raise ReplayViolation(f"paired replay {field} mismatch")
                results.extend((pair["parent"], pair["candidate"]))
    return results


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--non-target", required=True)
    parser.add_argument("--adapter", required=True, help="module:factory")
    parser.add_argument("--adapter-config")
    parser.add_argument("--seeds", default="11,12,13")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    module_name, factory_name = args.adapter.split(":", 1)
    config = read_json(args.adapter_config) if args.adapter_config else {}
    adapter = getattr(importlib.import_module(module_name), factory_name)(config)
    results = paired_replay(
        PolicyState.from_dict(read_json(args.parent)),
        PolicyState.from_dict(read_json(args.candidate)),
        target_manifest=read_json(args.target),
        non_target_manifest=read_json(args.non_target),
        seeds=[int(value) for value in args.seeds.split(",") if value.strip()],
        evaluator=adapter.replay,
    )
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "".join(canonical_json(row) + "\n" for row in results),
        encoding="utf-8",
    )
    print(json.dumps({"row_count": len(results), "out": str(target)}, indent=2))


if __name__ == "__main__":
    _main()
