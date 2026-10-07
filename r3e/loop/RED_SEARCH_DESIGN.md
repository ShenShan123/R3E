# Red search: evidence, counter-repair hypotheses, and transfer

Design decision, 2026-10-06. The bounded observation/search/seed diagnostic is now
implemented behind opt-in flags; see [implementation and review commands](RED_SEARCH.md).
The first paid diagnostic is complete; see the dated review below. It produced
no newly confirmed hits, and the effectiveness hypotheses remain unestablished.
Blue, admission, confirmation, scoring, and review remain externally owned.

Subsequent design priority: first establish a consistent failure representation
and assess its value separately from larger patch search. See
[Failure representation](FAILURE_REPRESENTATION_DESIGN.md). The Red protocol and
94-call allocation below describe that diagnostic; a changed Blue version
requires a fresh freeze and seed revalidation before that experiment.

## 2026-10-06 pilot review and next research direction

The pilot used 23 of its 94 allowed calls: seven seed calls, five cold calls, and
eleven warm calls. The seed reproduced budgeted failure in two of three runs.
Three warm follow-ups passed the original visible test on their first attempt.
The planned slots completed; unused calls do not create additional slots.
There are no new confirmed hits, and the remaining 71 calls stay unused.

Source and supplemental simulation review reveals a qualification to those
passes. For `RC_385e8736e87361` on continuous sequence detector 2, Blue repaired
the output-timing decoy but retained the missing terminal transition. On legal
consecutive input groups `01100110` with valid continuously asserted, the clean
design matches after bits 4 and 8; the actual Blue candidate matches only after
bit 4. The raw sources, testbench, hashes, traces, and review are stored under
`R3E-local-artifacts/red_search_pilot_v1/generator_review/` in the local artifact
root. This is one post-hoc residual-error witness, not a new scored hit or a
2-of-3 transfer result. Do not retroactively change the frozen trial's scores.

The initial transition-only candidates had already been rejected as invisible
to the visible test. Adding an observable output error admitted the combined
mutant. Whole-mutant observability therefore did not establish target-mechanism
observability. Red's predicted partial repair occurred once, but the original
test could not reject it. This is evidence to improve target validation before
concluding that all follow-ups were semantically repaired.

### Generator follow-up implementation

The v2 pilot subsequently used 13 of the remaining 71 calls. Seed validation
produced one budgeted failure in three encounters, so warm generation did not
start. Cold generation admitted one visibly repaired candidate and rejected one
at the hypothetical-repair observability gate. The original stop decision stands.

Seed candidate capture is now also enabled in the pilot integration. All six
materialized candidates from that completed seed validation were recovered by
matching original simulation sources to recorded attempt hashes. The two visible
passes showed no mismatch under random seeds 11, 23, 37 (1,000 cycles each) or
20-step bounded equivalence using the existing verifier's documented abstraction.
These scoped checks do not prove complete correctness or alter hit counts.
Recovery provenance and results are under
`R3E-local-artifacts/red_search_pilot_v2/seed_candidate_recovery/`.

The two batches together contain three budgeted failures in six encounters.
That is an empirical fraction, not an established 50% failure probability or
proof that provider behavior was stationary. Retain the existing two-failures-in-
three gate for the completed protocol, and do not repeat seed batches until one
passes. A future historical-hypothesis exploration branch would need its own
prospective definition and must not label its seed a currently revalidated
weakness. The 58 remaining calls are unallocated and unused.

The executable hypothetical-repair gate is now implemented for search v2. Source
strata broaden discovery menus, and public candidate artifacts allow the next Red
proposal to see actual Blue diffs. These changes passed local validation only;
no remaining paid calls have been spent. See [the protocol](RED_SEARCH.md) for
bounds, isolation, and limitations. The steps below distinguish implemented
mechanisms from outstanding semantic review and repository-scale expansion.

1. Implemented: materialize `predicted_wrong_repair` as executable edits relative to the
   final mutant. Locally test both the mutant and this partial repair. A passing
   partial repair means the existing tests cannot support the proposed trap;
   it is neither a confirmed difficult bug nor evidence that Blue chose it.
   Preserve a separate result for malformed or uncompilable partial repairs.
