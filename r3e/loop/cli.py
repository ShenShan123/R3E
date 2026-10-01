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
from r3e.loop.budget import BudgetedClient  # noqa: E402
from r3e.loop.corpus import load_public_manifest, split_by_cluster  # noqa: E402
from r3e.loop.env import build_client, load_llm_env  # noqa: E402
from r3e.loop.evaluate import evaluate_holdout  # noqa: E402
from r3e.loop.fakes import FakeBlueTransport, FakeRedTransport, knowledge_types, nearest_clean_resolver  # noqa: E402
from r3e.loop.orchestrator import ClosedLoop, LoopConfig, frozen_challenges  # noqa: E402
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
    p.add_argument("--allow-real-calls", action="store_true")
    p.add_argument("--fake", action="store_true")
    p.add_argument("--plan", action="store_true", help="print the call upper bound and exit")
    p.add_argument("--evaluate", action="store_true", help="run holdout evaluation after the loop")
    return p.parse_args()


def _upper_bound(cfg: LoopConfig, n_qual: int, n_holdout: int, evaluate: bool) -> dict[str, int]:
    k, e = cfg.blue.budget_k, cfg.blue.escalation_k
    red = cfg.rounds * cfg.proposals_per_round if cfg.red_mode != "random" else 0
    blue = cfg.rounds * cfg.proposals_per_round * (k + e)
    qual = cfg.rounds * cfg.max_candidates_per_round * min(n_qual, cfg.qualification.max_cases) * 2 * k
    ev = (n_holdout * k * (1 + 3 * cfg.rounds)) if evaluate else 0
    return {"red": red, "blue_with_escalation": blue, "qualification": qual, "evaluation": ev,
            "total_upper_bound": red + blue + qual + ev}


def main() -> int:
    args = _args()
    carriers, public = [], []
    for manifest in args.manifests:
        c, ch = load_public_manifest(ROOT / manifest, ROOT)
        carriers += c
        public += ch
    splits = split_by_cluster(carriers, seed=args.split_seed)
    cfg = LoopConfig(rounds=args.rounds, proposals_per_round=args.proposals, red_mode=args.red_mode,
                     seed=args.seed, blue=BlueConfig(budget_k=args.budget_k, escalation_k=args.escalation_k),
                     qualification=QualificationConfig(objective=args.objective))
    n_qual = len([c for c in public if c.carrier in splits["qualification"]]) + 2 * len(splits["qualification"])
    n_hold = len([c for c in public if c.carrier in splits["holdout"]]) + 2 * len(splits["holdout"])
    bound = _upper_bound(cfg, n_qual, n_hold, args.evaluate)
    print(json.dumps({"splits": {k: sorted({c.cluster_id for c in v}) for k, v in splits.items()},
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
        json_client = build_client(env)
        max_calls = args.max_calls
        print(f"real provider model={json_client.config.model_id} cap={max_calls} calls")

    budget = BudgetedClient(json_client, max_calls=max_calls)
    state = RunState(args.state_dir)
    simulator = Simulator(args.state_dir / "sim_workspace")
    blue = BlueRunner(json_client=budget, simulator=simulator, project_root=ROOT, config=cfg.blue)
    loop = ClosedLoop(config=cfg, state=state, splits=splits, public_challenges=public, blue=blue,
                      red_client=budget, simulator=simulator)
    result = loop.run()
    for row in result["rounds"]:
        print(json.dumps({k: row[k] for k in ("round", "admitted", "solved_within_budget",
                                               "solved_by_escalation", "candidates", "decisions",
                                               "suspended")}, ensure_ascii=False))
    if args.evaluate:
        holdout = frozen_challenges(splits["holdout"], public, simulator, per_carrier=2, seed=args.seed + 2000)
        report = evaluate_holdout(state, blue, holdout)
        for snap in report["snapshots"]:
            print(json.dumps({"round": snap["round"], "summary": snap["summary"]}))
    print(json.dumps({"cost": budget.report(), "fake": args.fake, "config": asdict(cfg)}, indent=1)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
