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
from r3e.loop.freeform_curriculum import (  # noqa: E402
    FreeformCurriculumLoop, FakeFreeformTransport, freeform_holdout_variants,
)
from r3e.loop.red_design import EditBudget  # noqa: E402
from r3e.loop.qualification import QualificationConfig  # noqa: E402
from r3e.loop.sim import Simulator  # noqa: E402
from r3e.loop.state import RunState  # noqa: E402
from r3e.loop.freeform_pilot import FreeformPilot, load_seed, PILOT_BOUND  # noqa: E402


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--state-dir", required=True, type=Path)
    p.add_argument("--manifests", nargs="*", default=["datasets/manifests/cirfix39.jsonl"],
                   help="public bug manifests; pass none to use only the generated designs")
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
    p.add_argument("--red-thinking", choices=["default", "disabled", "low", "high", "max"], default=None,
                   help="Red thinking: defaults to low for freeform and disabled for catalog/pool; explicit values override")
    p.add_argument("--blue-thinking", choices=["default", "disabled", "low", "high", "max"], default="default")
    p.add_argument("--escalation-max-output-tokens", type=int, default=32768,
                   help="output cap for escalation attempts (learning cost); DeepSeek V4 allows up to 384K")
    p.add_argument("--red-timeout", type=float, default=1500.0,
                   help="per-call timeout for Red; with thinking on, its calls take minutes as Blue's do")
    p.add_argument("--blue-timeout", type=float, default=1500.0,
                   help="per-call timeout for Blue; long reasoning at large caps takes minutes")
    p.add_argument("--max-output-tokens", type=int, default=32768,
                   help="per-call output cap; reasoning tokens count against it (16k left many "
                        "answers unfinished on hard bugs)")
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
    p.add_argument("--answer-format", choices=["full", "edits"], default="full",
                   help="Blue answers with the complete RTL, or with edits (any size) or the complete RTL")
    p.add_argument("--register-trace", action="store_true",
                   help="add the design's own register values to each row of Blue's evidence window")
    p.add_argument("--memory-from-attempt", type=int, default=2,
                   help="first repair attempt that is offered memory (1 = memory first; 2 = Blue tries alone "
                        "first, the default)")
    p.add_argument("--red-source", choices=["catalog", "pool", "freeform"], default="catalog",
                   help="curriculum: Red makes catalog bugs, or chooses among the real bugs of --manifests "
                        "or writes complete RTL (freeform); pool uses only the manifests' bugs")
    p.add_argument("--red-design-candidates", type=int, default=8,
                   help="freeform: number of designs offered together per discovery slot (positive)")
    p.add_argument("--red-search", action="store_true",
                   help="freeform: bounded candidate search and public observation feedback; needs --proposals 1")
    p.add_argument("--red-pilot-seed-run", type=Path,
                   help="enable the 94-call diagnostic using this historical run's red.jsonl")
    p.add_argument("--red-pilot-seed-id", help="reviewed historical challenge ID for seed revalidation")
    p.add_argument("--red-pilot-decisions", type=Path, help="historical hit_decisions.json containing a PASS")
    p.add_argument("--red-max-changed-fraction", type=float, default=0.40,
                   help="freeform Red's code/body token edit ceiling, strictly between 0 and 1")
    p.add_argument("--curriculum", action="store_true",
                   help="Red pursues Blue's weak points across designs (r3e/loop/curriculum.py)")
    p.add_argument("--mastery-repeats", type=int, default=3)
    p.add_argument("--confirm-runs", type=int, default=2,
                   help="curriculum: extra Blue runs when its run fails; a weak point needs "
                        "--reproducible-fails failed runs")
    p.add_argument("--reproducible-fails", type=int, default=2)
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
    """Worst case per round: every proposal is a pursued weak point that Blue fails."""
    k, e = cfg.blue.budget_k, cfg.blue.escalation_k
    p, reps = cfg.proposals_per_round, cur.mastery_repeats
    red = cfg.rounds * p if cfg.red_mode != "random" else 0
    if getattr(args, "red_source", "catalog") == "freeform":
        red *= 2  # target selection, then full RTL generation; no hidden retries
    blue = cfg.rounds * p * (k + e + cur.confirm_runs * k)
    mastery = cfg.rounds * p * (2 * reps - 1) * k if cfg.learning else 0
    window = CurriculumConfig().gate_window_challenges
    qual = cfg.rounds * cfg.max_candidates_per_round * min(window, cfg.qualification.max_cases) \
        * 2 * k * cfg.qualification.repeats if cfg.learning else 0
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
    pilot = args.red_pilot_seed_run is not None
    if pilot:
        if not args.red_pilot_seed_id or not args.red_pilot_decisions:
            raise ValueError("pilot requires --red-pilot-seed-id and --red-pilot-decisions")
        if (args.red_source != "freeform" or not args.curriculum or args.red_mode != "aware"
                or not args.no_learning or args.budget_k != 3 or args.escalation_k != 0
                or args.proposals != 1 or args.confirm_runs != 2 or args.reproducible_fails != 2
                or args.evaluate or args.cross_model or not 0 <= args.max_calls <= 94):
            raise ValueError("pilot requires freeform aware curriculum, no learning/evaluation/cross-model, "
                             "k=3, escalation=0, proposals=1, confirm=2, fails=2, cap<=94")
        args.red_search = True
    elif args.red_pilot_seed_id or args.red_pilot_decisions:
        raise ValueError("seed ID/decisions require --red-pilot-seed-run")
    if args.red_search and (args.red_source != "freeform" or args.proposals != 1):
        raise ValueError("--red-search requires freeform and --proposals 1")
    if args.red_thinking is None:
        args.red_thinking = "low" if args.red_source == "freeform" else "disabled"
    if args.red_source == "freeform":
        if args.red_design_candidates < 1:
            raise ValueError("--red-design-candidates must be positive")
        if not args.curriculum or args.red_mode == "random":
            raise ValueError("--red-source freeform requires --curriculum and --red-mode aware|blind")
        if args.evaluate and args.red_mode == "blind":
            raise ValueError("targeted freeform holdout requires --red-mode aware")
        EditBudget(args.red_max_changed_fraction)  # reject bad configuration before calls
    carriers, public = [], []
    for manifest in args.manifests:
        c, ch = load_public_manifest(ROOT / manifest, ROOT)
        carriers += c
        public += ch
    if args.red_source == "pool":
        if not args.curriculum:
            print("refusing: --red-source pool needs --curriculum")
            return 2
        if args.red_mode not in ("aware", "random"):
            print("refusing: --red-source pool supports --red-mode aware or random")
            return 2
    else:
        for manifest in args.carrier_manifest:
            carriers += load_carrier_manifest(manifest)
    if args.red_source == "freeform":
        carriers = [c for c in carriers if c.spec.strip()]
        if not carriers:
            raise ValueError("freeform Red requires carriers with nonempty specifications")
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
                                     register_trace=args.register_trace, answer_format=args.answer_format),
                     qualification=QualificationConfig(objective=args.objective, max_cases=args.max_qual_cases,
                                                       repeats=args.qual_repeats),
                     learning=not args.no_learning, frozen_mutants_per_carrier=args.qual_mutants,
                     max_candidates_per_round=args.max_candidates)
    n_qual = len([c for c in public if c.carrier in splits["qualification"]]) + args.qual_mutants * len(splits["qualification"])
    n_hold = len([c for c in public if c.carrier in splits["holdout"]]) + args.holdout_mutants * len(splits["holdout"])
    bound = _upper_bound(cfg, n_qual, n_hold, args.evaluate, len(args.eval_modes), args.eval_repeats)
    cur_cfg = CurriculumConfig(mastery_repeats=args.mastery_repeats, max_generations=args.max_generations,
                               lineages_offered=args.lineages_offered, confirm_runs=args.confirm_runs,
                               reproducible_fails=args.reproducible_fails)
    if args.curriculum:
        bound = _curriculum_bound(cfg, cur_cfg, args)
    elif args.cross_model and args.evaluate:
        bound["evaluation"] *= 1 + len(args.cross_model)
        bound["total_upper_bound"] = sum(v for k, v in bound.items() if k != "total_upper_bound")
    seed_challenge = seed_provenance = None
    if pilot:
        seed_challenge, seed_provenance = load_seed(args.red_pilot_seed_run, args.red_pilot_decisions,
            args.red_pilot_seed_id, splits, EditBudget(args.red_max_changed_fraction))
        bound = PILOT_BOUND
    print(json.dumps({"splits": {k: len(v) for k, v in splits.items()},
                      "call_upper_bound": bound,
                      **({"red_source": "freeform", "red_max_changed_fraction": args.red_max_changed_fraction,
                          "red_thinking": args.red_thinking, "red_design_candidates": args.red_design_candidates}
                         if args.red_source == "freeform" else {})}, indent=1))
    if args.plan:
        return 0

    if args.fake:
        clean = [c.clean_rtl for c in carriers]
        blue_transport = FakeBlueTransport(
            nearest_clean_resolver(clean),
            succeed=lambda user, n: n >= 1 or bool(knowledge_types(user)),
        )
        red_transport = FakeFreeformTransport() if args.red_source == "freeform" else FakeRedTransport()

        def transport(**request):
            user = json.loads(request["messages"][-1]["content"])
            return (blue_transport if "current_buggy_rtl" in user else red_transport)(**request)

        json_client = build_client({"DEEPSEEK_MODEL": "fake-local-transport"}, transport=transport)
        max_calls = args.max_calls or (94 if pilot else 10_000)
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
                                       timeout_seconds=args.red_timeout,
                                       failure_dir=args.state_dir / "unparseable")
        max_calls = args.max_calls
        print(f"real provider blue={json_client.config.model_id} red={red_json_client.config.model_id} "
              f"cap={max_calls} calls red_thinking={args.red_thinking} blue_thinking={args.blue_thinking}")

    budget = BudgetedClient(json_client, max_calls=max_calls)
    budget.output_caps = {phase: args.max_output_tokens for phase in
                          ("red", "blue_inference", "qualification", "evaluation", "authoring", "mastery",
                           "cross_model", "confirmation")}
    budget.output_caps["escalation"] = args.escalation_max_output_tokens
    red_budget = budget.sibling(red_json_client) if not args.fake else budget
    state = RunState(args.state_dir)
    budget.on_call = lambda entry: state.append("calls", entry)
    if red_budget is not budget:
        red_budget.on_call = budget.on_call
    simulator = Simulator(args.state_dir / "sim_workspace")
    blue = BlueRunner(json_client=budget, simulator=simulator, project_root=ROOT, config=cfg.blue)
    if pilot:
        settings = {"blue_model": json_client.config.model_id, "red_model": red_budget.config.model_id,
                    "blue_thinking": args.blue_thinking, "red_thinking": args.red_thinking,
                    "max_output_tokens": args.max_output_tokens,
                    "blue_timeout": args.blue_timeout, "red_timeout": args.red_timeout,
                    "blue_lens_hash": blue.lens_hash, "fake": args.fake,
                    "split_seed": args.split_seed}
        diagnostic = FreeformPilot(root=args.state_dir, config=cfg, splits=splits,
            seed_challenge=seed_challenge, seed_provenance=seed_provenance,
            blue_client=budget, red_client=red_budget, project_root=ROOT, provider_settings=settings,
            edit_budget=EditBudget(args.red_max_changed_fraction), design_candidates=args.red_design_candidates)
        result = diagnostic.run()
        print(json.dumps({"pilot": result["version"], "seed_usable": result["seed"].get("usable"),
                          "cold": result["cold"], "warm": result["warm"],
                          "calls": result["cost"]["total_calls"]}, ensure_ascii=False))
        return 0
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
    elif args.red_source == "freeform":
        loop = FreeformCurriculumLoop(config=cfg, state=state, splits=splits, public_challenges=public, blue=blue,
                                     red_client=red_budget, simulator=simulator, curriculum=cur_cfg,
                                     cross_model_blues=cross_blues,
                                     edit_budget=EditBudget(args.red_max_changed_fraction),
                                     design_candidates=args.red_design_candidates, search=args.red_search)
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
        elif args.red_source == "freeform":
            holdout = freeform_holdout_variants(loop, splits["holdout"], seed=args.seed + 2000)
            print(json.dumps({"holdout_variants": len(holdout)}))
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
