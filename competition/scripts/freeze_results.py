#!/usr/bin/env python3
"""Fail-closed freezer for an explicitly authorized live raw run."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any

from competition.services.common import git_provenance, payload_hash


ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "competition" / "results" / "frozen"
EXPECTED_CASES = {"demo_counter", "demo_fsm", "demo_shift"}


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _require(value: Any, message: str) -> Any:
    if value is None or value == "":
        raise SystemExit(message)
    return value


_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise SystemExit(f"{label} must be an exact sha256 digest")
    return value


def _validate_raw(raw: dict[str, Any]) -> dict[str, Any]:
    if raw.get("schema_version") != "r3e-aic-raw-run-summary-v1":
        raise SystemExit("input is not an R3E-AIC raw run summary")
    if raw.get("mode") != "live":
        raise SystemExit("refusing to freeze guided-demo or deterministic-fixture results")
    runs = raw.get("runs")
    if (
        not isinstance(runs, list)
        or len(runs) != len(EXPECTED_CASES)
        or any(not isinstance(row, dict) for row in runs)
        or {row.get("case_id") for row in runs} != EXPECTED_CASES
        or len({row.get("case_id") for row in runs}) != len(EXPECTED_CASES)
    ):
        raise SystemExit("expected exactly the three competition cases before freezing")
    if raw.get("case_count") != len(EXPECTED_CASES):
        raise SystemExit("raw summary case_count is not exactly three")
    raw_git = raw.get("git") or {}
    current_git = git_provenance(ROOT)
    if raw_git.get("dirty") is not False or current_git.get("dirty") is not False:
        raise SystemExit("refusing to freeze a dirty workspace")
    if not _require(raw_git.get("commit"), "raw summary is missing git commit") == current_git.get("commit"):
        raise SystemExit("raw summary commit differs from current checkout")
    first_provider_hash = ""
    for row in runs:
        if row.get("mode") != "live":
            raise SystemExit(f"{row.get('case_id')}: run mode is not live")
        provider = row.get("provider") or {}
        if not isinstance(provider, dict) or provider.get("live_model_call") is not True:
            raise SystemExit(f"{row.get('case_id')}: provider is not a live model call")
        identity = provider.get("config_identity") or {}
        if not isinstance(identity, dict):
            raise SystemExit(f"{row.get('case_id')}: provider config identity is invalid")
        _require(provider.get("id"), f"{row.get('case_id')}: missing provider identity")
        _require(identity.get("model_id"), f"{row.get('case_id')}: missing model identity")
        _require(identity.get("model_version"), f"{row.get('case_id')}: missing model version")
        provider_hash = payload_hash(provider)
        if first_provider_hash and provider_hash != first_provider_hash:
            raise SystemExit("provider identity differs across competition cases")
        first_provider_hash = first_provider_hash or provider_hash
        seed = row.get("run_seed")
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise SystemExit(f"{row.get('case_id')}: missing frozen seed")
        if int(row.get("candidate_budget", 0)) != 3:
            raise SystemExit(f"{row.get('case_id')}: candidate budget is not frozen at 3")
        accepted = row.get("accepted_candidate_ids")
        if not isinstance(accepted, list) or len(accepted) != 1 or not isinstance(accepted[0], str):
            raise SystemExit(f"{row.get('case_id')}: expected exactly one accepted candidate")
        evidence = row.get("candidate_results") or []
        if (
            not isinstance(evidence, list)
            or len(evidence) != 3
            or any(not isinstance(candidate, dict) for candidate in evidence)
            or len({candidate.get("candidate_id") for candidate in evidence}) != 3
        ):
            raise SystemExit(f"{row.get('case_id')}: candidate portfolio is incomplete")
        accepted_candidate = next(
            (candidate for candidate in evidence if candidate.get("candidate_id") == accepted[0]),
            None,
        )
        if accepted_candidate is None or accepted_candidate.get("accepted") is not True:
            raise SystemExit(f"{row.get('case_id')}: accepted candidate is not an accepted result")
        for candidate in evidence:
            result_hash = _digest(
                candidate.get("result_hash"),
                f"{row.get('case_id')}: result hash",
            )
            case_evidence = candidate.get("case_evidence") or {}
            if not isinstance(case_evidence, dict):
                raise SystemExit(f"{row.get('case_id')}: case evidence is invalid")
            for field in ("case_hash", "buggy_sha256", "reference_sha256", "testbench_sha256"):
                _digest(case_evidence.get(field), f"{row.get('case_id')}: case hash {field}")
            toolchain = candidate.get("toolchain") or {}
            if not isinstance(toolchain, dict):
                raise SystemExit(f"{row.get('case_id')}: toolchain identity is invalid")
            for tool in ("python", "iverilog", "vvp", "yosys"):
                item = toolchain.get(tool) or {}
                if item.get("available") is not True or not item.get("version"):
                    raise SystemExit(f"{row.get('case_id')}: incomplete toolchain identity for {tool}")
            verification = candidate.get("verification") or {}
            if verification.get("schema_version") != "r3e-aic-verification-result-v2":
                raise SystemExit(f"{row.get('case_id')}: missing v2 verification result")
            if verification.get("result_hash") != result_hash:
                raise SystemExit(f"{row.get('case_id')}: candidate/result hash mismatch")
            if candidate.get("accepted") is not verification.get("accepted"):
                raise SystemExit(f"{row.get('case_id')}: candidate acceptance mismatch")
            stages = verification.get("stages")
            if (
                not isinstance(stages, list)
                or [stage.get("name") for stage in stages]
                != ["parse", "scope", "compile", "simulation", "oracle", "structural_check", "repeatability"]
            ):
                raise SystemExit(f"{row.get('case_id')}: verification stages are incomplete")
            expected_acceptance = all(stage.get("status") == "pass" for stage in stages)
            if verification.get("accepted") is not expected_acceptance:
                raise SystemExit(f"{row.get('case_id')}: verification acceptance is not gate-derived")
            if payload_hash({key: value for key, value in verification.items() if key != "result_hash"}) != result_hash:
                raise SystemExit(f"{row.get('case_id')}: verification result hash is not reconstructable")
        _digest(row.get("descriptor_hash"), f"{row.get('case_id')}: descriptor hash")
        _digest(row.get("allocation_plan_hash"), f"{row.get('case_id')}: allocation plan hash")
    return current_git


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="raw summary.json produced by run-demo --mode live")
    args = parser.parse_args()
    source = Path(args.input).resolve()
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read raw summary: {exc}") from exc
    if not isinstance(raw, dict):
        raise SystemExit("raw summary must be an object")
    current_git = _validate_raw(raw)
    rows = sorted(raw["runs"], key=lambda row: row["case_id"])
    provider = rows[0]["provider"]
    cases: dict[str, Any] = {}
    toolchains: dict[str, Any] = {}
    for row in rows:
        accepted_id = row["accepted_candidate_ids"][0]
        accepted = next(item for item in row["candidate_results"] if item["candidate_id"] == accepted_id)
        verification = accepted["verification"]
        candidate = next(item for item in row["candidate_results"] if item["candidate_id"] == accepted_id)
        cases[row["case_id"]] = {
            "case_evidence": accepted["case_evidence"],
            "provider": provider,
            "budget": {
                "candidate_budget": row["candidate_budget"],
                "run_seed": row["run_seed"],
            },
            "candidate": {
                "candidate_id": accepted_id,
                "replacement_sha256": candidate.get("replacement_sha256", verification.get("candidate_sha256")),
                "semantic_signature_hash": candidate.get("semantic_signature_hash", ""),
                "changed_lines": candidate.get("scope", {}).get("changed_lines", 0),
            },
            "verification": {
                stage["name"]: stage["status"]
                for stage in verification.get("stages", [])
            },
            "result_hash": accepted["result_hash"],
        }
        toolchains[row["case_id"]] = accepted["toolchain"]
    source_hash = payload_hash(raw)
    write_json(TARGET / "demo" / "cases.json", {
        "schema_version": "r3e-aic-demo-cases-v2",
        "authority": "competition-demo-live-replay",
        "available": True,
        "source_summary_hash": source_hash,
        "cases": cases,
    })
    write_json(TARGET / "demo" / "toolchain.json", {
        "schema_version": "r3e-aic-demo-toolchain-v2",
        "available": True,
        "source_summary_hash": source_hash,
        "per_case": toolchains,
    })
    write_json(TARGET / "demo" / "manifest.json", {
        "schema_version": "r3e-aic-frozen-run-v2",
        "authority": "competition-demo-live-replay",
        "available": True,
        "status": "verified_run_replay",
        "git": current_git,
        "dataset": {
            "cases": {case_id: item["case_evidence"] for case_id, item in cases.items()},
            "manifest_hash": payload_hash({case_id: item["case_evidence"] for case_id, item in cases.items()}),
        },
        "provider": provider,
        "budget": {
            "candidate_budget": 3,
            "run_seeds": {row["case_id"]: row["run_seed"] for row in rows},
        },
        "cases": cases,
        "source_summary_hash": source_hash,
        "result_hash": payload_hash(cases),
        "claim_boundary": "This is three live competition-case replays, not benchmark generalization or evolution/memory evidence.",
    })
    print(f"frozen demo evidence written to {TARGET / 'demo'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
