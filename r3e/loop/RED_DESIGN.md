# Free-form Red generator

`red_design.py` implements the model-written mutation step. The model receives
the clean RTL, its specification, a selected Blue weakness, and the caller's
public pre-bug weakness context. It returns explicit source edits (preferred)
or a complete modified RTL design; the runner always materializes complete RTL.
There is no site list, operator catalog, or maximum number of edit locations.

The generator remains independently callable. `freeform_curriculum.py` now
connects it to the curriculum behind the explicit `--red-source freeform`
option. The default catalog and public bug pool paths remain available.

## Interface

```python
from r3e.loop.red_design import EditBudget, RedDesignGenerator

# client is the existing BudgetedClient, shared with the run's call ledger.
red = RedDesignGenerator(client, budget=EditBudget(max_changed_fraction=0.40))
proposal = red.generate(
    carrier,  # Carrier: clean_rtl, top_module, nonempty spec, identity
    target_weakness={
        "weakness_id": "W_missing_transition",
        "mechanism": "misses a missing state transition",
        "causal_chain": [
            {"failure": "never returns to idle",
             "attempt": "changes output decode",
             "why_failed": "the return transition remains absent"}
        ],
        "mastery_status": "not_mastered",
        "designs_used": ["previous_design_cluster"],
    },
    weakness_context={
        "mechanism_coverage": {
            "missing state transition": {"tested": 3, "failed": 2}
        }
    },
    direction="harder",  # explore | same | harder | simpler
    seed=7,
)
```

Only `weakness_id` and prose `mechanism` are required in the target object;
other JSON fields are passed intact. The context format is deliberately open
for the caller's causal-chain and mastery representation. No state database,
hidden tests, future outcomes, or memory store is read by the generator.
The input is frozen before the call. The caller must provide a public view
containing only evidence available before this bug and select a different
design cluster for a transfer/mastery test.

The model returns the four explanation strings below and exactly one of
`edits` or `buggy_rtl`:

| Field | Meaning |
|---|---|
| `buggy_rtl` | Complete modified source, without Markdown fences. |
| `edits` | Ordered arbitrary-size `{find, replace}` objects; alternative to `buggy_rtl`. |
| `hypothesis` | How the mutation is expected to expose that weakness. |
| `expected_symptom` | Predicted functional failure; not a measured result. |
| `change_summary` | Structural and behavioral modifications. |
| `spec_basis` | The supplied requirement that makes the intended repair knowable. |

The returned proposal also includes `status: proposal_only`, carrier ID,
direction, runner-assigned `target_weakness_id`, materialized `buggy_rtl`, actual
unified diff, raw model output, measured `edit_budget`, and a `receipt`. The model
is not asked to reproduce opaque target IDs. A legacy echoed ID is accepted only
if it matches; a mismatched echo is still rejected. The receipt
binds the prompt and seed, clean design, specification, weakness view, provider
request and raw response hashes, and input/output token counts. It does not
contain credentials. `spec_basis` is a model assertion for review, not proof
that a mutation is admissible.

## Destruction limit

The default ceiling is **40%**, configurable strictly between 0 and 1. There
is no unlimited setting. The generator applies the ceiling to two token diffs:

1. All RTL tokens, excluding comments, whitespace, and compiler directives.
2. Module-body tokens, additionally excluding module headers/end markers and
   module input/output/inout declarations. Internal declarations, function/task
   bodies, helper-module bodies, constants and signal names still count.

For each diff hunk, the cost is `max(deleted_tokens, inserted_tokens)`.
`SequenceMatcher(autojunk=False)` determines the hunks; this is a deterministic
syntactic metric, not minimum edit distance. The total cost must be at most
`floor(original_token_count * max_changed_fraction)` for **both** views.
The model sees both integer limits before generating. The proposal records
the original/candidate sizes, cost, ratio and limit for each view.

Formatting does not spend or enlarge the budget. Added code costs tokens;
candidate padding never increases the denominator. Large unchanged interfaces
cannot hide destruction of the module body. Top-level header tokens and
non-ANSI port declarations must remain identical, including public parameter
defaults. Compiler-directive operands and order must remain unchanged.
Helper deletion, internal-definition deletion, rewiring and coordinated
multi-location changes are permitted within the budget. A formatting-only
proposal or missing/empty top module is rejected. If the budget permits zero
token edits on a tiny design, input validation fails before spending a call.

