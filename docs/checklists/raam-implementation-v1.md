# RAAM implementation checklist

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
- [x] Freeze a separate “RAAM System Upgrade Complete V1” milestone after full
  tests, deterministic whole-policy rounds, deterministic memory promotion,
  release audit, and dataset verification pass.
