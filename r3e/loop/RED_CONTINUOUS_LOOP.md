# Continuous round-based Red/Blue adaptation

Prospective design, updated 2026-10-10. This records the user's clarified objective;
it is not a frozen execution plan or spending approval. A callable scheduler and
fake-client acceptance are described in CONTINUOUS_LOOP_IMPLEMENTATION.md.
Existing results and archived run configurations remain unchanged.

## Research order: C1 first, C2 and C3 explain the loop

The motivating question is whether bounded RTL repair can be complemented by an
automatic process that seeks gaps in the current repair strategy and uses verified
interactions to support later adaptation. This is a research hypothesis, not a
claim that all existing RTL repair systems ignore history or exploration.

- C1: condition Red on the committed current Blue state, construct legal
  challenges, execute real repairs, validate the interaction, commit an update,
  and challenge the updated state in the next round without human selection.
- C2: valid interaction evidence does not automatically become reliable,
  transferable advice. Qualification and contextual activation must address
  mistaken interpretation, overgeneralization, policy drift and inapplicability.
  Public-pass episode storage is the initial baseline, not a completed C2 method.
- C3: compare conditioned opposition, experience handling, exploration views and
  computation under matched budgets. More calls alone can improve search; a
  multi-view gain cannot be inferred without that control.

The immediate implementation priority is C1's autonomous transition, including
explicit no-update states when evidence is inadequate. C2 and C3 do not block a
minimal C1 loop, and passing integration tests does not establish any contribution's
empirical benefit. A validated functional episode, a qualified strategy rule and a
demonstrated performance gain are different evidence levels.

The completed v2 paid pilot already executed three automatic rounds. It established
state continuity and actual experience delivery, but its Red prompt contained only
earlier repairs; the incoming snapshot hash was recorded outside the prompt. It
did not explicitly expose which experience the current Blue policy would select.
In v3, Red additionally receives a bounded, source-authenticated projection of the
incoming Blue state. The shared spec-only selector produces both this preview and
Blue's actual delivery. Only earlier online public episodes appear; no anchor,
reference or evaluator results do. Actual task-bound delivery is still recorded
separately. This makes the conditioning inspectable without predicting a repair.

The preview includes state identity, repair budget, update/retrieval policy and
the selected provisional episodes. It has a 6,144-token ceiling including policy
metadata, in addition to the 8,192-token observation bank and full request limit.
Whole-entry selection remains capped at two entries and 4,096 payload tokens.
An oversized preview is archived and refused before any model call. This adds no
model-based extraction calls and changes neither Blue's update nor repair format.

The present admission matrix still restricts challenges to the qualified carriers'
public progress defects while preserving their safety suite. C1 currently operates
within that declared space; v3 does not claim general RTL weakness coverage.
Broadening this space requires its own public construction rules and checks, not
quietly changing a frozen experiment. No new paid run follows automatically.

The measurement reference points and Blue handoff are fixed in
[CONTINUOUS_BLUE_INTERFACE.md](CONTINUOUS_BLUE_INTERFACE.md). This is an interface
contract used by the Blue runner and the new continuous scheduler.

## Objective and state

Each round challenges the Blue state committed at the end of the previous round.
Red adapts from actual repair observations; Blue carries repair experience into
later rounds. Red does not predict a particular wrong patch. Neither side's model
weights change in this version: adaptation means changes in explicitly recorded
external state and the context actually delivered to the model.

- `R_t`: observed challenges, actual public repair outcomes, unresolved hypotheses,
  confirmed findings where available, coverage, and prior generation decisions.
- `B_t`: fixed model/settings plus versioned repair experience, retrieval rules,
  and the set of experience entries eligible for delivery.
- `C_t`: fresh admitted challenges generated in round t.
- `E_t`: actual patches, public checker results, costs and operational outcomes
  observed while the frozen `B_(t-1)` attempts `C_t`.

The transition is:

```text
(R_(t-1), B_(t-1))
       -> Red generates C_t
       -> admission
       -> frozen B_(t-1) repairs C_t
       -> record E_t
       -> commit R_t and B_t at the round boundary
       -> next round
```

Continuity is in the agent states, not in accumulating damage in one RTL file.
Each new challenge starts from a qualified clean carrier with an unchanged spec.
Within one repair encounter, successive attempts may build on its current patch.