This bounds syntax changes, not semantic damage: a small enable or reset edit
can still affect every output. It also does not prove that enough useful
functionality remains, or that a bug is stealthy. The external judge must
assess observable damage and recoverability. The lexer is conservative, not
a complete SystemVerilog frontend; unsupported syntax is rejected, never
accepted with an unmeasured budget. All 148 currently eligible generated
carriers pass its source preflight.

## Integration and failure handling

Each `generate` call makes exactly one budgeted provider call in phase `red`
after input validation, with no retries or extra ranking calls. It restores
the previous budget phase. Model proposals that violate the output schema,
target identity, interface or budget raise `RedDesignOutputViolation` with
`reason`, `details` and the billed call's `receipt`. These are rejected Red
proposals, never Blue repair failures. `RedDesignInputError`, invalid budget
configuration, call-cap exhaustion, and provider/transport errors propagate;
the caller must not manufacture a candidate or reward from them.

An optional `mutation_plan` carries concrete source edits, the broken invariant,
a legal activation sequence, and the specification basis. The runner applies
the plan locally and reports edit applicability and both token-budget checks
to the generation call. This is feedback within the existing call schedule,
not an automatic retry or functional admission. The second call may revise an
over-budget or inapplicable plan. The final candidate still passes the same
40% checks. Edited and full-source outputs have the same modification space.

Before admitting a proposal, the caller's judge must check compilation with
the real dependencies, visible-test failure, absence of introduced logic
loops, recoverability from the original specification, and duplicate status.
The generator neither executes RTL nor claims these checks passed. It does
not mutate the specification or return replacement tests. The intended task
remains repair to the original specification; requiring the buggy behavior
itself to satisfy that specification would prohibit a functional bug.

Compute stealth from simulation (wrong-cycle fraction and first wrong cycle),
and reward only reproducible Blue failures (at least two of three completed
runs). Configuration/provider failures must not count. Evaluate mastery using
a subsequent proposal for the same mechanism on a different design. Those
policies and Blue's structural-edit format belong to the surrounding workflow.
The curriculum adapter below reuses that workflow and provides an initial
public-history adapter that can be replaced by the caller.

Edit application reuses the existing pure `apply_text_edits` helper. Exact
unique spans are preferred; its existing whitespace tolerance is preserved.
Deletion and appending are supported, and all edits are applied in order.
An ambiguous or missing span rejects the candidate. This does not change Blue's
edit implementation, and the materialized result must satisfy `validate_design`.

Local checks (no external model calls):

```sh
python -m pytest -q tests/test_red_design.py
python scripts/check_release.py
```

## Curriculum connection

```sh
python -m r3e.loop.cli --state-dir RUN --curriculum --red-source freeform \
  --manifests --red-max-changed-fraction 0.40 --no-learning \
  --rounds 2 --proposals 3 --plan
```

`--plan` performs no model calls. `--fake` exercises the same routing with a
scripted local mutation and the existing fake Blue. Real calls still require
the CLI's explicit authorization and positive shared call cap. Both aware and
blind Red are supported; random remains a catalog/pool baseline. Targeted
holdout generation requires aware Red. Missing-spec carriers are excluded
from this generation path, and an empty specified discovery pool is an error.

`FreeformCurriculumLoop` inherits the existing Blue repair, confirmation,
mastery, qualification and memory workflow. Its generation path is:

1. Offer a rotating menu of discovery designs (default 8; configurable with
   `--red-design-candidates N`). Each includes full clean RTL, specification,
   token budgets, visible horizon, and eligible lineage IDs. Eligibility only
   enforces unused design clusters; it does not assert mechanism compatibility.
   The menu/order depends only on the frozen discovery split, seed, round and
   slot, and is identical for aware and blind arms with those settings. Small
   pools offer every design and rotate their order. Larger pools sweep windows.
   A lineage is pursued at most once per round, as before.
