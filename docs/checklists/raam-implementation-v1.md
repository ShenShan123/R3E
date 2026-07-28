# RAAM Protocol Skeleton V1 / Authority Closure V1 checklist

Status date: 2026-07-28

## Completed foundation

- [x] Preserve System Upgrade Complete V1 hashes and legacy behavior.
- [x] Add optional hash-bound `PolicyState.memory_binding`.
- [x] Implement strict `VerifiedEpisode` and `ControlMemory` schemas.
- [x] Implement content-addressed episode and memory objects.
- [x] Implement crash-safe hash-chain indexes and lifecycle events.
- [x] Reject prompt, patch, oracle, testbench, registry, credential, model
  route, red-truth, and AST-rewrite payloads.
- [x] Freeze and validate the V1 memory control whitelist.
- [x] Build runtime-only `FailureDescriptor`.
- [x] Restrict retrieval to an exact Active Memory Bank with top-k ≤ 3.
- [x] Require current-policy reactivation and deterministic abstention.
- [x] Reject stale/harmful memories and conflicting unqualified bundles.
- [x] Compile zero-token memory-aware execution plans.
- [x] Implement paired shadow replay and five qualification gates.
- [x] Bind Active Memory Bank changes into child policy hashes.
- [x] Add bank store, memory event stream, compatibility classifier, relation
  graph, and cross-store audit.
- [x] Add deterministic schema, storage, permission, replay, promotion,
  reactivation, compatibility, conflict, and audit tests.

## Completed integration

- [x] Emit a `VerifiedEpisode` automatically from every arena challenge.
- [x] Add deterministic candidate builder and consolidator stages.
- [x] Freeze shadow target/non-target manifests in the memory round state.
- [x] Reconstruct qualification decisions and per-memory bundles on resume.
- [x] Register the bank policy child only after qualification evidence passes.
- [x] Persist a non-authoritative bank candidate before the atomic registry
  commit; recover safely across either interruption point.
- [x] Apply `ExecutionPlan` to formal blue adapter analyzer/search/verifier
  calls while proving prompt input unchanged.
- [x] Orchestrate explicit inheritance and incremental revalidation after
  policy transitions; configuration-changing search children drop stale banks.
- [x] Add cross-round bank merge/split/compression and bounded-workset metrics.
- [x] Extend sanitized red capability packets with public memory summaries.
- [x] Implement memory bypass, deepening, and conflict red operators.
- [x] Add failure injection for episode, lifecycle, bank, shadow, and registry
  promotion paths.
- [x] Freeze “RAAM Protocol Skeleton V1” after full tests, deterministic
  whole-policy rounds, deterministic memory promotion, release audit, and
  dataset verification pass.

## Authority Closure completed

- [x] Registry reconstructs the exact policy decision from a frozen promotion
  bundle while holding the writer lock.
- [x] Memory bank promotion reconstructs memory objects, frozen evidence sets,
  shadow results, qualification decisions, and compatibility proofs.
- [x] Bind one immutable poison payload hash from red generation through
  validity, challenge, and VerifiedEpisode creation.
- [x] Make validity adapters evidence-only; reject returned poison fields.
- [x] Append duplicate semantic-memory support as idempotent evidence links.
- [x] Bind every qualification decision to a frozen evidence-set hash/support
  count.
- [x] Add `bank_candidate`; grant `active_dormant` only after policy promotion
  and revoke the candidate right after rejection.
- [x] Replace manual inheritance input with active-bank-derived inheritance and
  reconstruct static compatibility proof.
- [x] Require runner-owned ExecutionTrace conformance; a plan-hash echo alone
  fails closed.
- [x] Separate policy instance and behavioral hashes and reject no-op children.
- [x] Reconstruct evidence-set/link hashes, support, uniqueness, canonical order,
  and the exact frozen source `VerifiedEpisode` objects.
- [x] Propagate distinct policy instance/effective identities through episode,
  memory, bank, runtime, replay, and qualification records.
- [x] Separate memory-bank instance/effective identities and reject bank-ID or
  bank-version churn that does not change executable behavior.
- [x] Keep memory definition identity independent of policy lineage; accumulate
  cross-policy evidence and reject semantic duplicates in an active bank.
- [x] Bind bank-child provenance to current cumulative evidence-set and
  qualification-decision hashes.
- [x] Emit an `authority_graph_summary` in RAAM audit output without claiming a
  fully verified authority DAG.
- [x] Use durable atomic JSONL replacement and fsync append-only ledgers/events.
- [x] Freeze `RAAM Authority Closure V1` as a child of Protocol Skeleton V1.

## Grounded Runtime still open

- [ ] Recompute compile/formal/oracle truth from tool artifacts and provider
  receipts owned or independently parsed by the runner.
- [ ] Derive FailureDescriptor only from observable waveform/oracle/RTL
  analyzer artifacts.
- [ ] Require parser-backed semantic RTL diff for every memory-aware red
  operator.
- [ ] Reconstruct qualification target/non-target membership and activation
  precision/false-activation gates.
- [ ] Verify a complete authority DAG including replay rows, compatibility,
  promotion/registry transitions, runtime plans/traces, and oracle evidence.
- [ ] Refactor large runners into independently resumable stage components.
- [ ] Run real-model, multi-round continual-memory experiments only after all
  Grounded Runtime gates pass.
