# Failure representation before larger patch search

Design discussion, 2026-10-06. Proposed changes only; no Blue, retrieval, or
measurement implementation is changed by this document.

## Paper and inference boundary

The user's source is [Evidence-Guided Repository-Level RTL Repair,
arXiv:2610.03219v1](https://arxiv.org/html/2610.03219v1).
Sections III-B through III-D connect a reproduced failure, waveform queries,
and elaborated repair scope. The proposed temporal/structural/elaboration
retrieval representation below is our extension, not a retrieval result
demonstrated by that paper. Its consistency component also matters: better
localization does not establish that every required repair site was updated.

Our claim should remain conditional: in tasks with coupled repair obligations,
finding one faulty site can be insufficient. The paper's consistency ablation
removes scope construction and replay validation together, so its gain does not
independently identify propagation-scope inference as the dominant bottleneck.
Nor do different source/elaborated instance counts prove that more source edits
are required: one shared definition or generator change may repair many instances.

Treat graph reachability as a candidate inspection scope, not a mandatory edit
set. Distinguish locations to edit, locations affected automatically, and
locations/configurations to revalidate. A single configured elaboration cannot
establish correctness for every supported configuration. Scope predictions should
carry obligations and uncertainty, with source-to-instance/generator mappings.
Test scope inference separately from replay, holding the latter and the repair
budget fixed. Verify behavioral repair rather than exact agreement with the
historical patch; an alternative correct implementation can have a different scope.

## Research hypothesis: repair obligations after correct diagnosis

**Research question:** Given a correctly identified failure mechanism, does an
explicit representation of the associated repair obligations reduce omitted
changes and unnecessary changes?

**H1 (unverified):** Under matched repair and validation budgets, representing
these obligations improves repair completeness and reduces unsupported edits
relative to providing the same diagnosis without that representation.

The scope is conditional on correct diagnosis; it is not a claim that scope
inference dominates all repository-level repair failures. Establish and freeze
the diagnosis before comparing repair arms, using an independent review of the
mechanism and its evidence. Both arms receive the same diagnosis. Label this a
controlled diagnostic experiment, separate from an end-to-end experiment in
which Blue must also discover the mechanism. Do not select cases according to
which arm later repairs them successfully.

Represent each obligation with its triggering change, the relation or invariant
that must remain consistent, supporting evidence, applicable configurations,
candidate locations, and uncertainty. Distinguish source edits from automatically
affected instances and locations that need validation only. The representation
must be constructed from the permitted specification, buggy source, build context,
and visible evidence, without the golden patch or hidden test outcomes.

For example, a field-width change raises obligations to check declarations,
packing/unpacking, and port bindings. A shared definition may satisfy several
obligations through one edit; every connected instance is not a separate edit
requirement. Reachability in the relation graph supplies inspection candidates,
not proof that each candidate must change.

Use paired arms on the same frozen cases, Blue model/settings, diagnosis,
specification, memory condition, repair-attempt budget, and validation tools.
The treatment adds explicit obligations; the control retains the existing
representation. Keep replay validation available in both arms so its benefit
is not conflated with scope reasoning. Report context size and extraction cost;
a later equal-information presentation control can separate structured
representation from simply supplying additional information.

Measure final repair success together with two reviewed error categories:

* **Omitted changes:** a remaining violated obligation attributable to an
  incomplete repair, supported by a behavioral or consistency witness.
* **Unnecessary changes:** edits independently judged outside the required
  repair, with their regressions reported separately. Patch size alone is not
  evidence of unnecessary editing. Reverting a hunk while available tests still
  pass supports test-relative redundancy, not universal semantic redundancy.

Also report review uncertainty, paired case outcomes, and resource use. Accept
alternative behaviorally correct repairs; historical patch overlap is not the
success criterion. An inconclusive or negative result leaves H1 unsupported
under the tested conditions and must not be rewritten as a demonstrated gain.

## Current implementation findings

* `profile.build_profile` already records temporal symptoms, onset buckets,
  driver kind, register distance, and coarse dependency-cone properties.
* `KnowledgeMatcher` defaults to status/causal/type weights of 0.45/0.35/0.20.
  Retrieval is therefore already more than bug-type matching. These static
  structural features are not proven dynamic causal relations.
* `CaseMemoryAuthor.group_key` groups by verified bug type, symptom, driver kind,
  and register distance. `_item` retains consensus applicability fields. New
  discriminating context can be lost if grouping and storage remain unchanged.
* `structure.py` explicitly implements heuristic token-level analysis, not HDL
  elaboration. `register_trace.parameter_values` decodes literal state labels;
  it does not resolve a configured hierarchy, types, or generated instances.
* `evidence_window` labels alternating-clock samples with cycles/edges, whereas
  `compare_traces` assigns sample indices to `first_cycle` and sample counts to
  `total_cycles`. The profile consumes the latter. A read-only synthetic witness
  with eight samples and its first mismatch at sample four reports first_cycle=4
  and total_cycles=8 in feedback, but first cycle=2 and total cycles=4 in the
  window. Normalize the timebase before extending temporal retrieval features.
* `_attempt` sends original buggy RTL plus attempt summaries. Every feedback
  record should explicitly identify the source version it measures. A failed
  candidate's trace cannot silently describe the original RTL. This does not
  require changing the existing original-source edit contract.

## Proposed representation

Maintain a detailed, case-local failure state and derive a portable retrieval
query from it. Do not concatenate all context into one exact-match key or a
single embedding. Keep the following channels separately inspectable:

| Channel | Example information | Role |
| --- | --- | --- |
| Temporal signature | Clock domain, edge/phase, event-relative onset, recurrence, persistence, recovery, valid-qualified observations | Compare behavior over time |
| Structural context | Output role, register boundaries, data/control dependencies, branch guards, relevant definitions/uses | Compare dependency roles |
| Elaboration context | Actual parameter bindings, effective widths/signedness, selected generate branches, instantiated port bindings, build defines | Establish the circuit/configuration that was executed |
| Contract context | Requirement, legal trigger/preconditions, reset/handshake semantics, unresolved assumptions | Decide when a lesson is applicable |

The fourth channel need not be a fourth similarity vector. Use it to interpret
the other three and represent applicability conditions. Similar traces do not
by themselves imply the same permitted repair.

Every derived field records its source artifact, candidate hash, extraction
method, availability, and uncertainty. Separate observed facts, static possible
dependencies, dynamic exercised paths, and hypothesized mechanisms. A static
fan-in cone alone cannot certify which driver was active at a sampled edge.
Unknown values must not score as positive agreement merely because both are
unknown. Resolve clock/phase from the harness where available; unknown timing
must remain unknown rather than using a toggling data signal as a clock.

Elaborate the buggy design under the actual build configuration and preserve
source mappings; do not substitute a clean design or an optimized netlist with
untracked transformations. Record elaborator limitations and partial results.
Golden source, Red mutation locations, and hidden outcomes cannot enter Blue's
query. Existing permitted expected output traces remain usable.

Exact paths, instance names, raw timestamps, and build hashes belong in local
evidence/provenance. Portable features use roles and semantic relations: for
example, whether counter width covers the configured range, not equality of
all parameter values between unrelated designs. Retain exact values for checking
an item's explicit requirements when they matter.

## Retrieval and memory construction

1. Check explicit applicability requirements. Reject demonstrated contradictions,
   not arbitrary differences in width, hierarchy, or parameter values. Missing
   required evidence yields uncertain applicability or abstention, not a match.
2. Rank remaining cases by temporal and structural compatibility, using relevant
   elaborated relations. Keep bug-type probability as an optional prior.
3. Return matching observations, contradictions, unknowns, and the supporting
   evidence with each retrieved case. A prior repair is a hypothesis source.
4. Preserve distinct applicability branches when authoring memory. Do not merge
   incompatible cases and then discard their distinguishing fields by consensus.

First implement a deterministic representation/extractor using existing traces
and build artifacts. Do not add a model call merely to write a confident-sounding
diagnosis. Start with timebase/source-version consistency, then temporal and
structural relationships. Full hierarchy elaboration can follow for HWE; a small
single-module pool may provide limited variation for testing that dimension.

## Evaluation order and Red interaction

Freeze candidate bugs, Blue configuration, patch-attempt budget, and memory pool.
First compare old/new evidence representation with retrieval disabled. Then hold
the new evidence constant and compare old/new retrieval and matched/shuffled
memory. This separates gains from observation quality, selection quality, and
knowledge content. When claims concern elaboration specifically, add an ablation
removing that channel. Log extraction failures and resource costs alongside
repair outcomes; tune thresholds on discovery/qualification only.

Do not change Blue representation and Red search simultaneously in a comparison.
Red may eventually receive an allowlisted summary of Blue's public diagnostic
trajectory and learn which inferred mechanism was contradicted by which evidence.
This exposes useful targeting feedback without giving Blue Red's hidden mutation.

The proposed 94-call Red diagnostic remains unexecuted. If Blue representation
changes first, freeze it and revalidate any historical seed against that version
before using the earlier warm-start plan. No new spending allocation is implied.