2. Make one Red call to jointly select `carrier_id`, an offered weakness and
   direction (or a new exploration hypothesis), AND exact `mutation_plan` edits.
   `applicability` supplies `rtl_anchors`, a nonempty array with one contiguous
   source span per location, and a reason connecting the
   selected design to the mechanism. Invalid IDs, reused lineage clusters, and
   absent source anchors stop before generation. Exploration in the presence of
   offered weaknesses requires `skipped_weaknesses` reasons for every offered
   weakness, covering its eligible menu designs. These assessments are logged.
   Both token budgets and the known visible sample horizon are supplied before
   planning. The mechanism must be grounded in this design, not a new algorithm,
   protocol, interface or testbench. A cold start is labeled exploration.

The `grounding_v3` selection contract makes the target vocabulary explicit in
every request. Only `open_weak_points` supplies offered weakness IDs;
`mechanism_coverage` also describes bugs Blue repaired and is not a target list.
With no offered IDs, exploration sends `skipped_weaknesses={}`. If an answer adds
coverage explanations anyway, they are preserved as
`ignored_skipped_weaknesses` and the effective skip map is normalized to empty.
When exploring with offered IDs, a nonempty reason is still required for every
offered ID; coverage prose cannot replace those reasons. Pursuit has no skip
obligation. The original `selection_output` is never rewritten.

Each `rtl_anchors` entry is matched independently, allowing token-preserving
formatting differences but no changes to identifiers, operators, literals or
string contents. Do not concatenate separate sites with ellipses or labels.
Legacy single/composite `rtl_anchor` answers are supported: composite citations
can be reconstructed only from planned `find` spans that actually occur in the
source and in the submitted citation. Anchors are citations and need not be
unique edit locations. An ambiguous `find` still receives `revise_plan` feedback
in the existing generator call, and final edits must pass the unchanged edit
application and mutation checks. No extra model call is added.

For a read-only replay of stored selectors (no model or simulation calls):

```sh
python scripts/replay_freeform_selection.py --red-ledger RUN/red.jsonl \
  --manifest datasets/manifests/chipbench89.jsonl --report /path/to/replay.json
```

The replay reports grounding acceptance and local planning feedback only. It
does not generate bugs, admit them, run Blue, or revise historical results.
3. Make one `RedDesignGenerator` call with that concrete plan, locally computed
   planning feedback, target and frozen public view. It implements or revises
   the plan, preferably through explicit edits. Invalid final model output is a
   rejected Red proposal; configuration, transport and call-cap errors stop.
4. Pass the candidate through the optional external `proposal_check`, then
   the existing `RedAgent._admit`; measure stealth through `Simulator.stealth`.
   Accepted challenges enter the inherited repair/confirmation workflow.

There are at most **two Red calls per discovery slot**, with no internal retries;
the CLI call estimate includes both. A larger menu increases input tokens, not
the number of Red calls. There is one generated mutant, so this path does
not simulate and rank several hidden candidate generations. Known-target
holdout uses one generation call with direction `same`, on a frozen holdout
carrier outside the lineage's design clusters. This scheduling change covers
**discovery slots** only; the existing one-call holdout helper still assigns one
unused holdout carrier. Do not interpret its assignment as verified mechanism
compatibility.

Generation occurs before Blue evaluation within each existing curriculum
round. All slots see only completed earlier discovery rounds; previous slots
in the same batch have no Blue outcomes yet. The view includes actual visible
repair attempts/feedback, confirmation counts, measured stealth, mastery
state, and mechanism coverage. Current/future-round and holdout records are
excluded. Mechanism prose is explicitly labeled as Red's hypothesis, not a
verified causal diagnosis. Blind Red receives an empty weakness view.

The runtime owner's richer view can be supplied as
`FreeformCurriculumLoop(view_builder=..., view_version="your-version", ...)`.
Its signature is `(state, offered_lineages, round_index) -> dict`, with
`open_weak_points` containing `weakness_id` and `mechanism`, and optional other
public context fields. Target IDs must belong to offered lineages. The entire
JSON view is frozen, recorded, and passed to both Red calls unchanged.

