"""Run the closed red–blue loop, a dry plan, or the holdout evaluation.

Safety defaults:
- No real model call happens unless ``--allow-real-calls`` is given together
  with ``--max-calls N`` (N > 0). The cap is enforced before every call.
- Credentials are read only from the ``.llm`` env file passed with
  ``--llm-env``, or named by ``R3E_LLM_ENV_FILE``. They are never printed.
- ``--fake`` uses scripted local transports; its results check plumbing only.

Examples::

    python -m r3e.loop.cli --state-dir RUN --plan
    python -m r3e.loop.cli --state-dir RUN --fake --rounds 2
    python -m r3e.loop.cli --state-dir RUN --llm-env PATH \\
        --allow-real-calls --max-calls 50 --rounds 1 --proposals 2
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "r3e"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from r3e.loop.blue import BlueConfig, BlueRunner  # noqa: E402
from r3e.loop.budget import BudgetedClient, cost_from_calls  # noqa: E402
from r3e.loop.carriers import DEFAULT_CARRIER_MANIFEST, load_carrier_manifest  # noqa: E402
from r3e.loop.corpus import load_public_manifest, split_by_cluster  # noqa: E402
from r3e.protocol.hashing import hash_payload  # noqa: E402
from r3e.loop.env import build_client, load_llm_env  # noqa: E402
from r3e.loop.evaluate import evaluate_holdout  # noqa: E402
from r3e.loop.fakes import FakeBlueTransport, FakeRedTransport, knowledge_types, nearest_clean_resolver  # noqa: E402
from r3e.loop.orchestrator import ClosedLoop, LoopConfig, frozen_challenges  # noqa: E402
from r3e.loop.curriculum import CurriculumConfig, CurriculumLoop, holdout_variants  # noqa: E402
from r3e.loop.pool import PoolCurriculumLoop, holdout_pool  # noqa: E402
from r3e.loop.qualification import QualificationConfig  # noqa: E402
from r3e.loop.sim import Simulator  # noqa: E402
from r3e.loop.state import RunState  # noqa: E402


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--state-dir", required=True, type=Path)
    p.add_argument("--manifests", nargs="+", default=["datasets/manifests/cirfix39.jsonl"])
    p.add_argument("--rounds", type=int, default=2)
    p.add_argument("--proposals", type=int, default=4)
    p.add_argument("--red-mode", choices=["aware", "blind", "random"], default="aware")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--split-seed", type=int, default=0)
    p.add_argument("--budget-k", type=int, default=3)
    p.add_argument("--escalation-k", type=int, default=3)
    p.add_argument("--objective", choices=["solve_rate", "cost"], default="solve_rate",
                   help="what qualification counts as help (freeze before formal runs)")
    p.add_argument("--llm-env", type=Path, default=None)
    p.add_argument("--max-calls", type=int, default=0)
    p.add_argument("--model", default=None, help="override the model named in the .llm env file")
    p.add_argument("--red-model", default=None,
                   help="Red's model (default: --model), so Blue can be swapped while Red stays fixed")
    p.add_argument("--cross-model", action="append", default=[], metavar="[PREFIX:]MODEL",
                   help="also run the curriculum mastery test and the holdout evaluation with this Blue "
                        "model, using the same memory and seeds (repeatable; PREFIX selects the .llm env "
                        "variables, default DEEPSEEK)")
    p.add_argument("--red-thinking", choices=["default", "disabled", "low", "high", "max"], default="disabled",
                   help="DeepSeek thinking for Red; V4 defaults to high effort, which exhausted the output cap")
    p.add_argument("--blue-thinking", choices=["default", "disabled", "low", "high", "max"], default="default")
    p.add_argument("--escalation-max-output-tokens", type=int, default=32768,
                   help="output cap for escalation attempts (learning cost); DeepSeek V4 allows up to 384K")
    p.add_argument("--blue-timeout", type=float, default=900.0,
                   help="per-call timeout for Blue; long reasoning at large caps takes minutes")
    p.add_argument("--max-output-tokens", type=int, default=16384,
                   help="per-call output cap; reasoning tokens count against it")
    p.add_argument("--allow-real-calls", action="store_true")
    p.add_argument("--fake", action="store_true")
    p.add_argument("--plan", action="store_true", help="print the call upper bound and exit")
    p.add_argument("--evaluate", action="store_true", help="run holdout evaluation after the loop")
    p.add_argument("--carrier-manifest", nargs="*", type=Path, default=[DEFAULT_CARRIER_MANIFEST],
                   help="extra carriers built by r3e.loop.carriers (generated visible/hidden testbenches)")
    p.add_argument("--max-carriers", nargs=3, type=int, metavar=("DISC", "QUAL", "HOLD"), default=None,
                   help="cap carriers per split (deterministic subset) to keep pilots small")
    p.add_argument("--max-qual-cases", type=int, default=8)
    p.add_argument("--qual-mutants", type=int, default=4,
                   help="frozen random-operator mutants per qualification carrier")
    p.add_argument("--max-candidates", type=int, default=4,
                   help="knowledge candidates qualified per round (highest support first)")
    p.add_argument("--eval-modes", nargs="+", default=["none", "static", "matched", "shuffled"])
    p.add_argument("--holdout-mutants", type=int, default=2)
    p.add_argument("--eval-repeats", type=int, default=3)
    p.add_argument("--eval-rounds", nargs="*", type=int, default=None,
                   help="snapshots to evaluate (default: all rounds)")
    p.add_argument("--max-holdout-cases", type=int, default=None,
                   help="deterministic subset of holdout challenges to evaluate")
    p.add_argument("--no-learning", action="store_true",
                   help="Red + Blue only: no authoring, qualification, monitoring (cheap mechanism checks)")
    p.add_argument("--qual-repeats", type=int, default=3)
    p.add_argument("--structural-view", action="store_true",
                   help="add a factual structural view of the failing outputs to Blue's evidence")
    p.add_argument("--memory-from-attempt", type=int, default=2,
                   help="first repair attempt that is offered memory (1 = memory first; 2 = Blue tries alone "
                        "first, the default)")
    p.add_argument("--red-source", choices=["catalog", "pool"], default="catalog",
                   help="curriculum: Red makes catalog bugs, or chooses among the real bugs of --manifests "
                        "(pool: designs are those of the manifests' bugs)")
    p.add_argument("--curriculum", action="store_true",
                   help="Red pursues Blue's weak points across designs (r3e/loop/curriculum.py)")
    p.add_argument("--mastery-repeats", type=int, default=3)
    p.add_argument("--max-generations", type=int, default=4)
    p.add_argument("--lineages-offered", type=int, default=4)
    return p.parse_args()


def _upper_bound(cfg: LoopConfig, n_qual: int, n_holdout: int, evaluate: bool, n_modes: int = 4,
                 eval_repeats: int = 1) -> dict[str, int]:
    k, e = cfg.blue.budget_k, cfg.blue.escalation_k
    red = cfg.rounds * cfg.proposals_per_round if cfg.red_mode != "random" else 0
    blue = cfg.rounds * cfg.proposals_per_round * (k + e)
    qual = (cfg.rounds * cfg.max_candidates_per_round * min(n_qual, cfg.qualification.max_cases)
            * 2 * k * cfg.qualification.repeats) if cfg.learning else 0
    ev = (n_holdout * k * (1 + (n_modes - 1) * cfg.rounds) * eval_repeats) if evaluate else 0
    return {"red": red, "blue_with_escalation": blue, "qualification": qual, "evaluation": ev,
            "total_upper_bound": red + blue + qual + ev}


def _curriculum_bound(cfg: LoopConfig, cur: CurriculumConfig, args) -> dict[str, int]:
    """Worst case per round: every proposal is a pursued weak point."""
    k, e = cfg.blue.budget_k, cfg.blue.escalation_k
    p, reps = cfg.proposals_per_round, cur.mastery_repeats
    red = cfg.rounds * p
    blue = cfg.rounds * p * (k + e)
    mastery = cfg.rounds * p * (2 * reps - 1) * k
    window = CurriculumConfig().gate_window_challenges
    qual = cfg.rounds * cfg.max_candidates_per_round * min(window, cfg.qualification.max_cases) \
        * 2 * k * cfg.qualification.repeats
    weak_points = cfg.rounds * p
    n_cross = len(args.cross_model)
    cross = cfg.rounds * p * 2 * reps * k * n_cross
    ev = (weak_points + weak_points * k * 2 * args.eval_repeats * (1 + n_cross)) if args.evaluate else 0
    out = {"red": red, "blue_with_escalation": blue, "mastery": mastery, "cross_model_mastery": cross,
           "qualification": qual, "evaluation_incl_holdout_red": ev}
    out["total_upper_bound"] = sum(out.values())
    return out


def main() -> int:
    args = _args()
    carriers, public = [], []
    for manifest in args.manifests:
        c, ch = load_public_manifest(ROOT / manifest, ROOT)
        carriers += c
        public += ch
    if args.red_source == "pool":
        if not args.curriculum:
            print("refusing: --red-source pool needs --curriculum")
            return 2
    else:
        for manifest in args.carrier_manifest:
            carriers += load_carrier_manifest(manifest)
    splits = split_by_cluster(carriers, seed=args.split_seed)
    if args.max_carriers:
        for name, cap in zip(("discovery", "qualification", "holdout"), args.max_carriers):
            splits[name] = sorted(splits[name], key=lambda c: hash_payload({"s": args.split_seed, "c": c.carrier_id}))[:cap]
    if args.curriculum:
        # gate cases are Red's cross-design variants on discovery designs, so the
        # qualification designs join discovery; holdout stays untouched
        splits["discovery"] = splits["discovery"] + splits["qualification"]
        splits["qualification"] = []
    cfg = LoopConfig(rounds=args.rounds, proposals_per_round=args.proposals, red_mode=args.red_mode,
                     seed=args.seed, blue=BlueConfig(budget_k=args.budget_k, escalation_k=args.escalation_k,
                                     memory_from_attempt=args.memory_from_attempt,
                                     structural_view=args.structural_view),
                     qualification=QualificationConfig(objective=args.objective, max_cases=args.max_qual_cases,
                                                       repeats=args.qual_repeats),
                     learning=not args.no_learning, frozen_mutants_per_carrier=args.qual_mutants,
                     max_candidates_per_round=args.max_candidates)
    n_qual = len([c for c in public if c.carrier in splits["qualification"]]) + args.qual_mutants * len(splits["qualification"])
    n_hold = len([c for c in public if c.carrier in splits["holdout"]]) + args.holdout_mutants * len(splits["holdout"])
    bound = _upper_bound(cfg, n_qual, n_hold, args.evaluate, len(args.eval_modes), args.eval_repeats)
    cur_cfg = CurriculumConfig(mastery_repeats=args.mastery_repeats, max_generations=args.max_generations,
                               lineages_offered=args.lineages_offered)
    if args.curriculum:
        bound = _curriculum_bound(cfg, cur_cfg, args)
    elif args.cross_model and args.evaluate:
        bound["evaluation"] *= 1 + len(args.cross_model)
        bound["total_upper_bound"] = sum(v for k, v in bound.items() if k != "total_upper_bound")
    print(json.dumps({"splits": {k: len(v) for k, v in splits.items()},
                      "call_upper_bound": bound}, indent=1))
    if args.plan:
        return 0

    if args.fake:
        clean = [c.clean_rtl for c in carriers]
        blue_transport = FakeBlueTransport(
            nearest_clean_resolver(clean),
            succeed=lambda user, n: n >= 1 or bool(knowledge_types(user)),
        )
        red_transport = FakeRedTransport()

        def transport(**request):
            user = json.loads(request["messages"][-1]["content"])
            return (blue_transport if "current_buggy_rtl" in user else red_transport)(**request)

        json_client = build_client({"DEEPSEEK_MODEL": "fake-local-transport"}, transport=transport)
        max_calls = args.max_calls or 10_000
    else:
        if not args.allow_real_calls or args.max_calls <= 0:
            print("refusing: real calls need --allow-real-calls and --max-calls > 0 (or use --fake/--plan)")
            return 2
        env = load_llm_env(args.llm_env)
        json_client = build_client(env, model_override=args.model, thinking=args.blue_thinking,
                                   maximum_output_tokens=max(args.max_output_tokens,
                                                             args.escalation_max_output_tokens),
                                   timeout_seconds=args.blue_timeout,
                                   failure_dir=args.state_dir / "unparseable")
        red_json_client = build_client(env, model_override=args.red_model or args.model, thinking=args.red_thinking,
                                       maximum_output_tokens=args.max_output_tokens,
                                       failure_dir=args.state_dir / "unparseable")
        max_calls = args.max_calls
        print(f"real provider blue={json_client.config.model_id} red={red_json_client.config.model_id} "
              f"cap={max_calls} calls red_thinking={args.red_thinking} blue_thinking={args.blue_thinking}")

    budget = BudgetedClient(json_client, max_calls=max_calls)
    budget.output_caps = {phase: args.max_output_tokens for phase in
                          ("red", "blue_inference", "qualification", "evaluation", "authoring", "mastery",
                           "cross_model")}
    budget.output_caps["escalation"] = args.escalation_max_output_tokens
    red_budget = budget.sibling(red_json_client) if not args.fake else budget
    state = RunState(args.state_dir)
    budget.on_call = lambda entry: state.append("calls", entry)
    if red_budget is not budget:
        red_budget.on_call = budget.on_call
    simulator = Simulator(args.state_dir / "sim_workspace")
    blue = BlueRunner(json_client=budget, simulator=simulator, project_root=ROOT, config=cfg.blue)
    cross_blues = {}
    for spec in args.cross_model:
        prefix, _, model = spec.rpartition(":")
        if args.fake:
            inner = json_client  # plumbing only: the fake transport stands in for every model
        else:
            inner = build_client(env, prefix=prefix or "DEEPSEEK", model_override=model,
                                 thinking=args.blue_thinking,
                                 maximum_output_tokens=max(args.max_output_tokens, args.escalation_max_output_tokens),
                                 timeout_seconds=args.blue_timeout,
                                   failure_dir=args.state_dir / "unparseable")
        cross_blues[spec] = BlueRunner(json_client=budget.sibling(inner), simulator=simulator,
                                       project_root=ROOT, config=cfg.blue)
    if cross_blues and not args.curriculum and not args.evaluate:
        print("note: --cross-model is used by the curriculum mastery test and by --evaluate")
    if args.red_source == "pool":
        loop = PoolCurriculumLoop(pool=public, config=cfg, state=state, splits=splits, public_challenges=public,
                                  blue=blue, red_client=red_budget, simulator=simulator, curriculum=cur_cfg,
                                  cross_model_blues=cross_blues)
    elif args.curriculum:
        loop = CurriculumLoop(config=cfg, state=state, splits=splits, public_challenges=public, blue=blue,
                              red_client=red_budget, simulator=simulator, curriculum=cur_cfg,
                              cross_model_blues=cross_blues)
    else:
        loop = ClosedLoop(config=cfg, state=state, splits=splits, public_challenges=public, blue=blue,
                          red_client=red_budget, simulator=simulator)
    result = loop.run()
    for row in result["rounds"]:
        print(json.dumps({k: row[k] for k in ("round", "admitted", "followups", "solved_within_budget",
                                               "solved_by_escalation", "lineages_by_status", "candidates",
                                               "decisions", "suspended") if k in row}, ensure_ascii=False))
    if args.evaluate:
        if args.red_source == "pool":  # real bugs on designs the loop never touched
            holdout = holdout_pool(loop)
            print(json.dumps({"holdout_real_bugs": len(holdout)}))
        elif args.curriculum:  # Red's variants of each weak point on untouched designs
            holdout = holdout_variants(loop, splits["holdout"], seed=args.seed + 2000)
            print(json.dumps({"holdout_variants": len(holdout)}))
        else:
            holdout = frozen_challenges(splits["holdout"], public, simulator, per_carrier=args.holdout_mutants,
                                        seed=args.seed + 2000)
        if args.max_holdout_cases is not None:
            holdout = sorted(holdout, key=lambda ch: hash_payload({"s": args.seed, "c": ch.challenge_id}))[
                :args.max_holdout_cases]
        if not holdout:
            print("no holdout challenges (no weak points to vary); evaluation skipped")
            args.evaluate = False
    if args.evaluate:
        for label, runner in [("primary", blue), *cross_blues.items()]:
            report = evaluate_holdout(state, runner, holdout, modes=args.eval_modes, repeats=args.eval_repeats,
                                      rounds=args.eval_rounds, blue_model=label)
            for snap in report["snapshots"]:
                print(json.dumps({"blue_model": label, "round": snap["round"], "summary": snap["summary"]}))
    print(json.dumps({"cost_this_process": budget.report(), "cost_whole_run": cost_from_calls(state.read("calls")),
                      "fake": args.fake, "config": asdict(cfg)}, indent=1)[:5000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
