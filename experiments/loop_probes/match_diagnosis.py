"""Score every stored memory item against every encounter of a run (no model calls).

Shows which profile features keep an item from matching bugs on other designs.
Usage: python -m experiments.loop_probes.match_diagnosis RUN_DIR
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from r3e.knowledge.schema import ObservableProfile
from r3e.loop.state import RunState


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dir", type=Path)
    run_dir = parser.parse_args().run_dir
    state = RunState(run_dir)
    items = state.store.items_with_status("candidate", "qualified", "active", "rejected", "suspended", "retired")
    inference = state.inference()
    encounters = [json.loads(line)["encounter"] for line in (run_dir / "encounters.jsonl").read_text().splitlines()
                  if line.strip()]
    missed, scores = collections.Counter(), []
    for item in items:
        sources = set(item.payload["evidence"].get("source_episode_hashes") or [])
        print(f"== {item.item_id} v{item.version} type={item.bug_type}")
        print("   conditions on:", dict(item.applicability_profile.status), dict(item.applicability_profile.causal))
        for e in encounters:
            profile = ObservableProfile(status=e["profile"]["status"], causal=e["profile"]["causal"])
            result = state.matcher.score(item, profile, inference.posterior(profile))
            if result is None:
                print(f"   {e['challenge_id']}: excluded (oracle stage)")
                continue
            own = e["episode_hash"] in sources
            print(f"   {e['challenge_id']}{' (source)' if own else ''}: score {result.score:.2f} "
                  f"mismatched {list(result.mismatched)}")
            if not own:
                missed.update(result.mismatched)
                scores.append(result.score)
    print(f"threshold {state.matcher.threshold}; non-source pairs {len(scores)}; "
          f"max score {max(scores):.2f}" if scores else "no non-source pairs")
    print("most frequent mismatched features:", missed.most_common(8))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