`proposal_check(carrier, proposal) -> None | rejection_reason` is an optional
runtime-owned admission extension; provide `proposal_check_version` when using
it. In particular, this is where the owner's specification-recoverability
review can be connected. The default CLI reuses existing compile/visible-test/
logic-loop/duplicate checks; **it does not add or claim an automatic semantic
specification-recoverability judge**. `spec_basis` alone is not that judge.
No Blue repair, scoring or measurement logic was replaced by this adapter.

Complete candidates, selection/generation receipts, budget measurements and
the pre-bug view are stored in the existing `red` ledger. Rejected generation
outputs also retain the original model JSON, materialized RTL when available,
actual diff and planning feedback, including no-change and over-budget answers.
Selection output is retained even if invalid. The candidate menu identities,
source/spec hashes, per-design eligible IDs, applicability and exploration
explanations are recorded as well. Scheduler version and menu size are frozen;
old scheduler checkpoints cannot silently resume under this design.

An exact source anchor only verifies that quoted code exists, not that Red's
mechanism interpretation is correct. A finite menu may contain no compatible
new design; exploration is then explicit rather than mislabeled pursuit. Increase
`--red-design-candidates` to cover the whole discovery pool when that is affordable.
This enables a meaningful pursuit opportunity; an adaptation claim still requires
actual targeted follow-ups and controls for selected-design difficulty. The historical
`edit_kinds`/`operators` fields carry opaque mechanism IDs solely for compatibility
with existing lineage records; they never restrict the generator's edit space.
Generation has no dependency on `enumerate_sites` or `apply_edits`.

Resume verifies original RTL/specification and candidate hashes, then restores
the exact source and duplicate registry. Completed/rejected slots are reused
without regenerating. Budget, prompts, input hashes and hook versions are
frozen across checkpoints. A corrupted candidate causes an error rather than
silent omission. This is proposal replay, not an exactly-once transaction for
interrupted external requests: interruption before a completed proposal is
recorded may require another call, still subject to the call cap. The inherited
repair workflow retains its own resume behavior.

Holdout candidates are recorded for replay but do not update discovery
encounters, episodes, screens or lineages. A resumed holdout reuses its exact
candidate and spends no new generation call.

Adapter checks:

```sh
python -m pytest -q tests/test_freeform_curriculum.py tests/test_red_design.py
```

## Generator revision after the first blind pilot

The `freeform_pilot_blind_v2_20261005` ledger contains 20 proposals: 6 admitted,
7 unchanged, 3 invisible to the visible test, 3 over budget, and 1 identity
mismatch. Admitted proposals changed 1, 1, 2, 4, 6 and 8 tokens (median 4.5% of
whole-design tokens). All 20 were blind exploration; they did not exercise
known-weakness pursuit. The six admitted bugs were all repaired on the first
attempt in the reported pilot.

Source evidence identifies several planning failures: a self-checking testbench
was proposed as the mechanism; a handshake was proposed for a design without
backpressure; another target proposed replacing the multiplication algorithm;
one final answer explicitly described a functionally equivalent refactor.
The first call previously had neither a concrete-edit requirement nor the token
budgets. These are generator contract defects, distinct from admission failure.
The seven unchanged raw answers were not retained by the old rejection path,
so their individual rationale cannot be established from that ledger.

The revised planner must identify actual source edits and a concrete behavioral
difference. The generation call receives local applicability/budget feedback
and writes explicit edits or a full file. A known generated visible harness's
sample count is supplied to avoid planning a trigger beyond the run; unfamiliar
harnesses are marked unknown, not guessed. Hidden harnesses and expected traces
are never read for this context. The invariant and activation remain model
predictions; the existing judge determines whether a functional bug exists.

Freeform Red now defaults to `--red-thinking low`; catalog/pool Red retain
`disabled`. An explicit `--red-thinking` always overrides the default, and
`--plan` prints the effective freeform setting. This is a bounded experimental
choice, not evidence that disabled thinking caused the pilot failures. The
previous pilot did not compare thinking modes. There is no added model call or
automatic retry, no relaxed edit ceiling, and no new rule treating a larger
mutation as harder. Difficulty/admission improvements require a new real pilot.
Use a new state directory: the changed prompts are part of frozen run identity.