### Carrier schedule decision

For the next small pilot, fix one carrier for all three online rounds. The plan
records `carrier_mode=fixed` and `fixed_carrier_index`; the index cannot change
after observing results. Red can revisit the same design against B_0, B_1 and B_2,
using a different admitted mutant each time. Exact/normalized duplicate and anchor
collision checks still apply, and exposure must still be reported. Revisiting a
repair span is allowed; successful recall is not automatically novel adaptation.

Use ZipCPU for the prospective paid pilot as the smaller-context carrier, not
because of a selected failure outcome. Retain the three fixed anchors. This does
not authorize any paid run. Offline acceptance exercises each of the three real
carriers in its own fixed-carrier run, using scripted human defects and repairs.
These fixtures test integration, not Red generation quality or learning gains.

An explicit `carrier_mode=rotate` remains available for a separate cross-carrier
development protocol. The original three-round rotation visits each carrier only
once and cannot demonstrate same-carrier revisiting. Mode and index are frozen;
seed namespaces change with the policy. Call reservations remain 21 with one slot
per round and three anchors in either mode. Neither mode establishes unseen-family
transfer on these already inspected development carriers.

## One round

1. Freeze and hash both incoming states. Red sees only permitted earlier-round
   observations. Record the Blue version each new challenge targets.
2. Red chooses a supported requirement/mechanism to challenge and emits bounded
   RTL edits. It may revisit an unresolved observation, test a previously repaired
   mechanism on a different case, or explore uncovered behavior. These are search
   choices, not a fixed catalog of edit operators or a predicted Blue answer.
3. Admit each candidate using the frozen public construction rules. Retain invalid
   candidates, abstentions and duplicates in the funnel. Do not reward code size,
   malformed Blue answers, or tool failures as functional difficulty.
4. Repair every admitted challenge against the same incoming Blue snapshot. Any
   eligible retrieved experience is offered from the first repair attempt. Record
   its exact delivered bytes and hashes; a nonempty store with no retrieval is
   not a memory intervention. Do not expose Red's clean RTL, mutation diff or
   private generation rationale to Blue.
5. Record actual outcomes and prepare separate Red observations and Blue learning
   candidates. Complete the round before publishing either next-round state.
6. Commit `R_t` and `B_t` atomically, including parent hashes, source episode IDs,
   update decisions and costs. If no acceptable update exists, retain the previous
   state explicitly. An unchanged Blue state is not evidence of learning.

All slots within a round use the incoming snapshot. No same-round update may
depend on slot order. A stopped or partially executed round cannot silently
publish an apparently complete next state; any future recovery policy must be
declared in the runnable plan.

## Learning from observations without inventing weaknesses

Red's observation channel should retain both public successes and actual public
functional failures, with their evidence strength. Restricting feedback to
successful repairs would hide precisely the failures the continuous loop needs
to investigate. This is an explicit change from the earlier feedback protocol.

- Public success: Blue handled this sample under the public checks; hidden
  correctness remains unknown.
- Single public functional failure: an unconfirmed observation that may motivate
  exploration, not a discovered weakness, confirmed reward, or mastery verdict.
- Repeated, reviewed functional failure: a finding tied to the exact Blue
  snapshot and verification scope used for confirmation.
- Invalid edits, no answer, infrastructure errors and context limits: operational
  outcomes, never functional weakness evidence.

Red may use tentative evidence to choose its next experiment. That does not make
the tentative label stronger. Confirmation, when separately scheduled and funded,
must use fresh encounters with the same frozen Blue snapshot. Repeating a challenge
against a later Blue version tests adaptation, not repeatability of the old result.

Blue learning candidates use only its own task-visible RTL/specification, applied
patches and public feedback. An unresolved failed repair may be retained as an
unresolved episode, but must not be turned into an asserted repair rule. Entries
derived from public-passing repairs retain that limited validation label.

The learning updater and eligibility gate must be fixed before execution. They
must preserve provenance, reject unsupported claims and use only designated public
development checks; public passing is not proof of universal correctness or useful
transfer. A paired usefulness gate, if used, consumes separately counted calls.
No reference patch, hidden counterexample or Red-only explanation may teach Blue.