2. Record a legal trigger and the surviving violated obligation. Red may propose
   a stimulus but cannot author the scoring oracle. The clean design must pass
   and its behavior must agree with the specification. Any expanded suite needs
   measurement review and prospective freezing; no hidden-feedback search.
3. Implemented as prompt guidance and source-stratified menus: diversify mechanisms and causal contexts: boundary input consumption,
   data/control alignment under backpressure, flush/reset cleanup, and
   definition/use consistency across parameters and instances. These describe
   search opportunities, not a fixed mutation catalog or guaranteed difficulty.
   Keep the existing 40% ceiling; do not impose a minimum diff or reward damage.
4. Implemented actual-candidate archival and bounded diff feedback; paid evaluation
   remains outstanding. Use completed public Blue repairs to retain and revise hypotheses. Repaired
   cases are negative evidence; a retained residual is an observation. Before
   evolving a repaired candidate, state why the previous valid repair would be
   insufficient for the new defect, while keeping all intent in the unchanged
   specification. Limit revisions and log all search costs and discarded cases.
5. Add medium-sized multi-module carriers with reliable tests before attempting
   full repository-scale generation. Check source/build-manifest, elaboration,
   multi-file editing, and oracle support first. Select carriers by mechanism
   opportunities before looking at Blue outcomes. Expansion is a new stratum,
   not a comparable continuation of the old ChipBench hit-rate denominator.

The objective is a recoverable, testable repair challenge with meaningful
dependencies. Multiple independent faults and larger code changes are not
substitutes for dependencies. Search discovery remains separate from independent
confirmation and later frozen evaluation of Blue's learned memory.

### Relevant public evidence and its limits

