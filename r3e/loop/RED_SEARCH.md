# Bounded Red search and the 94-call diagnostic

The implementation is opt-in. Existing single-candidate freeform runs retain
their generation path; `--red-search --proposals 1` enables candidate search.
The pilot flags below also enable it. Blue repair, memory, confirmation, and
scoring implementations are reused without modification.

## Candidate search

One slot spends at most two Red calls:

1. Select a compatible discovery design and target, a primary mutation plan,
   up to two alternatives, and a hypothesis about a specific incorrect repair.
2. Return one final mutant after receiving local admission results. It may use
   a checked candidate or introduce one revision; the revision is checked too.

The original interface and 40% code/body token limits apply to every candidate.
The existing visible-test, compilation, loop, and duplicate rules apply. Hidden
tests are not run for portfolio admission. Only the final candidate is committed
to the duplicate set. There are at most four distinct candidate admission checks
per slot, with cached checks for repeated candidates. Clean-reference simulation,
loop analysis, and the existing final stealth measurement are additional local
work, not additional model calls. Local feedback reports admission reasons,
actual diffs, and budget measurements; it does not claim recoverability or
predict Blue success with a measured score.

The final output records the broken invariant, a predicted incorrect repair,
its residual failure, and `predicted_wrong_repair_edits`. Those edits apply to the
**final mutant**, in order, using the same exact-span edit syntax. They do not apply
to the clean design. The runner stores the materialized hypothetical repair,
its hash, and its actual diff. These remain model hypotheses. Admitted candidates
can still be excluded by independent specification/recoverability review.

Search v2 adds a generator-side necessary observability check before Blue runs.
The clean baseline must execute and pass; Yosys analysis must succeed without a
logic loop. The hypothetical repair must change code, preserve the interface and
directives, execute without a loop, and still fail the frozen visible test. If it
passes, the proposal is rejected as
`target_not_observable_after_predicted_repair`. Invalid edits, unchanged code,
analysis failures, and non-executable repairs are reported separately. The
original four-mutant check bound is unchanged. There is at most one additional
hypothetical-repair simulation per final admitted mutant, plus baseline validation
cached by clean/spec/test/dependency inputs. There are still at most two Red calls.

This check does not prove that a residual is the claimed mechanism, or that Blue
will produce this repair. Review must reject hypotheses that introduce an unrelated
error to manufacture a residual. No tests, scoring or confirmation rules change;
no hidden tests are opened by this check. A new test suite would require a separate
prospective freeze. The original pilot's scores remain intact.

The parser tolerates only the nonsemantic extra annotation `type: "json_object"`;
the raw output and ignored annotation are preserved. Other extra fields and target
identity mismatches remain invalid. Output, search, context and artifact versions
are frozen, so an old search directory cannot silently resume with this protocol.

Aware search sees bounded public observations: the latest six proposals and
up to six older cases relevant to offered targets. They include rejected
generation, first-attempt repairs, functional misrepairs, no-answer attempts,
compile failures, and inconclusive runs as distinct facts. Only completed prior
discovery rounds are visible. Blue edit summaries are labeled model explanations,
not verified source changes. During new discovery rounds, an integration adapter
also archives Blue's actual candidate RTL after the original simulator returns its
unchanged verdict. Red consumes only candidates linked by hash and carrier ID to
completed prior discovery encounters and admitted challenges. The resulting diffs
are against the original challenge, not necessarily the preceding attempt. Missing
old artifacts remain missing; no private simulation directories are scanned.
Diffs are capped at 2,000 characters per attempt and 12,000 per view, newest cases
first, with explicit truncation. Hidden results, memory, and prompts are excluded.
Passing means passing the visible test, not proven functional correctness.
Blind search reads no history. Hypotheses supported
by observations do not become confirmed weakness IDs merely by being mentioned.

Search v2 interleaves discovery carriers by source-grounded strata (single/multiple
modules, parameter declarations, explicit clock edges), preserving frozen rotation
within each stratum. Menus remain identical for aware and blind under the same
pool/settings and do not depend on outcomes. These are lexical source facts, not
effective elaboration or confirmed semantic applicability. The generator remains
free-form, with no minimum edit size or operator catalog. Existing non-search and
holdout scheduling paths retain their prior logic.

The existing generated-carrier manifest contains four multi-module designs:
RTLLM adder_8bit, adder_16bit, adder_32bit, and barrel_shifter. They are available
only when that manifest is explicitly included; this change does not move carriers
between splits or add them to a ChipBench-only run. Repository-wide multi-file
mutation is not implemented: the mutable unit is still `clean_rtl`, which may
contain several modules; `deps` remain read-only. The new strata and actual-patch
feedback make diversification and feedback-conditioned generation testable, but
do not establish difficulty, adaptation, or transfer without a new experiment.

## Seed validation and branches

The pilot imports one historical admitted Red mutant with a PASS review. It
verifies its RTL/spec hashes against the current eligible corpus and requires
the seed cluster to belong to discovery. It does not move a frozen holdout
design into discovery. Plan with a prospective split explicitly if needed.

