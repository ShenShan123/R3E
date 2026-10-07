# HWE Ibex development handoff

`scripts/convert_hwe_ibex.py` converts three reviewed HWE tasks into repository
workspaces and can reproduce their upstream directed tests without model calls.
It does not implement Blue's repository editing, log evidence, or supplementary
verification. The output is a new repository-task schema, not a ChipBench
single-file manifest accepted by the current loop.

## Selection and partition

| PR | Development purpose | Effective baseline |
| --- | --- | --- |
| 1816 | Debug-entry cause must survive a later input change | `70186c57aeff46ff47b80e8f3d6e2c3d849f2e5b` |
| 907 | Prefetch/FIFO coordination for a spanning instruction | `dd39ec0c91d13d6288e4f131a39d049ccfe32173` |
| 2232 | Debug-mode PMP permissions and propagation through interfaces | `667fd20d2ede51caececccbcbda3652074424ce2` |

Selection uses source mechanism and integration structure, before any Blue run.
All three belong to the Ibex family and are **development-only**. They do not
establish a repository-isolated holdout. Another Ibex PR is not automatically an
independent family or cross-design transfer. Freeze the formal evaluation set
separately after dependency/clone review.

For 1816, upstream `prepare_script` checks out a different commit from the
record's `base.sha` (`7b1be3354d650bc5b23dff6f439459c353288e4f`). HWE's preparation
override controls the actual workspace. The converter uses that explicit
checkout, records both commits, and checks that the reference patch applies.
It never silently substitutes the metadata base or downloads dependencies.

## Build and reproduce

Use a local Ibex git repository containing the pinned commits. A bare repository
is supported. The dataset and source checkout are separate inputs; no upstream
installation or preparation shell scripts are executed.

```bash
python scripts/convert_hwe_ibex.py \
  --dataset datasets/external/hwe-bench-data/hwe_bench_full.jsonl \
  --repo ../R3E-local-artifacts/hwe_sources/ibex.git \
  --output ../R3E-local-artifacts/hwe_ibex_development_v1 \
  --reproduce
```

The output must be new; existing runs are never overwritten. `--ids` selects a
subset of the three reviewed profiles. Unrecognized test-script hashes fail
closed. Without `--reproduce`, tasks remain ineligible with `not_run` validation.
Reproduction requires local Git, Verilator, a C++ compiler and make. The current
adapter uses the installed tool rather than recreating upstream Docker images;
the version is recorded and may differ from HWE's original environment.

The converter copies literal C++/SV heredocs and converts the upstream Verilator
command into an argument list, relocating only harness paths. It does not alter
the stimulus, assertions, expected values, RTL, or compilation flags. Runtime
build files live in temporary directories and are deleted after each variant.
Upstream repository licenses remain in the exported source tree.

## Data boundary and runner contract

Each task directory has:

* `public/task.json`: relative source/spec/test paths, repository family and scope.
* `public/repo/`: the complete pinned source tree, without git history. No
  reference fix or upstream test patch is applied to this Blue workspace.
* `public/spec.txt`: the exact upstream `problem_statement`, not the PR body or
  a specification inferred from the reference patch.
* `public/visible/`: exact extracted test sources plus `build.json`. Resolve
  `{harness}` to a scratch directory containing these sources, run its `argv`
  from the candidate repository, then run `obj_dir/<binary>`. Compile errors,
  runtime failures, timeouts and infrastructure errors must remain distinguishable.
* `oracle/`: original `fix.patch` and `test.patch`, evaluator-only.
* `manifest.json`, `validation.json`, `validation/`: evaluator provenance, scoped
  results, source hashes and logs. These are **not Blue inputs**.

Only `public/` may be copied into Blue's isolated workspace. Do not grant Blue
filesystem access to the artifact parent, source mirror, oracle, or reference
logs. Hashes and directory separation make the intended boundary reviewable;
they do not themselves implement runtime isolation. The measurement runner owns
and must test that isolation. Gold changed-file lists are deliberately absent
from the public task metadata.

The offline evaluator applies the upstream test patch to both scratch variants,
then the fix patch only to the reference. For these profiles, 1816 and 907 have
empty test patches; 2232 changes an upstream UVM test top that the extracted
directed build does not compile. Blue's new runner should preserve that distinction
and keep test support outside editable RTL.

Eligibility requires a successful baseline build, a nonzero simulation exit with
the reviewed target-failure marker, and a successful reference build/run with the
reviewed pass marker. A compilation error, timeout or unrelated crash does not
qualify. The two runs use identical extracted tests and tool arguments.

## Limits and next integration step

These directed tests establish local fail-to-pass witnesses, not full repository
regression, specification completeness, repair difficulty, or formal equivalence.
In particular, task 2232's harness includes upstream primitive stubs and exercises
the core; it does not compile every wrapper or configuration changed by the PR.
Multi-file ground-truth changes therefore do not establish that this visible
test rejects all incomplete repairs. Record elaboration/parameter coverage and
add independently specified checks before making a repair-completeness claim.

The Blue-side handoff is repository editing plus this build recipe and the
upstream textual logs. There is no generated clock/edge trace schema yet.
Supplementary verification, hidden-test policy, model budget and evidence format
must be frozen by the measurement implementation before paid evaluation.
No Part A or Part B call allowance is reassigned by this conversion.
