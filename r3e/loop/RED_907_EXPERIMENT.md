# 907: from a false repair to cross-design challenges

Prospective development protocol, 2026-10-08. This is a design decision, not an
implemented runner or approval for model calls. Original HWE results remain frozen.

## Question and evidence boundary

Can Red abstract an observed repair mistake and generate valid challenges on
different designs that independently sampled Blue repairs mishandle by the same
mechanism? Downstream improvement from experience is a separate, later question.

Source: HWE development task lowRISC__ibex-907, repeat index 1. The archived Blue
repair admits a fetch whenever no response is outstanding, even when the FIFO
cannot hold another response. The reviewed backpressure sequence demonstrates
data loss. One erroneous patch establishes an observation, not a repeatable model
weakness, automatic discovery, or transfer. Selecting this source was human work.

Local provenance and fresh offline replays are in the local artifact directory
`red_907_mechanism_dev_20261008/`, alongside the original
`hwe_dev_run_20261007/review/`. The source candidate hash is
`sha256:ef4221f1cc9d2ec028156ab46fc908b7ee3e4ae7d3076c92055c834bfd69dc5a`.
The replay records hashes and text traces. Newly generated VCD files are offline
replay evidence, not waveforms recovered from the original model encounters.

## Two distinct obligations and witnesses

| Variant | W_bug: timely refill for a spanning instruction | W_preserve: backpressure and data correspondence |
| --- | --- | --- |
| Original faulty baseline | fail | pass |
| Upstream reference | pass | pass |
| Archived incorrect Blue repair | pass | fail |

W_bug establishes the original repair task. W_preserve exposes a regression
introduced by the repair. Passing W_preserve does not make the original baseline
correct. Failing W_preserve alone does not establish a valid Red-generated task.

For a new carrier, establish its clean implementation and unchanged specification
first. A generated mutant must compile and fail a frozen public W_bug that clean
passes. For this focused experiment, also require the mutant to pass the public
W_preserve: the intended challenge begins with a progress defect and requires
preserving capacity/data safety during repair. Log candidates outside this
subclass as rejected; do not silently discard them or call them invalid in general.

An executable predicted shortcut is a separate diagnostic. To support the claimed
trap, it must compile, fix W_bug, and fail W_preserve through the claimed regression.
Other outcomes remain recorded. This diagnostic must not determine which admitted
mutants receive Blue trials: otherwise filtering may make the arms incomparable.
Red cannot substitute a second unrelated defect for the predicted repair mistake.
Here the public suite includes both W_bug and W_preserve. The predicted shortcut
therefore fails the public suite; it is not a visible-pass/hidden-fail repair.
This prospective visibility differs from the original frozen 907 encounter, where
the preservation witness was added only during post-hoc review.

Red may propose triggers; it cannot change the frozen checker, expected outputs,
specification, or input legality rules. Preserve capacity and accepted-request /
delivered-response correspondence, not internal bit-for-bit implementation identity.
Accounting must include the carrier's bypass, simultaneous consumption, cancellation,
and reservation semantics; do not impose a universal occupancy formula blindly.

## Three arms and an explicit information boundary

Use identical carrier slots, generator tools, mutation ceilings, public tests,
Blue settings, and per-arm collection caps. Interleave arms in a frozen order.

| Arm | Red history input | Interpretation |
| --- | --- | --- |
| ordinary | no 907 history or target-mechanism summary | ordinary generation on the same opportunity set |
| automatic | a mechanically assembled source packet, followed by one model abstraction call | automatic abstraction of a human-selected, observed error |
| manual | the same source packet plus a frozen human mechanism summary | diagnostic assistance, not automatic discovery |

The automatic packet includes the original public task/spec/build context,
faulty source, actual Blue edit/description, visible feedback, and the reviewed
legal source regression stimulus with observed/expected transactions. It excludes
upstream fix text, reference RTL, review prose explaining the mechanism, and any
other arm's output. The source regression is explicitly declassified development
evidence; no formal evaluation counterexample is declassified. An abstraction
from this packet cannot be described as inference from the original visible pass
alone. Do not invent missing model reasoning or provider messages.

The extractor returns source evidence IDs, the attempted decision, violated and
preserved obligations, applicability preconditions, and predicted misleading
repair. Freeze its first well-formed output; no human semantic correction. A
format/provider failure follows the frozen retry rules, with no manual fallback.
Record extraction costs and success/failure. A schema-only check cannot certify
the causal interpretation.

