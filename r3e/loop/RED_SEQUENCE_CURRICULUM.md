# Continuous development on one historical repair mechanism

This protocol selects ChipBench Prob003 and the reviewed historical challenge
RC_406d811b4d61a2 as a human-selected development seed. Its older failures do not
confirm that the current Blue still fails. No model spending is authorized here.

The mechanism is consuming every input bit exactly once across non-overlapping
six-bit groups, with output timing and asynchronous reset preserved. Red receives
the clean design, public contract, historical mutation/provenance, authenticated
incoming Blue state and actual prior online repair observations. It emits edits
only, never a predicted Blue repair. Each challenge starts from clean RTL.
Normalized copies of the historical seed and reserved evaluation mutants are
rejected before Blue and retain their scheduled denominator; no replacement is generated.

Preparation freezes public W_bug, W_preserve and a separate evaluator before
creating qualification or reserved mutations. The original ChipBench prose is
retained with an explicit prospective timing contract; this is a new development
task definition, not a retroactive change to old scores. Suites use specification
checks instead of matching internal RTL state. The evaluator has a separately
implemented monitor but the same author as the public suites.

Three fixed-carrier rounds each have one Red proposal, one one-attempt online
Blue encounter using B_(t-1), and one independent one-attempt reference encounter
using frozen experience-free B_0. Both Blue roles use identical model, evidence,
repair/output budgets and task bytes; their sampling seeds differ. Only public
online repair episodes can update state. Functional failures inform Red as
unconfirmed observations and never become asserted Blue advice. Public passes
become provisional episodes, not certified general repair rules.

Two reserved variants, fixed before collection, reverse group-boundary input
classification separately in the matched and mismatched terminal branches.
Every B_0..B_3 snapshot attempts each variant twice with distinct seeds after the
final online commit. Reserved code, patches and all probe outcomes stay out of
learning. The reserved mutations may share repair spans with training mutations;
report that exposure explicitly. These are new variants on a seen design, not
cross-design transfer or pristine holdout families.

Maximum schedule: 3 Red + 3 online + 3 reference + (2 variants x 2 samples x 4
snapshots) = 25 requests. No retries, replacement slots or unplanned confirmation.
Red has a 16,384 output ceiling; Blue retains 32,768. This is an offline schedule,
not a paid approval. Existing A/B/HWE reserves are not allocated. Any paid launch
requires a fresh recoverable freeze, current client identity, measured request
limits, provider safeguards and a separately approved call/money ceiling.

Answer the questions separately: admitted distinct challenges with generation
losses; online versus B_0 on each challenge; B_0 versus each B_t on deferred new
variants. Keep visible pass, scoped evaluator pass, functional failure, invalid
edit, no answer, compilation/context/infrastructure and inconclusive outcomes
separate. Report all scheduled slots and samples, actual delivered experience,
exact/normalized repeats, overlap/unknown exposure and post-run source validity.
Two variants with two samples are diagnostic; an apparent gain cannot establish
mastery, feedback causality or transfer. A no-gain result is retained as well.

`experiments.loop_probes.sequence_curriculum` provides prepare, scripted offline
acceptance and deferred endpoint reporting. Scripted fixtures restore clean RTL
and demonstrate integration only. Reports and audits never alter committed state.
The shared continuous launcher has an explicit sequence profile with a 25-call
cap and CNY 12 envelope. Red's reduced output cap makes the full-context cost
bound fit within that envelope at the declared frozen tariff. This conditional
bound is not expected spending or a current provider-price guarantee; check the
official tariff before launch. The ZipCPU profile still refuses sequence scope.
No automatic paid launch or budget migration is introduced.
