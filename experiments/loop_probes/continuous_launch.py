"""Offline freeze/check and explicit funded launch for the 21-call continuous pilot."""
from __future__ import annotations

import argparse
from decimal import Decimal
from pathlib import Path
import shutil

from experiments.loop_probes import continuous_loop as loop, red907_launch as shared
from r3e.loop import red_continuous as red, repo_blue as rb
from r3e.loop.token_count import TokenCounter
from r3e.protocol.hashing import hash_payload

TARIFF = {
    "currency": "CNY", "input_per_million": "2", "output_per_million": "8",
    "basis": "peak_uncached", "checked_date": "2026-10-10",
    "source": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
    "model_alias": "deepseek-v4-flash", "served_model": "DeepSeek-V4.1-Flash",
}
ENVELOPE = "12"
SYSTEM_LIMITS = {red.SYSTEM: 65536, rb.SYSTEM_REPAIR: 32768}


def budget(profile="zipcpu"):
    """Bound all 21 requests at context minus output, including the usage tolerance."""
    rows = []
    if profile not in {"zipcpu", "sequence"}:
        raise ValueError("unknown continuous pilot profile")
    schedule = (("red", 3, 16384 if profile == "sequence" else 65536), ("online", 3, 32768),
                ("reference", 3, 32768), ("anchor", 16 if profile == "sequence" else 12, 32768))
    for role, count, output in schedule:
        local_input = 131072 - output
        provider_input = local_input * 105 // 100 + 128
        cost = Decimal(count) * (provider_input * 2 + output * 8) / 1_000_000
        rows.append({"role": role, "calls": count, "output_limit": output,
                     "maximum_local_input": local_input, "maximum_allowed_provider_input": provider_input,
                     "maximum_CNY": str(cost)})
    return {"rows": rows, "max_calls": sum(r["calls"] for r in rows),
            "maximum_CNY": str(sum(Decimal(r["maximum_CNY"]) for r in rows)),
            "spending_envelope_CNY": ENVELOPE, "tariff": TARIFF,
            "scope": "Conditional tariff bound, not expected spending or a provider invoice guarantee."}


def client_identity():
    from r3e.loop.env import build_client
    return build_client({}, model_override="deepseek-v4-flash", thinking="low", timeout_seconds=1500,
                        maximum_output_tokens=65536, temperature=0.2, require_seed=False,
                        transport=lambda **kw: None).config.public_identity


def check_scope(plan):
    profile = plan.get("launch_profile", "zipcpu")
    if profile == "sequence":
        if (plan["carrier_mode"] != "fixed" or plan["fixed_carrier_index"] != 0
                or len(plan["carriers"]) != 1 or len(plan["anchors"]) != 2 or plan["anchor_repeats"] != 2
                or [a["carrier_index"] for a in plan["anchors"]] != [0, 0]
                or plan["identities"][0]["carrier_id"] != "chipbench_prob003_group_boundary_v1"
                or plan["mechanism_focus"] is None
                or plan["mechanism_focus"]["history"]["challenge_id"] != "RC_406d811b4d61a2"
                or plan["rounds"] != 3 or plan["slots_per_round"] != 1 or plan["max_calls"] != 25
                or plan["red_output_tokens"] != 16384 or plan["client_config"] != client_identity()
                or plan["cost_bound"] != budget(profile) or plan["spending_envelope_CNY"] != ENVELOPE):
            raise ValueError("launch scope or cost policy differs from the 25-call Prob003 mechanism pilot")
        return
    if profile != "zipcpu":
        raise ValueError("unknown continuous pilot profile")
    if (plan["carrier_mode"] != "fixed" or plan["fixed_carrier_index"] != 0
            or len(plan["carriers"]) != 3 or len(plan["anchors"]) != 3
            or [a["carrier_index"] for a in plan["anchors"]] != [0, 1, 2]
            or plan["identities"][0]["carrier_id"] != "zipcpu_wb2axip_axilfetch_32x4"
            or plan["rounds"] != 3 or plan["slots_per_round"] != 1 or plan["max_calls"] != 21
            or plan["client_config"] != client_identity()
            or plan["cost_bound"] != budget() or plan["spending_envelope_CNY"] != ENVELOPE):
        raise ValueError("launch scope or cost policy differs from the 21-call fixed-ZipCPU pilot")


def check_bundle(directory, repo):
    plan = shared.check_archive(directory, repo)
    if shared.sha(directory / "launch.py") != shared.sha(Path(__file__)):
        raise ValueError("launcher copy differs from the checked implementation")
    loop.validate_plan(plan)
    check_scope(plan)
    counter = TokenCounter()
    if counter.identity != plan["tokenizer"]:
        raise ValueError("tokenizer changed")
    return plan, counter