Manual summary: "To restore request progress, a repair may relax admission based
only on outstanding requests, without reserving capacity for responses already
stored or still to arrive. Preserve delivery order and data/address association
under downstream backpressure while meeting the specified progress requirement."

The ordinary arm is still told the carrier's full correctness requirements.
Do not smuggle the 907 hypothesis into its generator prompt or alter its feedback.
Diagnostic predicted-repair fields use identical, mechanism-neutral schemas in
all arms. Ordinary Red may discover this mechanism independently.

## Carrier selection and isolation

Start with three independently implemented development carriers and one proposal
slot per carrier per arm. Select and freeze them before observing new Blue results.
Required public behavior: specified request progress, finite response buffering,
outstanding transactions, and legal downstream backpressure. Confirm that the
original problem and the preservation obligation are knowable from the spec.

Candidates for offline screening are a read-response buffer, a DMA/read-ahead
queue, and a credit-controlled response path. These are capability categories,
not acquired or qualified datasets. No paid run starts until actual source commits,
licenses, specifications, build closures, and both witness suites are recorded.
Check implementations and ancestry, not names alone; renamed/parameterized Ibex
copies do not count as cross-design transfer. Existing excluded ChipBench FIFOs
remain excluded. A name search of the current manifests is not a mechanism audit.

All three arms receive the same carriers. Their clean implementations must pass
the public suites and independently authored evaluator suites before collection.
The latter are frozen before Red proposals and never used for mutation revision,
admission feedback, predicted-shortcut selection, or Blue retries. Inspect them
only after collection; public W_preserve remains available to both Red and Blue.

Keep source family S (907), Red-development families D, and eventual downstream
evaluation families E disjoint, including clones. Reserve E before constructing
experience; do not inspect E to choose prompts, memories, thresholds, or carriers.
Development evaluator results may inform a later version but then require a new
freeze and fresh evaluation material. No formal holdout is selected by Blue failure.

## Actual Blue trials and outcome definitions

Run three independent memory-free Blue encounters for every admitted mutant, each
with at most three repair attempts and identical context/output limits. No source
packet, Red hypothesis, predicted shortcut, or hidden verdict reaches Blue. Public
test failures may drive retries as usual. Hidden checks run only after collection.
Admission failure/abstention does not create replacement slots.

Record each attempted patch, but score reproducibility at the encounter endpoint:
the first visible-pass candidate, or the last materialized candidate when attempts
are exhausted. No-answer-only, infrastructure, and context-limit endpoints are
separate categories, not target-mechanism mistakes. An earlier wrong repair later
corrected is an intermediate error, not an endpoint failure.

Report two distinct endpoint categories, never a pooled failure count:

* `public_pass_hidden_fail`: the endpoint passes the entire public suite, including
  W_preserve, but fails a legal frozen evaluator check. Review its mechanism and
  report it as supplementary repair-quality failure. It cannot be the exact
  predicted shortcut already rejected by public W_preserve.
* `public_unrepaired_at_budget`: no public pass is obtained within the encounter's
  fixed attempt budget. Record the final candidate's compilation, W_bug and
  W_preserve results and reason separately. This category alone does not establish
  the target mechanism: compilation errors, the original defect left untouched,
  and unrelated functional failures do not qualify as target errors.

Reviewers first inspect code, protocol-legal traces and violated obligations with
arm and Red narrative masked. Then compare the independently assigned mechanism
to the predeclared target. A visible pass that violates a legal evaluator check
is an erroneous repair; retain its original visible-pass status alongside that
judgment. Solver exit text or unused-output differences cannot certify an error.

The primary **2-of-3 confirmed target challenge** uses only
`public_unrepaired_at_budget` endpoints with a reviewed target regression: an
actual Blue patch compiles, passes W_bug, and fails public W_preserve through
the predeclared capacity/data-preservation mechanism. At least two of the three
independent encounters must meet all these conditions. A predicted patch alone
never counts. Hidden-only failures remain a separate secondary outcome and do
not contribute to this threshold; one public target failure plus one hidden-only
target failure is not a confirmation. Report individual and intermediate target
errors too, including those subsequently repaired. Two-of-three is an operational
rule, not an estimate of a stable probability. Do not rerun source batches or add
slots until it passes.