Hidden evaluation remains sealed until collection ends. Hidden findings cannot
affect state updates, generation priorities, admission, early stopping or retries
in the ongoing run. A later decision to declassify development evidence creates
a new version and is not retroactive learning in the original run.

## What must be measured separately

Before round 1, freeze `B_0` with empty experience, its complete model/runner
settings, and an anchor manifest. Every admitted online challenge is also attempted
by `B_0` with the same repair budget. Keep these reference encounters isolated from
both agents' learning channels; they do not supply training episodes. Use separate
samples even in round 1, and predeclare encounter order and seeds.

Freeze one existing human qualification defect from each of Hazard3, ZipCPU and
Ultra-Embedded as three diagnostic anchors. Record exact task, configuration,
public/evaluator suite and variant hashes before collection. Evaluate `B_0` and
each committed `B_t` on all anchors with matched budgets. These probes may run
after collection using saved snapshots; their outcomes must remain sealed from
all learning and update decisions. Reserve their calls before collection starts.
An aborted run reports absent snapshots/probes rather than replacing them.

These carriers and qualification defects have already been inspected during
development. The anchors are fixed diagnostic tasks, not pristine holdouts.
Neither their patches nor their results may enter this run's initial experience,
Red feedback or updates. Reject generated challenges identical to an anchor under
the frozen task/code identity checks, without revealing the anchor to Red. Report
near duplicates and historical exposure; hash exclusion cannot certify semantic
independence. Transfer claims still require new design families.

Every challenge records exposure against both the entire incoming experience store
and the entries actually delivered: exact mutant match, same-span repair overlap,
or unknown mapping. Exposure metadata stays in measurement, not Blue's prompt.
Split results by exposure and retrieval; an exact retrieved old patch establishes
retention rather than novel adaptation. See the interface for comparison rules.

A harder challenge distribution can reduce Blue's online pass rate even when Blue
improves. Conversely, easier proposals can raise that rate without learning.
The changing online success rate alone establishes neither effect.

| Question | Required comparison |
| --- | --- |
| Is the generated stream harder for a fixed solver? | Each round's admitted challenges against frozen B_0; describe sample noise and admission changes |
| Does Red use repair feedback effectively? | A separate adaptive versus feedback-free comparison with matched generation/Blue budgets and the same frozen target; B_0 tracking alone does not establish causality |
| Does Blue's performance change? | B_0 and each B_t on the frozen anchor set, with matched budgets, source exposure and actual delivery recorded |
| Does ability transfer? | Later evaluation on reserved, unexposed design families |

The first small implementation run establishes that the state transitions and
actual interventions occur. It need not launch all comparisons simultaneously.
Same-task replays measure retention; fresh variants on seen carriers measure
within-carrier adaptation. Neither is cross-design generalization by itself.
Evaluation probes stay outside the learning channel. Do not count a training
episode subsequently replayed from memory as an unseen-task gain.

After collection, audit the source repairs of every saved snapshot using the
frozen evaluator. Report unique source counts as no mismatch within scope,
checker-confirmed mismatch (with review status), inconclusive, or unavailable.
Report how many entries and delivered requests depend on each category. Deduplicate
shared sources; do not retroactively remove entries, rewrite snapshots or change
earlier delivery records. A source mismatch does not by itself prove its derived
advice caused a later failure. This audit is distinct from outcome verification.

## Minimal implementation and budget boundary

Start by verifying three logical rounds offline: the third round must consume
states produced after the second, proving this is not just a one-time feedback
injection. Round and slot counts remain configurable and must be frozen before a
paid run; continuous adaptation does not mean unbounded expenditure.

Reuse the qualified carriers, no-prediction edit schema, public admission checks,
candidate archive, bounded evidence, delivery journal and provider/cost guards.
The earlier fixed-Blue feedback experiment is an optional later ablation. Its
16-call estimate is not a budget for this new loop.