* [BugLab, NeurIPS 2021](https://proceedings.neurips.cc/paper/2021/hash/ea96efc03b9a050d895110db8c4af057-Abstract.html)
  jointly trains a bug selector and detector/repairer. The selector targets the
  detector's loss over available rewrites. This motivates performance-conditioned
  selection; its Python rewrite setting does not establish free-form RTL transfer.
* [BugGen](https://arxiv.org/html/2506.10501v2) generates and validates functional
  mutations with feedback and retries on OpenTitan IP. Its functional validity
  and downstream triage results are not measurements of an LLM repairer's failure
  rate. Successful construction and adversarial repair difficulty remain distinct.
* [VeriBugBench](https://arxiv.org/html/2609.18022v1) combines repair-history-derived
  mutations, project-level stimulus enhancement, and execution filtering. It
  supports test-observable benchmark construction, not adaptive Red superiority.
* [RTL-Repair](https://slice.eecs.berkeley.edu/papers/rtl-repair-fast-symbolic-repair-of-hardware-design-code/)
  explicitly reports testbench-overfitting repairs. This reinforces the need to
  distinguish a test pass from adequate functional validation.
* [HWE-Bench](https://arxiv.org/html/2604.14709v1) analyzes localization, hardware
  reasoning, and coordination failures. Its project comparisons motivate
  studying protocol and cross-artifact dependencies rather than size alone;
  they are observational evidence, not a controlled proof of one difficulty axis.
* [Evidence-Guided Repository-Level RTL Repair](https://arxiv.org/html/2610.03219v1)
  motivates explicit repair scope and replay. Its consistency ablation removes
  both scope construction and replay, so it does not isolate the causal value of
  repair obligations or validate a particular retrieval key.

Our residual-observability gate and proposed search axes are design hypotheses
informed by these works. Their effect on current Blue must still be measured.

## 2026-10-07 contribution and evaluation priorities

The primary research claim concerns adversarial feedback: whether Red uses Blue's
observed repair failures to construct valid, recoverable follow-up challenges,
and whether those challenges improve downstream repair under a frozen evaluation.
Memory is one possible mechanism for retaining that experience, not the primary
contribution. Two reviewed exploratory hits exist; adaptive cross-design transfer
and downstream system gain remain unestablished. Neither larger RTL nor a useful
memory ablation establishes those claims on its own.

The completed ChipBench Part B feasibility run used 124 calls for all five
scheduled repeats (105 units). Two units were inconclusive after provider
failures; both arms passed all 103 evaluable units within three attempts. Of five
forks, only three actually received memory. The only difference in repair attempt
count occurred in a fork without memory delivery and cannot be attributed to
memory. Supplementary verification has 108 candidate-check records: 98 shared
first passes and five per arm, covering 103 outcomes per arm. These are not 206
independent repairs, and scoped checks finding no mismatch are not full proofs.
The immediate limitation is insufficient intervention opportunity; this result
cannot distinguish a weak memory effect, retrieval mismatch, or source shift.
No additional repetitions on these eight problems are planned. Part A's 58 and
Part B's 76 unused calls remain separate and are not assigned to HWE.

A later, separate memory study may evaluate efficiency at matched validation
quality rather than only final repair rate:

* **Zero-model-call repair:** a frozen executable rule or patch template, applied
  only when its explicit structural and elaboration preconditions hold, produces
  a candidate that passes the predeclared verification. Retrieving prose alone
  is not a repair. Report verified zero-call successes over all eligible tasks,
  coverage, abstentions, rejected candidates, and fallback outcomes separately.
  Zero model calls does not mean zero computation or proven correctness.
* **Assisted repair cost:** compare model calls, input/output tokens, and wall time
  for paired tasks under identical budgets and verification. Count retrieval,
  rule application, failed candidates, checking, and fallback costs; report
  unsuccessful tasks too, so fewer completed repairs cannot look like savings.
  Input and output tokens remain separate unless model-specific prices are frozen.
* **Amortization:** report memory/rule construction costs separately and their
  amortized contribution at declared reuse counts. Freeze rules on discovery
  data; do not author or tune them using evaluation fixes or counterexamples.

This is a prospective protocol, not an implemented zero-call repair engine or an
authorization for model calls. Repository-scale HWE development and the separate
frozen-diagnosis repair-obligation experiment retain their own evaluation scopes.

## Pre-pilot diagnosis from the implementation

The grounding protocol fixes unblock selection. They establish neither functional
admission nor repair difficulty. Replaying twenty previously rejected selections
must not be reported as producing twenty valid bugs.

The larger limitations are in the feedback and search architecture:

* `freeform_curriculum.public_weakness_view` exposes detailed repair attempts only
  for offered, already-open failure lineages. Other admitted cases contribute
  mechanism coverage counts. A successfully repaired exploration therefore gives
  Red almost no explanation of how its challenge was defeated.
* `curriculum.round` opens a lineage only after reproducible budgeted failure.
  Keeping that reward threshold is correct, but using it as the threshold for
  receiving useful observations creates an unnecessary cold-start bottleneck.
* `_propose_slots` produces the whole round before Blue runs. Five slots share a
  pre-round history; they cannot respond to each other's repair results.
* The first Red call already writes exact edits. `assess_mutation_plan` checks
  applicability and edit limits, not compilation or simulation. The second call
  materializes/revises one proposal. There is no functional candidate comparison
  or search against a specific hypothesized incorrect repair.
* The 40% ceiling limits syntactic destruction. Neither a larger diff nor a low
  mismatch fraction demonstrates that Blue will find a repair difficult.
* An unused design cluster is transfer-eligible, but that does not mean it has
  the target mechanism. The current menu lets Red assess this; it does not prove it.

The latest aware-v2 ledger has seven accepted cases: six first-attempt visible
passes, and one `no_answer` followed by a visible pass. That seventh case is not
an observed wrong functional repair. There is no functional near-miss in these
seven cases to promote into an established weakness.

## Decision: observe broadly, reward narrowly

Keep separate records for an observation, a repair-failure hypothesis, and a
confirmed weakness. A hypothesis is not an offered confirmed-weakness ID.

| Observation | Search interpretation | Evidence of a confirmed weakness? |
| --- | --- | --- |
| First-attempt successful repair | This challenge was easy for the current Blue configuration; record how it was repaired. | No |
| Executable incorrect repair, followed by success | A possible local reasoning trap, with an observed recovery. | No |
| Reproducible budgeted failure, pending specification review | Candidate weakness; preserve the actual attempts and failure categories. | Not yet |
| Reproducible failure with recoverable intent, review passed | Confirmed challenge-level weakness; mechanism remains a hypothesis until transfer is tested. | Yes, under the recorded budget/configuration |
| Empty answer or output exhaustion | Delivery/budget observation, separately labeled. | Not evidence of a functional reasoning mechanism by itself |
| Provider/configuration failure | Operational failure. | No |

Expose a bounded, deterministic view of completed discovery encounters, including
successful repairs and unsuccessful candidate generation. Never read current or
future rounds, qualification outcomes, holdout outcomes, or private Blue memory.
Retain only public attempt feedback and allowlisted metadata. In particular, do
not copy entire encounter objects: they can contain hidden-test fields.

Each observation links the challenge, design cluster, clean/spec/mutant hashes,
the actual Red diff, visible outcomes, and Blue configuration/snapshot identity.
The existing Blue `edit` field is model-written explanation, not a verified patch;
label it accordingly. Exact repair code/diffs require a separate allowlisted
artifact from the measurement side. Missing artifacts must remain missing.

Initially bound the view to twelve challenge summaries, selected deterministically:
the most recent six plus up to six older cases relevant to offered targets, with
deduplication and explicit truncation metadata. Keep full records in the ledger.
Preserve passed repairs as negative evidence, not just interesting failures.

Change feedback cadence to one proposal per round. This uses existing scheduling
support and makes every subsequent proposal see the last completed observation.
Use a separate hypothesis archive for unconfirmed targets; do not fabricate
lineage events to bypass the existing confirmation threshold.

## Decision: generate against a repair hypothesis

A Red proposal must distinguish the injected RTL defect from the predicted Blue
mistake. For example:

* Defect: a terminal FSM transition stops consuming the first bit of the next group.
* Predicted wrong repair: move the output decode one cycle earlier while leaving
  the group-boundary transition wrong.
* Why it is wrong: output alignment and next-group input consumption are separate
  obligations under the specification.
* Discriminating observation: a legal consecutive-group sequence exposes the
  dropped first bit even after the proposed output-only repair.

This example is a hypothesis motivated by the reviewed sequence-detector case,
not a verified general explanation of Blue's behavior.

The candidate record should contain `broken_invariant`, `predicted_wrong_repair`,
`residual_failure`, `activation`, `spec_basis`, and source observation IDs. These
are explanations, never unsupported success labels. The model may propose any
legal structural edits within the existing budget; this does not introduce an
operator catalog or require an arbitrary minimum edit size.

Use coherent coupled dependencies to search for difficulty: consuming an input
and updating state, valid/data alignment, carry dependencies, or coordinated
definitions and uses. Only choose mechanisms actually present in the design.
Adding independent defects or deleting more code is not an objective. Retain the
40% code/body ceilings, immutable interface, and original specification.

## Decision: spend the two Red calls on local search

Retain at most two paid Red calls per slot, but give them different work:

1. From the frozen design menu and observation view, choose a design and target;
   return up to three materially different complete edit plans for that target.
   These are candidates, not three Blue challenges.
2. Materialize and evaluate those plans locally using the existing judge's
   compile, visible-failure, logic-loop, duplicate, and budget rules. Supply
   allowlisted diagnostic feedback to the second Red call. It selects a candidate
   or produces one revision, explaining the predicted incorrect repair and the
   remaining failure. Validate a revision through the same gates.

Bound local evaluation to three initial mutants and one revision per slot. A
malformed plan cannot trigger an unbounded repair loop. If no admissible candidate
remains, reject the slot and record the reason. Model/provider failures retain
the existing stop behavior. Record every candidate and selection decision.

The second call must not secretly invoke Blue. It may select only a locally
admissible candidate or propose the one bounded revision. The reviewed candidate
then enters the existing Blue encounter and confirmation workflow exactly once.
Adapters to the judge must preserve its rules and avoid mutating duplicate state
for candidates that were evaluated but not committed. Cache by source/test/tool
hashes where safe and record simulator cost separately from API calls.

Difficulty ordering is deliberately not a numeric proxy for a hit: first satisfy
automatic admission and document specification grounding, then prefer the candidate with the clearest
evidence-backed repair-failure hypothesis and novelty within that target. Treat
stealth as a secondary descriptor. Freeze the selection rule and log the model's
rationale; this rationale is not an independent validation.
Specification grounding is not automatic proof of recoverability. Keep the
existing human review before counting a reproducible failure as a valid hit.

A later optional surrogate can simulate an explicitly materialized hypothesized
wrong repair. Its failure would only establish that this particular patch fails,
not that Blue will choose it. Do not add that extra evaluator in the first pilot.

## Decision: warm-start explicitly, retain a cold-start branch

Use the reviewed sequence-detector case `RC_406d811b4d61a2` as the initial seed
candidate. Its six-bit grouping requirement is a clearer basis than the vending
case's source hold/timing behavior. Exclude accumulation, traffic, serial-to-parallel,
and rejected vending mutations from seed evidence. Keep the valid arithmetic
vending hit as a possible later hypothesis, not the initial seed.

Revalidate the seed against the current, fully frozen Blue configuration. Run
three independent encounters, each with the unchanged three-attempt budget and
no escalation. Retain all outcomes, including an initially successful run.
Historical 2/3 failure is not a substitute for this current check. If failures
are exclusively empty answers, do not label the result a transition-reasoning
weakness; preserve the budget-failure category separately.

A seed package must carry source ledgers/hashes, the PASS review, source design
cluster, actual mutation, public attempts, model/prompt/budget provenance, and
the new verification results. Store seed provenance separately from new-run
discoveries. Seed import requires an explicit integration path and measurement
review: injecting an arbitrary target into the current view fails offered-ID
validation, and copying a historical state directory corrupts provenance.

Freeze seed/discovery/qualification/holdout clusters before generation. An old
bug-type variant of the same RTL is not a new design. If the seed belongs to a
frozen holdout cluster, do not silently move it or read that holdout's feedback;
define and document a new prospective split for this pilot. Follow-ups must use
different discovery clusters with actual mechanism support. Abstain rather than
force a mechanism onto an unrelated menu candidate.

Keep the current history-independent design-menu policy for the first pilot,
with the same menu construction for any future matched blind control. Log
applicability opportunities and skips. A lack of compatible designs is a search
space limitation, not a Blue repair success or failed weakness transfer.

The cold branch has no imported seed/history. It may learn from its own completed
explorations, including repairs that succeed. The warm branch receives the seed
and its own subsequent observations only. These are diagnostic branches, not a
statistically powered aware/blind comparison.

## The next 94 calls: a bounded diagnostic, not a gain claim

Candidate search, public observations, and seed import are implemented; measurement
review remains the next gate before a paid run. Use fresh run directories and
freeze the full settings, not just a
checkpoint hash. Keep Blue's model, reasoning, answer format, specification,
evidence/register visibility, token cap, and three-attempt budget fixed. Disable
learning, escalation, qualification, cross-model probes, and holdout evaluation
for this search diagnostic. Retain Red low thinking; increasing it is not the
first intervention. Report token/runtime costs as well as call counts.

| Allocation | Worst-case normal calls | Purpose |
| --- | ---: | --- |
| One historical seed, three independent Blue encounters, up to three attempts each | 9 | Check that the warm-start target still exists |
| Two cold discovery slots, each at most two Red + nine Blue calls | 22 | Exercise observation feedback without imported failures |
| Five warm pursuit slots, each at most two Red + nine Blue calls | 55 | Test cross-design recurrence and response to completed repairs |
| Reserved within the same shared cap | 8 | Operational accounting margin; not extra hidden search |
| Total cap | 94 | Includes failed/billed calls and all phases |

Here nine Blue calls per new challenge means one primary encounter and, after a
primary failure, two confirmation encounters, each up to three attempts. It is
a normal-operation bound, not a claim that infrastructure retries are free. The
shared 94-call ceiling takes precedence over completing every planned slot.
Do not start an encounter unless its configured remaining requirement fits the
remaining allocation; partial screens are inconclusive, not hits.

Run the seed check first. If it does not reproduce, keep its record as a historical
hypothesis and stop the warm branch; do not relabel it a current weakness. The
two cold slots may still run. Unspent calls remain unspent until the failed
premise is investigated. If the seed reproduces, run the five warm slots
sequentially; keep the two cold slots in an isolated state directory. Savings
from early successes/rejections are not silently converted into more proposals.

Predeclare three diagnostic decisions:

1. If the first three warm slots produce no admitted challenge on a compatible
   new cluster, stop the remaining warm slots and diagnose construction or menu
   coverage. This is a generator failure, not a Blue weakness result.
2. One reviewed, reproducible failure on a new cluster supports one transfer
   example. Two such clusters are a stronger continuation signal, but neither
   alone proves an adaptive advantage over blind search.
3. If compatible admitted follow-ups are all repaired, record a non-transferring
   hypothesis under this search budget. Do not lower Blue's budget or weaken its
   specification/evidence to create a hit.

The two cold slots test plumbing and feedback use. They cannot settle autonomous
discovery rate; that needs a later adequately sized, predeclared experiment.

## What would demonstrate system gain

Keep the following claims separate:

* **Construction:** more slots yield compiled, visible, recoverable, novel bugs.
* **Discovery:** new reviewed 2/3 failures appear without imported failing cases.
* **Targeted transfer:** a stated repair-failure hypothesis recurs on a different
  design with supporting attempt evidence.
* **Red adaptation:** matched aware/blind search with the same menus, budget,
  generator, and seed opportunities differs because of permitted feedback.
* **Blue improvement:** frozen unseen bugs are repaired better with learned memory
  than without it, with otherwise matched Blue settings and isolated holdout.

Warm-start can help test transfer; it cannot establish autonomous discovery.
Harder bugs can improve a training curriculum; they do not themselves demonstrate
Blue improvement. Evaluate learning on the same frozen bugs, not on an easier
post-training set. Report source/family effects and budget-exhaustion failures
separately. No search design guarantees a weakness exists in a small design pool.

The useful training target is a repeatable, explainable, transferable repair
mistake. After a reviewed failure, construct a simpler teaching sibling if the
mechanism is unclear, then a same-mechanism challenge on another design; only
after that attempt a harder variant. A lesson should link a legal trigger, the
broken dependency, the observed incorrect repair, the specification-supported
correction, and its applicability limits. The known clean RTL supplies a repair
witness; it does not prove the model's explanation of Blue's reasoning.

When Blue's memory or settings change, label earlier failures with their original
snapshot and test whether they still apply. A historical failure is not a claim
about the current Blue. The present default `memory_from_attempt=2` also means a
memory benefit should be evaluated over the allowed repair trajectory; memory
cannot explain a changed first attempt if none was supplied on that attempt.

## Implementation boundary and required checks

Red-owned work: the observation adapter, hypothesis archive, two-call candidate
search, bounded judge adapter, generation ledger/replay, and explicit seed-target
integration. Preserve `RedDesignGenerator.generate` as a single-candidate API
where possible; put portfolio orchestration in the curriculum adapter. Version
and freeze changed prompts, view schemas, seed inputs, search limits, and receipts.

Measurement-owned review: public-feedback allowlist, seed validation/import
semantics, Blue isolation, scoring, confirmation accounting, and hit adjudication.
No change is proposed to what constitutes a valid repair or a confirmed failure.

Before paid calls, test cold history with only successful repairs; wrong repair
then recovery; empty-answer separation; current/future/holdout exclusion; blind
isolation; seed provenance and cluster exclusion; final-candidate admission after
revision; bounded simulations/calls; duplicate-state isolation; and exact resume.
Run the existing tests, release audit, and fake end-to-end path. Those checks
qualify the implementation for a pilot; they do not demonstrate Red effectiveness.