def freeze(materials, directory, *, seed=700, profile="zipcpu"):
    """Qualify public materials and save a recoverable proposal. Never read credentials."""
    counter = TokenCounter()
    extra = ({"mechanism_focus": materials["mechanism_focus"], "anchor_repeats": 2, "red_output_tokens": 16384}
             if profile == "sequence" else {})
    plan = loop.make_plan(materials["carriers"], materials["anchors"], counter=counter,
                          client_config=client_identity(), seed=seed,
                          carrier_mode="fixed", fixed_carrier_index=0, **extra)
    plan.update(cost_bound=budget(profile), spending_envelope_CNY=ENVELOPE, launch_profile=profile,
                authorization="proposed only; separate user approval required before collect")
    check_scope(plan)
    directory.mkdir(parents=True, exist_ok=False)
    for rel, digest in plan["code"].items():
        dst = directory / "code_snapshot" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(loop.ROOT / rel, dst)
        if shared.sha(dst) != digest:
            raise ValueError("code changed while freezing")
    shutil.copy2(Path(__file__), directory / "launch.py")
    shared.save(directory / "plan.json", plan)
    shared.save(directory / "plan_hash.json", {"hash": hash_payload(plan)})
    shared.save(directory / "code_snapshot_manifest.json", {"files_sha256": plan["code"]})
    shared.save(directory / "budget.json", budget(profile))
    shared.save(directory / "launch_manifest.json", {"files_sha256": {"launch.py": shared.sha(directory / "launch.py")}})
    files = ["plan.json", "plan_hash.json", "code_snapshot_manifest.json", "budget.json", "launch_manifest.json"]
    if profile == "sequence":
        (directory / "PLAN.md").write_text(
            "# Prob003 continuous mechanism development pilot\n\n"
            "One human-selected historical mechanism; three fixed-carrier rounds. Each round has one Red proposal, "
            "one current-Blue repair and one independent frozen-B_0 repair. Two reserved new variants each receive "
            "two independent encounters at every B_0..B_3 snapshot, after the final online commit.\n\n"
            "25 requests maximum; no retries, replacement slots or confirmation. Red output limit 16,384; Blue "
            "32,768. Provider/client/tokenizer/interpreter and all source/suite/code identities are in plan.json. "
            "Historical seed and reserved-mutant repeats are rejected before Blue. Only online public repair "
            "observations teach either agent; passing repairs remain provisional.\n\n"
            f"Conditional full-context cost bound: CNY {plan['cost_bound']['maximum_CNY']}, at the tariff recorded "
            "in budget.json. CNY 12 is a hard spending envelope, not an expected invoice. Recheck official pricing "
            "before any separately authorized launch. Existing A/B/HWE reserves are not assigned.\n\n"
            "Report the full generation funnel, current Blue versus B_0, deferred variant performance by snapshot, "
            "actual experience delivery, repeated exposure and source verification. Small diagnostic samples "
            "cannot establish mastery, causal feedback benefit or cross-design transfer. No spending approval is implied.\n")
        (directory / "LAUNCH.md").write_text(
            "# Execution\n\nUse the exact interpreter in plan.json and an environment with the pinned tokenizer. "
            "Run from the repository root; replace PLAN_DIR and RUN_DIR with the chosen directories.\n\n"
            "```sh\n<FROZEN_PYTHON> -m experiments.loop_probes.continuous_launch check --plan-dir PLAN_DIR\n"
            "# Only after separate approval for 25 requests and the CNY 12 envelope:\n"
            "<FROZEN_PYTHON> -m experiments.loop_probes.continuous_launch collect --plan-dir PLAN_DIR "
            "--env-file ENV_FILE --run-dir RUN_DIR\n"
            "# Offline, only after complete collection:\n"
            "<FROZEN_PYTHON> -m experiments.loop_probes.sequence_curriculum report --out RUN_DIR/collection\n```\n\n"
            "The balance pre-check runs before creating RUN_DIR. One shared transport enforces auth/balance "
            "halts, consecutive-failure halt, request-specific output and usage guards, and the spending reserve. "
            "Do not resume, substitute tasks or spend unused reserves on more requests. Reports do not modify state.\n")
        files += ["PLAN.md", "LAUNCH.md"]
    shared.save(directory / "delivery_manifest.json", {
        "files_sha256": {name: shared.sha(directory / name) for name in files}})
    check_bundle(directory, loop.ROOT)
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "check", "collect"))
    parser.add_argument("--repo-root", type=Path, default=loop.ROOT)
    parser.add_argument("--plan-dir", type=Path, required=True)
    parser.add_argument("--materials", type=Path)
    parser.add_argument("--seed", type=int, default=700)
    parser.add_argument("--profile", choices=("zipcpu", "sequence"), default="zipcpu")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--run-dir", type=Path)
    args = parser.parse_args()
    if args.action == "freeze":
        if args.materials is None:
            parser.error("freeze requires --materials")
        plan = freeze(shared.read(args.materials), args.plan_dir, seed=args.seed, profile=args.profile)
        print(f"PASS: frozen proposal, {plan['max_calls']} calls, CNY 12 envelope; no model calls or spending authorization")
        return 0
    plan, counter = check_bundle(args.plan_dir, args.repo_root)
    if args.action == "check":
        print(f"PASS: {len(plan['code'])} frozen code files, {plan['max_calls']} calls, CNY 12 envelope; offline only")
        return 0
    if args.env_file is None or args.run_dir is None:
        parser.error("collect requires --env-file and a fresh --run-dir; obtain separate approval first")
    return shared.run_collection(args, plan, counter, loop.collect,
                                 allowed_systems={red.SYSTEM: plan["red_output_tokens"], rb.SYSTEM_REPAIR: 32768},
                                 entrypoint=Path(__file__))


if __name__ == "__main__":
    raise SystemExit(main())
