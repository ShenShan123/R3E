"""Frozen three-arm collection schedule for the 907 development experiment.

This module has no credential loading or paid CLI. Plan creation and validation
are offline. collect requires an explicitly constructed fresh shared client.
Evaluator bytes are used only for integrity checks until classify_collection.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import random
import sys
from typing import Any

from experiments.loop_probes import red907, red907_measure as measure, witness_matrix as wm
from r3e.loop.budget import BudgetedClient, CallBudgetExceeded
from r3e.loop.red_regression import (VERSION, RegressionGenerator, arm_context, save_new, validate_packet)
from r3e.loop.repo_blue import RepoBlueConfig
from r3e.protocol.hashing import hash_payload
from r3e.providers.openai_compatible import OpenAICompatibleProviderViolation

ARMS = ("ordinary", "automatic", "manual")
SCHEDULE_VERSION = "red907_schedule_v5"
ROOT = Path(__file__).resolve().parents[2]


def collection_policy(mode: str) -> dict:
    if mode == "screening":
        return {"proposal_calls": 1, "blue_encounters": 1, "blue_attempts": 1,
                "provider_retries": 0, "direct_only": True, "endpoint_protocol": "screening"}
    if mode == "development":
        return {"proposal_calls": 2, "blue_encounters": 3, "blue_attempts": 3,
                "provider_retries": 2, "direct_only": False, "endpoint_protocol": "confirmation"}
    raise ValueError("unknown collection mode")


def _json(path):
    return json.loads(Path(path).read_text())


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def runtime_identity() -> dict:
    """Freeze the actual interpreter, independently of tokenizer data identity."""
    executable = Path(sys.executable).resolve()
    return {"executable": str(executable), "executable_sha256": _sha(executable),
            "python_version": sys.version, "implementation": sys.implementation.name,
            "cache_tag": sys.implementation.cache_tag}


def code_identity() -> dict:
    files = sorted({*ROOT.glob("r3e/loop/*.py"), *ROOT.glob("r3e/providers/*.py"),
                    *ROOT.glob("r3e/knowledge/verilog*.py"), ROOT / "r3e/protocol/hashing.py",
                    *(Path(m.__file__) for m in (red907, measure, wm)),
                    ROOT / "experiments/loop_probes/carrier_task.py",
                    ROOT / "experiments/loop_probes/red907_launch.py", Path(__file__)})
    return {str(f.relative_to(ROOT)): _sha(f) for f in files}


def _evaluator_identity(freeze_path: Path, root: Path) -> dict:
    freeze = _json(freeze_path)
    files = freeze["files"]
    if not files:
        raise ValueError("empty evaluator freeze")
    parents = {Path(f).parent for f in files}
    if len(parents) != 1:
        raise ValueError("v1 evaluator must occupy one frozen directory")
    directory = red907._inside(root, next(iter(files))).parent
    present = {str(f.relative_to(root)) for f in directory.rglob("*") if f.is_file()}
    if present != set(files):
        raise ValueError("evaluator file set differs from freeze")
    for rel, expected in files.items():
        if hash_payload(red907._inside(root, rel).read_text()) != expected:
            raise ValueError("evaluator file changed")
    return {"freeze_sha256": _sha(freeze_path), "files": dict(files)}


def _carrier_identity(item: dict) -> dict:
    pkg = Path(item["package"])
    freeze = _json(item["suite_freeze"])
    public = red907.public_material(pkg, freeze)
    man = wm.load(pkg)
    source = man.get("source", {})
    if any(not isinstance(source.get(k), str) or not source[k].strip()
           for k in ("repository", "commit", "license", "family")):
        raise ValueError("carrier needs recorded source provenance and family")
    if any("ibex" in source[k].lower() for k in ("repository", "family")):
        raise ValueError("source family cannot also be a transfer carrier")
    return {"carrier_id": public["carrier_id"], "public_hash": hash_payload(public), "source": source,
            "suite_freeze_sha256": _sha(item["suite_freeze"]),
            "evaluator": _evaluator_identity(Path(item["evaluator_freeze"]), Path(item["evaluator_root"]))}


def make_plan(carriers: list[dict], packet_path: Path, *, counter, client_config: dict,
              seed: int, blue: RepoBlueConfig | None = None, per_arm_cap: int | None = None,
              red_output_tokens: int = 65536, abstraction_output_tokens: int = 32768,
              mode: str = "development") -> dict:
    """Record all slots before collection. One-carrier plans are inspectable, not runnable.

    Carrier items contain package, suite_freeze, evaluator_freeze, evaluator_root.
    Development reserves selection calls because mutations can grow. Screening
    is direct-only with one proposal and one Blue attempt; it never buys selection
    or replacement work from unused allowance.
    """
    if not carriers or type(seed) is not int:
        raise ValueError("nonempty carriers and integer seed required")
    if any(set(c) != {"package", "suite_freeze", "evaluator_freeze", "evaluator_root"} for c in carriers):
        raise ValueError("carrier path fields mismatch")
    policy = collection_policy(mode)
    blue = blue or RepoBlueConfig(budget_k=policy["blue_attempts"], max_infra_retries=policy["provider_retries"])
    if blue.budget_k != policy["blue_attempts"] or blue.max_infra_retries != policy["provider_retries"]:
        raise ValueError("Blue attempts/retries differ from collection policy")
    if mode == "screening" and per_arm_cap is not None:
        raise ValueError("screening arm caps are fixed by its schedule")
    if any(type(n) is not int or not 0 < n <= client_config["maximum_output_tokens"]
           or n >= blue.context_tokens for n in (red_output_tokens, abstraction_output_tokens, blue.output_reserve)):
        raise ValueError("output reserve exceeds client authority")
    packet_path = packet_path.resolve()
    validate_packet(_json(packet_path))
    items = [{k: str(Path(v).resolve()) for k, v in item.items()} for item in carriers]
    identities = [_carrier_identity(item) for item in items]
    ids = [i["carrier_id"] for i in identities]
    if (len(set(ids)) != len(ids) or len({i["source"]["family"] for i in identities}) != len(ids)
            or len({i["source"]["repository"].rstrip("/").lower() for i in identities}) != len(ids)):
        raise ValueError("carrier IDs and declared source families must be distinct")
    qualified = []
    for item in items:
        q = wm.qualify(Path(item["package"]), _json(item["suite_freeze"]))
        if not q["qualified"]:
            raise ValueError("carrier public qualification failed")
        qualified.append({"carrier_id": q["carrier_id"], "package_files": q["package_files"], "tool": q["tool"]})
    # A deterministic rotation balances ordinal positions across three carriers.
    order = list(range(len(items)))
    random.Random(seed).shuffle(order)
    arms = list(ARMS)
    random.Random(seed + 1).shuffle(arms)
    reserve = policy["proposal_calls"] + policy["blue_encounters"] * blue.worst_case_calls(
        "direct" if policy["direct_only"] else "select")
    cap = 1 + len(items) * reserve if per_arm_cap is None else per_arm_cap
    if type(cap) is not int or cap < 1:
        raise ValueError("positive integer per-arm cap required")
    extraction = {a: int(a == "automatic" or mode == "development") for a in ARMS}
    arm_caps = ({a: len(items) * reserve + extraction[a] for a in ARMS}
                if mode == "screening" else {a: cap for a in ARMS})
    slots = []
    for position, carrier in enumerate(order):
        rotated = arms[position % 3:] + arms[:position % 3]
        for arm in rotated:
            # Same carrier seed across arms; provider seeds do not promise deterministic samples.
            slot_seed = int(hash_payload([seed, ids[carrier]])[7:15], 16) % 1_000_000
            slots.append({"slot_id": f"slot_{len(slots):02d}", "arm": arm, "carrier_index": carrier,
                          "carrier_id": ids[carrier], "seed": slot_seed, "reserved_calls": reserve})
    return {"version": SCHEDULE_VERSION, "generator_version": VERSION,
            "mode": mode, "collection_policy": policy,
            "ready_for_collection": len(items) == 3,
            "scope": f"three-carrier {mode}" if len(items) == 3 else "incomplete carrier plan; offline only",
            "seed": seed, "carriers": items, "identities": identities, "qualifications": qualified,
            "packet_path": str(packet_path), "packet_sha256": _sha(packet_path),
            "client_config": client_config, "tokenizer": counter.identity, "runtime": runtime_identity(),
            "blue_config": asdict(blue),
            "red_output_tokens": red_output_tokens, "abstraction_output_tokens": abstraction_output_tokens,
            "proposal_format": "edits_only", "context_tokens": blue.context_tokens,
            "per_arm_cap": cap if mode == "development" else None, "arm_caps": arm_caps,
            "max_calls": sum(arm_caps.values()), "slots": slots,
            "extraction_reservations": extraction, "spending_envelope_CNY": "12" if mode == "screening" else "120",
            "retry_policy": {"red_provider": 0, "extraction": 0, "blue_per_encounter": policy["provider_retries"],
                             "malformed_proposal_extra_calls": policy["proposal_calls"] - 1},
            "budget_bounds": {"generation": policy["proposal_calls"] * len(slots), "automatic_extraction": 1,
                "blue": len(slots) * policy["blue_encounters"] * blue.worst_case_calls(
                    "direct" if policy["direct_only"] else "select")},
            "code": code_identity(), "authorization": "plan only; no model calls or budget allocation"}


def validate_plan(plan: dict) -> None:
    if (plan["version"] != SCHEDULE_VERSION or plan["generator_version"] != VERSION
            or plan["code"] != code_identity()):
        raise ValueError("experiment code changed; create a new plan before collection")
    if plan.get("runtime") != runtime_identity():
        raise ValueError("Python runtime changed; use the frozen interpreter and environment")
    if _sha(plan["packet_path"]) != plan["packet_sha256"]:
        raise ValueError("source packet changed")
    if [_carrier_identity(c) for c in plan["carriers"]] != plan["identities"]:
        raise ValueError("carrier or evaluator material changed")
    for item, qualified in zip(plan["carriers"], plan["qualifications"], strict=True):
        if wm.package_files(Path(item["package"])) != qualified["package_files"]:
            raise ValueError("qualified package changed")
    cfg = RepoBlueConfig(**plan["blue_config"])
    policy = collection_policy(plan["mode"])
    reserve = policy["proposal_calls"] + policy["blue_encounters"] * cfg.worst_case_calls(
        "direct" if policy["direct_only"] else "select")
    extraction = {a: int(a == "automatic" or plan["mode"] == "development") for a in ARMS}
    caps = ({a: len(plan["carriers"]) * reserve + extraction[a] for a in ARMS}
            if plan["mode"] == "screening" else {a: plan["per_arm_cap"] for a in ARMS})
    if (plan["collection_policy"] != policy or cfg.budget_k != policy["blue_attempts"]
            or cfg.max_infra_retries != policy["provider_retries"] or plan["extraction_reservations"] != extraction
            or plan["arm_caps"] != caps or plan["max_calls"] != sum(caps.values())
            or plan["spending_envelope_CNY"] != ("12" if plan["mode"] == "screening" else "120")
            or plan["retry_policy"] != {"red_provider": 0, "extraction": 0,
                "blue_per_encounter": policy["provider_retries"], "malformed_proposal_extra_calls": policy["proposal_calls"] - 1}):
        raise ValueError("collection policy/caps changed")
    if (plan.get("proposal_format") != "edits_only" or plan["context_tokens"] != cfg.context_tokens
            or any(type(n) is not int or not 0 < n <= plan["client_config"]["maximum_output_tokens"]
                   or n >= cfg.context_tokens for n in
                   (plan["red_output_tokens"], plan["abstraction_output_tokens"], cfg.output_reserve))):
        raise ValueError("invalid stage output budgets or proposal format")
    expected = {(a, i) for a in ARMS for i in range(len(plan["carriers"]))}
    if (len(plan["slots"]) != len(expected)
            or {(s["arm"], s["carrier_index"]) for s in plan["slots"]} != expected
            or len({s["slot_id"] for s in plan["slots"]}) != len(expected)
            or any(s["reserved_calls"] != reserve for s in plan["slots"])
            or any(s["carrier_id"] != plan["identities"][s["carrier_index"]]["carrier_id"] for s in plan["slots"])):
        raise ValueError("scheduled slot set/reservation mismatch")


class ArmBudget(BudgetedClient):
    """Per-arm ceiling backed by the SAME root budget and ledger; no budget loans."""
    def __init__(self, shared: BudgetedClient, cap: int):
        super().__init__(shared.inner, max_calls=cap)
        self.shared = shared
        self.output_caps = shared.output_caps

    def complete_json(self, **kwargs):
        if self.total_calls >= self.max_calls:
            raise CallBudgetExceeded("per-arm cap reached")
        phase = self.phase
        before = (self.shared.calls[phase], self.shared.failed[phase], dict(self.shared.tokens[phase]))
        try:
            with self.shared.in_phase(phase):
                return self.shared.complete_json(**kwargs)
        finally:
            self.calls[phase] += self.shared.calls[phase] - before[0]
            self.failed[phase] += self.shared.failed[phase] - before[1]
            for kind in ("input", "output"):
                self.tokens[phase][kind] += self.shared.tokens[phase][kind] - before[2][kind]


def _append(path: Path, row: dict):
    with path.open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()


def collect(plan: dict, out: Path, *, client: BudgetedClient, counter) -> dict:
    """Fresh collection only. No evaluator execution and no result-dependent slots."""
    validate_plan(plan)
    if not plan["ready_for_collection"] or len(plan["carriers"]) != 3:
        raise ValueError("three qualified independent carriers are required")
    if (client.total_calls or client.max_calls != plan["max_calls"]
            or client.config.public_identity != plan["client_config"] or counter.identity != plan["tokenizer"]):
        raise ValueError("fresh client, frozen call cap/configuration and tokenizer required")
    policy = plan["collection_policy"]
    out.mkdir(parents=True, exist_ok=False)
    save_new(out / "plan.json", plan)
    save_new(out / "plan_hash.json", {"hash": hash_payload(plan)})
    for name in ("calls", "events", "slots"):
        (out / f"{name}.jsonl").touch(exist_ok=False)
    # The shared client permits larger Red proposals. Keep both Blue request
    # phases at their independently frozen reserve, including provider retries.
    client.output_caps.update({p: plan["blue_config"]["output_reserve"]
                               for p in ("localization", "blue_inference")})
    budgets = {a: ArmBudget(client, plan["arm_caps"][a]) for a in ARMS}
    seen = {a: set() for a in ARMS}
    packet = _json(plan["packet_path"])
    cfg = RepoBlueConfig(**plan["blue_config"])
    generators = {a: RegressionGenerator(budgets[a], counter, context_tokens=plan["context_tokens"],
                                         output_tokens=plan["red_output_tokens"],
                                         abstraction_output_tokens=plan["abstraction_output_tokens"]) for a in ARMS}
    previous_callback = client.on_call
    label = {}

    def ledger(entry):
        _append(out / "calls.jsonl", {**entry, **label})
        if previous_callback is not None:
            previous_callback(entry)

    client.on_call = ledger
    abstraction, abstraction_attempted = None, False
    rows, status = [], "complete"
    try:
        for slot in plan["slots"]:
            validate_plan(plan)
            arm = slot["arm"]
            budget = budgets[arm]
            extra = plan["extraction_reservations"][arm] if arm != "automatic" or not abstraction_attempted else 0
            row = {**slot, "calls_before": client.total_calls, "extraction_calls": 0}
            if (budget.max_calls - budget.total_calls < slot["reserved_calls"] + extra
                    or client.max_calls - client.total_calls < slot["reserved_calls"] + extra):
                row.update(status="not_started_budget", calls=0)
            elif arm == "automatic" and abstraction_attempted and abstraction.get("status") != "frozen":
                row.update(status="not_started_abstraction_unavailable", calls=0)
            else:
                label.update(arm=arm, slot_id=slot["slot_id"], stage="generation")
                _append(out / "events.jsonl", {"event": "slot_start", **row})
                if arm == "automatic" and not abstraction_attempted:
                    abstraction_attempted = True
                    label["stage"] = "abstraction"
                    extraction_start = client.total_calls
                    try:
                        abstraction = generators[arm].extract(packet, seed=plan["seed"], directory=out / "extraction")
                    except OpenAICompatibleProviderViolation:
                        abstraction = {"status": "provider_failure"}
                    row["extraction_calls"] = client.total_calls - extraction_start
                    _append(out / "events.jsonl", {"event": "abstraction_end", "status": abstraction["status"],
                            "calls": budget.total_calls, "arm": arm})
                if arm == "automatic" and abstraction.get("status") != "frozen":
                    row.update(status="not_started_abstraction_unavailable")
                else:
                    context = arm_context(arm, **({} if arm == "ordinary" else {"packet": packet}),
                                          **({"abstraction": abstraction} if arm == "automatic" else {}))
                    item = plan["carriers"][slot["carrier_index"]]
                    directory = out / slot["slot_id"]
                    label["stage"] = "generation"
                    try:
                        generated = red907.generate_slot(generators[arm], Path(item["package"]),
                            _json(item["suite_freeze"]), context, directory, seed=slot["seed"], seen=seen[arm],
                            proposal_calls=policy["proposal_calls"])
                    except OpenAICompatibleProviderViolation:
                        row.update(status="red_provider_failure")
                    else:
                        row.update(status=generated["status"], generation=generated)
                        if generated["status"] == "admitted":
                            rel = f"{slot['slot_id']}/candidate_{generated['admitted_candidate']}/blue_task"
                            label["stage"] = "blue"
                            before_blue = client.total_calls
                            try:
                                results = measure.run_blue(out / rel, directory / "blue", client=budget,
                                                            counter=counter, cfg=cfg, seed=slot["seed"],
                                                            encounters=policy["blue_encounters"], direct_only=policy["direct_only"],
                                                            endpoint_protocol=policy["endpoint_protocol"])
                            except measure.MeasurementRefused as exc:
                                # Baseline replay disagreement and screening context limits
                                # stay local to a slot; integrity/accounting failures are fatal.
                                context_limit = policy["direct_only"] and isinstance(exc, measure.DirectContextLimit)
                                if (not (context_limit or str(exc).startswith("baseline no longer shows the admission row:"))
                                        or client.total_calls != before_blue
                                        or (directory / "blue").exists()):
                                    raise
                                validate_plan(plan)
                                measure._check_export(out / rel)
                                row.update(status="blue_context_limit" if context_limit else "blue_refused", generation_status="admitted",
                                           blue_task=rel, refusal_reason="direct_context_required" if context_limit else "baseline_replay_disagreement",
                                           refusal_detail=str(exc), blue_calls=0)
                            else:
                                row.update(blue_task=rel, blue_directory=f"{slot['slot_id']}/blue",
                                           blue_outcomes=[r["outcome"] for r in results])
                row["calls"] = client.total_calls - row["calls_before"]
                _append(out / "events.jsonl", {"event": "slot_end", "slot_id": slot["slot_id"],
                                               "status": row["status"], "calls": row["calls"]})
            row["slot_calls"] = row["calls"] - row["extraction_calls"]
            _append(out / "slots.jsonl", row)
            rows.append(row)
    except Exception:
        status = "aborted"
        raise
    finally:
        client.on_call = previous_callback
        save_new(out / "collection.json", {"status": status, "scheduled_slots": len(plan["slots"]),
            "recorded_slots": len(rows), "unrecorded_slots": [s["slot_id"] for s in plan["slots"] if s["slot_id"] not in {r["slot_id"] for r in rows}],
            "calls": client.total_calls, "per_arm": {a: budgets[a].report() for a in ARMS},
            "evaluator_executed": False})
    return _json(out / "collection.json")


def classify_collection(out: Path) -> list[dict[str, Any]]:
    """Explicit post-collection stage; never feed these results back to generation."""
    plan = _json(out / "plan.json")
    if hash_payload(plan) != _json(out / "plan_hash.json")["hash"]:
        raise ValueError("stored plan changed")
    validate_plan(plan)
    if _json(out / "collection.json")["status"] != "complete":
        raise ValueError("collection did not complete; inspect journal before offline review")
    summaries = []
    for line in (out / "slots.jsonl").read_text().splitlines():
        row = json.loads(line)
        if "blue_directory" not in row:
            continue
        item = plan["carriers"][row["carrier_index"]]
        blue_config = _json(out / row["blue_directory"] / "blue_config.json")
        policy = plan["collection_policy"]
        if (blue_config["endpoint_protocol"] != policy["endpoint_protocol"]
                or blue_config["encounters"] != policy["blue_encounters"]
                or blue_config["direct_only"] != policy["direct_only"]
                or blue_config["runner"] != plan["blue_config"]):
            raise ValueError("recorded Blue protocol differs from frozen plan")
        result = measure.classify(out / row["blue_directory"], out / row["blue_task"],
                                   _json(item["evaluator_freeze"]), Path(item["evaluator_root"]))
        for endpoint in result["encounters"]:
            if endpoint["category"] in {"public_pass_hidden_pass", "public_pass_hidden_fail"}:
                verification = endpoint["endpoint_evaluator"]
                if (endpoint["endpoint_public"]["tier"] != "visible_pass"
                        or verification["result"] not in {"pass", "fail"}
                        or verification["result"] == "fail" and not verification.get("failures")):
                    raise ValueError("measurement verdict is inconclusive; do not aggregate as a functional outcome")
        summaries.append({"slot_id": row["slot_id"], "arm": row["arm"], "carrier_id": row["carrier_id"],
                          "measurement_summary": result["summary"]})
    return summaries