Report the full funnel by arm and carrier: scheduled slots, actual calls,
abstentions, malformed proposals, generated mutants, admission outcomes/reasons,
predicted-shortcut outcomes, Blue encounters, visible outcomes, supplementary
outcomes, reviewed target errors by endpoint category, and primary confirmed
challenges. Use both all scheduled
slots and admitted mutants as denominators. Report tokens and tools/wall time,
including rejection, abstraction, provider retries, and failed repairs.

## Budget and stopping

For three carriers x one slot x three arms, at most two Red proposal calls per
slot, and three Blue encounters x three attempts, with direct-context carriers:
18 generation calls + 1 automatic abstraction call + 81 Blue calls = 100 before
provider retries. This is an upper-bound schedule, not a budget authorization or
expected cost. Enforce equal per-arm caps that cover automatic extraction; the
other arms' unused extraction allowance never becomes an extra slot.

Freeze retry allowance, token/output limits, and monetary ceiling only after
carrier/context qualification and an offline plan. If localization is needed,
include its calls explicitly and recompute before approval. Reserve enough for
an entire slot's generation and all three Blue encounters before starting it.
Stop at the frozen schedule/cap, not after a favorable number of hits. Partial
collection, provider failures and unrun slots are reported, not replaced. Existing
Part A, Part B and HWE unused budgets are not assigned to this experiment.

## Implementation gaps and handoff

Existing `red_design.py`, `Carrier` and `TargetObservabilityCheck` operate on a
primary RTL text plus dependencies. Reuse this representation when a qualified
carrier supports mutation and repair within its primary file. Decide editable
scope from carrier requirements before generation or Blue outcomes; do not force
a genuinely coupled repair into one file. Add repository overlays only for
qualified carriers that require cross-file edits. This is conditional work, not
a prerequisite for the whole pilot.

Existing search instructions require the *same residual violation* after a
predicted repair and gate admission on that check. Those semantics must change
for this experiment's progress-to-safety regression and diagnostic-only shortcut
check, regardless of whether editing is single-file or multi-file.

Red-side work before execution:

1. Export an allowlisted source packet with provenance, no oracle/review prose;
   add the frozen extraction stage and three-arm input construction.
2. Reuse single-file mutation and repair where sufficient. Only when a qualified
   carrier requires it, extend mutations and predicted edits with file paths.
   In either case preserve public interfaces/configuration, candidate archives
   and deduplication. Retain the 40% syntax ceiling against the frozen eligible
   implementation; do not use unrelated closure files as edit-budget padding.
3. Represent `original_defect_obligation`, `preserved_obligations`, and
   `predicted_repair_regression` separately. In this new protocol only, record
   shortcut validation independently from common functional admission. Preserve
   existing experiment behavior and freeze a new version.
4. Connect admitted candidates to the matching existing single-file or repository
   Blue runner. Keep the same routing and editable scope across arms for a given
   carrier; freeze task/hash/suite identities and the full ledger.

Measurement-side work: qualify public and evaluator suites, implement the common
admission matrix and outcome schema, review legality and mechanism, and validate
budget accounting, isolation and replay without model calls. Temporary acceptance
scripts/builds are removed after testing; retain evidence and durable regression
checks needed by maintained implementation, subject to the agreed code policy.

## What a result would establish

Manual better than automatic suggests an abstraction bottleneck; it does not
prove one with three carriers. Both failing to produce valid tasks points first
to construction/applicability. Valid tasks that Blue repairs correctly provide
negative evidence for transferred difficulty. An automatic advantage over ordinary
Red on reviewed cross-design outcomes would support targeted generation within
this scope; a few selected difficult examples alone would not establish it.
The automatic-versus-ordinary contrast adds both source history and the abstraction
step. It estimates the effect of the combined method, not the isolated benefit
of the abstraction module. This pilot adds no arm to separate those components.
Manual assistance remains diagnostic, not proof of an abstraction-specific effect.

Challenge construction (C1) and downstream repair improvement (C2) are distinct.
C2 requires frozen experience and a third, untouched family set, compared under
matched budgets and verification against no memory and the best fixed-check
procedure selected on development data. Include retrieval and experience-building
costs. An automatic memory claim additionally requires automatic experience
construction. Do not claim capability grows with interaction from C1 alone.

## Prospective v2 collection amendment (2026-10-09)

The interrupted v1 pilot remains an immutable, infrastructure-incomplete record.
Its hidden outcomes are not used to choose v2. A new plan and source snapshot are
required; no v1 calls or unused A/B/HWE reserves authorize v2 collection.