Count generation, repair, reference encounters, anchor probes, any model-based
experience extraction, qualification and confirmation separately. For T rounds,
S scheduled slots per round and A anchors, one direct attempt and zero retries
give at most `3*T*S + (T+1)*A` calls before model-based learning or confirmation:
T*S Red, T*S online Blue, T*S reference Blue, and (T+1)*A anchor Blue calls.
For three rounds, one slot per round and three anchors, this is 21 calls, not six.
The initial deterministic episode extractor in the interface adds zero calls;
changing to model-authored experience requires a revised budget. Invalid slots
reduce actual online/reference calls without replacement. Reserve the full round
and its measurement obligations before opening it. This is arithmetic, not an
execution or monetary approval. Do not spend existing reserves by implication.

Required implementation changes:

- Scheduler: arbitrary fixed rounds, incoming-state snapshots, immutable
  within-round reads, atomic boundary commits and explicit no-update transitions.
- Red consumer: prior-round public observations with uncertainty labels, without
  hypothetical Blue patches or hidden feedback.
- Blue runner: explicit experience input and delivery logging from attempt one;
  a fixed learning/update policy at round boundaries.
- Measurement: snapshot-bound confirmation and separate retention, adaptation,
  repair quality and operational-failure records.

The existing `repair_feedback.py` accepts only feedback-arm round-1 public passes;
it is reusable measurement infrastructure, not already a general round-state
interface. The older `curriculum.py` has learning machinery, but its scheduling,
qualification and cost semantics must be adapted rather than copied wholesale.
The single-file `BlueConfig.memory_from_attempt=2` default would suppress all
learning interventions in a one-attempt pilot and must not carry into this mode.

Offline acceptance must show round 3 consuming round 2's committed state, actual
Blue experience delivery, no same-round/hidden/other-run leakage, correct handling
of no-update rounds, and call accounting for every update stage. It must also show:

- Reference B_0 is unchanged; all B_t probes use the exact frozen anchor manifest,
  and neither measurement channel enters updates or Red requests.
- Exact repeats, overlapping repair spans, insertions and unmappable spans have
  the prescribed exposure tags for stored and delivered experience separately.
- Experience fits its fixed 4,096-token envelope including its prompt wrapper;
  oversized entries are omitted whole and logged, with no route switch.
- Each repair request binds its experience bytes, task and named snapshot before
  sending; confirmation restores that snapshot, not the latest one.
- A simulated post-run source mismatch changes only the audit report, never
  previous snapshots, selection, delivery or outcomes.
- Three rounds, one slot and three anchors produce exactly 21 model requests in
  the all-admitted fake run, with no model-based extraction or confirmation.

Only afterward can a concrete low-cost execution plan be reviewed.

## First fixed-carrier paid-plan scope

The prospective ZipCPU feasibility pilot retains 21 calls: three Red proposals,
three online Blue attempts, three independent B_0 attempts, and twelve anchor
probes (three anchors at each of B_0 through B_3). There are no retries, slot
refills, confirmation batches or additional anchor repetitions. A single entry
is a provisional public-pass repair episode, not an established reusable rule.

Only a0 is on the online carrier. Its single sample at each snapshot cannot
support a claim that Blue improved. The other two anchors describe cross-carrier
responses to retrieved ZipCPU experience; they do not supply more same-carrier
samples. All three are development diagnostics, not pristine holdouts. Report
exposure groups and actual delivery, including same-span overlap, before reading
any apparent trend. A frozen B_0 comparison is descriptive with this sample size;
there is no matched non-adaptive Red arm to identify feedback's causal benefit.

This run can establish that real proposals, public feedback, boundary commits and
subsequent experience delivery operate together, and record the resulting repair
behavior and costs. It cannot establish Red improvement, stable Blue improvement,
confirmed difficulty or transfer from three single-attempt online encounters.
Stopping and subsequent budgets do not depend on obtaining a favorable result.

The launch proposal uses a CNY 12 envelope and 21-call hard cap. At the declared
peak uncached tariff (CNY 2/M input, 8/M output), the full context/output limits,
including floor(1.05 * counted input) + 128, sum to CNY 10.425588. This is a
conditional worst-case calculation, not an expected charge. A failed HTTP request
retains its entire monetary reservation because its billing is unknown. Changes
to tariffs require a new cost review. Existing A/B/HWE reserves remain separate.
The launcher freezes recoverable code, including the confirmation seed fix;
pre-launch check is offline. Collection requires separate approval, a sufficient
balance pre-check, a new directory and one shared provider transport. No paid run
is authorized by writing this plan.