The seed gets three independent, current-configuration Blue encounters, even
if the first succeeds. Each allows three repair attempts and no escalation.
Every materialized seed candidate is now saved through the same hash-bound public
candidate recorder as discovery, under `seed/public_repair_candidates/`. Seed
encounter rows include the carrier ID and remain marked `counts_as_new_hit=false`.
The supplementary verifier recognizes seeds through `pilot_config.json`'s frozen
provenance without fabricating a Red admission entry. It reports distinct seed
run IDs and rejects a changed reference hash. No-answer attempts have no RTL.
Candidate capture does not alter the returned simulator verdict, confirmation
budget, or seed gate. Old exact candidates may be recovered by matching their
recorded hashes, with recovery provenance kept separately from original ledgers.
Two failed runs out of three complete runs reproduce the budgeted failure.
The warm branch additionally requires at least one functional wrong attempt
in a failed run: pure no-answer failures are reported separately and do not
establish a functional target. The seed is never a new hit.

Cold and warm have separate state directories. Both exclude the seed design
cluster from generation, including other ChipBench bug types of that problem.
Cold imports no seed or warm history and has two slots. Warm receives the new
seed-validation observations and has five slots. Each warm slot must pursue the
seed mechanism on an unused discovery cluster or abstain; it cannot quietly
become exploration. Negative follow-ups remain visible. The five predeclared
probes remain available even when a follow-up is easy; this does not change its
recorded lineage status into a failure or imply the seed transfers.

If the seed is not usable, warm stops and cold can still run. If the first three
warm slots admit no new-design bug, warm stops. Here admission means the existing
automatic gates; a counted hit still requires independent PASS review. Menus keep
the versioned history-independent discovery rotation described above. Lack of an applicable target is
recorded as `no_applicable_seed_target`, with no second Red or Blue call.

## Calls and persistence

| Stage | Normal-operation call allowance |
| --- | ---: |
| Seed: three runs of up to three attempts | 9 |
| Cold: two slots of up to two Red plus nine Blue calls | 22 |
| Warm: five such slots | 55 |
| Shared operational reserve | 8 |
| Shared hard cap | 94 |

Existing configured infrastructure retries consume the reserve and the same
cap; they are never free. Before starting a slot, the controller reserves room
for two Red calls and three complete Blue encounters including configured
retries (17 calls with the default two retries). It may stop early if a whole
step no longer fits. Early success or rejected generation does not create extra
slots. Unused allowance stays unused. This is a search diagnostic, not an
estimate of autonomous discovery rate or a paired memory-gain experiment.

`pilot_config.json` freezes settings, seed/review hashes, split membership,
source/test hashes, prompt identity, and implementation hashes. The root
`calls.jsonl` is the authoritative shared budget ledger; branch copies are for
per-branch reporting and must not be summed again with the root. Costs are
restored from the root ledger when resuming a completed step.

`pilot_events.jsonl` journals step start/completion. Completed steps are not
reissued. An interrupted step with no completion receipt refuses automatic
replay; its partial encounters are inconclusive until reviewed. This prevents
silently spending twice after a lost response. Do not delete the journal to
resume. Seed, cold, and warm records live in their own subdirectories; only the
warm lineage ledger contains an explicitly marked `seed_import` event, and its
seed encounters are not inserted into new discovery encounters/screens.

## Review commands

From the repository root, preview the real reviewed sequence seed without
calling a model or creating a run directory:

```bash
python -m r3e.loop.cli \
  --state-dir ../R3E-local-artifacts/red_search_pilot_v1 \
  --manifests datasets/manifests/chipbench89.jsonl --carrier-manifest \
  --curriculum --red-source freeform --red-mode aware --no-learning \
  --budget-k 3 --escalation-k 0 --proposals 1 --max-calls 94 \
  --red-pilot-seed-run ../R3E-local-artifacts/partA_chipbench_20261005/aware \
  --red-pilot-seed-id RC_406d811b4d61a2 \
  --red-pilot-decisions ../R3E-local-artifacts/partA_chipbench_20261005/aware/hit_decisions.json \
  --plan
```

For a scripted dry run, replace `--plan` with `--fake` and choose a separate
fresh directory. The default fake Blue repairs the seed, exercising the warm
stop path. Fake Red deliberately proposes a full reversion as its hypothetical
repair, exercising the new rejection path instead of claiming a difficult bug.
`tests/test_red_search.py` exercises all seven generation slots
and seed validation with real Icarus/Yosys checks and a scripted failing Blue:
that scenario uses 86 fake calls and verifies exact completed-step resume.
The v1 command is historical: select a new directory for v2, and predeclare a
new allocation before using the 71 remaining paid calls. They are not launched
or reassigned by this implementation.

For measurement, freeze the intended Blue reasoning, answer format, register
visibility, model, and token cap explicitly. Real calls continue to require
`--allow-real-calls`, a positive cap, and the configured provider environment.
Do not reuse a fake directory for a real run. No paid experiment is launched by
the implementation or these tests.

```bash
python -m pytest -q tests/test_red_search.py tests/test_freeform_curriculum.py tests/test_red_design.py
python scripts/check_release.py
```

The search rationale is in [RED_SEARCH_DESIGN.md](RED_SEARCH_DESIGN.md).
Blue failure representation remains separate work described in
[FAILURE_REPRESENTATION_DESIGN.md](FAILURE_REPRESENTATION_DESIGN.md).