All arms now submit primary-file edits, with separate predicted repair edits.
Full-file `buggy_rtl` answers are rejected during generation. Existing saved
full-text qualification examples remain replayable offline. Syntax/body ceilings,
comment preservation, public admission and diagnostic-only shortcut checks do not
change. A second proposal receives its submitted edits and public check results,
not redundant materialized copies of both designs. No feedback is silently truncated.

Proposal output allowance is 65,536 tokens; abstraction and both Blue phases retain
32,768. Total context remains 131,072, so proposals have at most 65,536 counted input
tokens. Every request is checked individually. Larger abstraction or feedback text
can cause a recorded `request_too_large` without an HTTP call. These limits apply
uniformly to the arms. A length-terminated proposal is `no_answer/output_budget`,
even if its partial content parses; it stops that slot's generation without a bonus
call. A truncated abstraction is unavailable, without a replacement extraction.

The maintained launcher is `experiments/loop_probes/red907_launch.py`. It checks
funding for the proposed CNY 120 envelope before creating a run directory, explicitly
requests Flash, and shares one transport across every arm and Blue phase. Sanitized
transport diagnostics are copied into the usage journal. Authentication/billing
halts and the third consecutive transport error abort collection, preserving the
ledger. Received responses, including output cutoffs, reset the transport streak.

The proposed cap remains 237 (79 per arm), with at most 235 executable calls and
no conversion of unused allowances into extra work. At the previously checked
peak tariffs of CNY 2/M input and 8/M output, the stage-specific schedule ceiling
is CNY 111.345664; including the two unusable reserve calls as 32k calls gives
CNY 112.263168, or CNY 114.594546 with the launcher's input-count tolerance. These
are conditional calculations, not billing guarantees; failed-request billing may
be unknown. Fresh independent review and spending approval precede collection.

## Low-cost screening protocol (2026-10-09)

The interrupted v2 collection is retained separately and is not resumed. The
screen uses fresh proposals and a fresh automatic abstraction; its inputs are
clean carrier packages and the original allowlisted 907 packet, never a v1/v2
proposal or repair. This is a new development screen, not a confirmation run.

Freeze the same three carriers, three arms, slot order and seeds, with one Red
proposal per slot and one memory-free Blue encounter of one attempt per admitted
mutant. No provider or format retries, replacement slots or implicit resume.
Losses from service errors are missing data, not generator or repair failures.
An unavailable abstraction leaves subsequent automatic slots unrun and recorded.
The shared authentication/balance halt and three-consecutive-failure halt remain.

Each slot reserves two calls. All arms have six generation/repair calls; automatic
has one additional abstraction call. Caps are ordinary 6, automatic 7, manual 6,
root 19. No localization calls are allowed: an admitted mutant whose current RTL
needs selection receives `blue_context_limit`, with no Blue HTTP request, and
stays in the scheduled denominator. Proposal/abstraction/Blue output limits remain
65,536/32,768/32,768; per-request context checks remain mandatory.

The proposed spending envelope and startup balance check are CNY 12. Using the
previously checked frozen peak rates (CNY 2/M input, 8/M output), nine full-size
proposals and ten full-size 32k calls cost CNY 10.485760 before input-count tolerance.
The usage guard reserves the request's maximum exposure before HTTP and retains
that reservation when failed-request billing is unknown. No extra retries are
purchased from savings. Offline review and a separate go-ahead precede launch.

Outcome rules are fixed before collection:

- Primary signal candidate: an actual single Blue patch compiles, passes public
  W_bug, and fails W_preserve with the checker's failure marker. A masked TARGET
  review is required to label it a reviewed target-mechanism signal.
- Public pass / hidden fail: a separate supplementary outcome requiring a legal
  checker violation. It never contributes to the primary signal count.
- Public pass / hidden pass: no mismatch within the frozen checks; not a proof.
- Public pass / hidden inconclusive, replay mismatch, unrelated or untouched public
  failures, no usable candidate, provider/tool errors and context/output limits
  retain separate categories or recorded subreasons. They are not target signals.

No screening outcome is a 2-of-3 confirmed challenge or establishes reproducibility.
Reports retain all nine scheduled slots and the admitted-mutant denominator, plus
actual calls, tokens, service losses and review status. Any later repeat-confirmation
experiment needs a separately frozen plan and budget; screening results are not
silently pooled with it. Real hidden checks remain post-collection only.
