# Whole-Policy Evolution V2 architecture

## Authority boundary

Policy Registry V2 is the sole formal authority. It contains exactly one
active policy. `configs/skills.json`, manual skills, strategy memory, and old
preflight logic are legacy inputs and are rejected in `formal_mode=True`.

Formal repair consumes a validated `PolicyState`; adapters cannot activate a
policy, mutate a frozen manifest, decide promotion, or write the registry.

## Round state machine

The resumable runner executes:

`load active → conditioned red generation → validity gate → blue challenge →
residual archive → adaptation/target split → child search → screening →
paired replay → decision → atomic commit → renewed challenge`.

Each checkpoint binds hashes of its inputs and outputs. Completed stages
cannot be rewritten or reordered. Promotion requires the candidate's parent
ID and hash to match the current active policy. A process interrupted after an
atomic commit can resume only when the active hash matches the recorded
winner.

## Rollback

Before promotion, the registry saves an immutable snapshot named by its
registry hash. The promoted policy binds `rollback_registry_hash` and
`rollback_policy_id`. Rollback fails closed if that exact version is missing,
corrupt, or does not restore the bound parent.

Registry lifecycle operations are formal transitions:

- `retire` may mark only a non-active, non-base policy that is not the active
  policy's rollback parent;
- `audit-fail` records hash-bound failure evidence in an adjacent hash-chain
  tombstone ledger;
- an audit failure of the active policy restores the exact pre-promotion
  registry snapshot;
- a tombstoned policy hash can never be promoted again, even though exact
  rollback restores its old candidate entry.

## Residual archive

Every poison binds `challenged_policy_hash`. Only validity evidence with
`PROVEN_NON_EQUIV` can enter the archive. Archive cells include family,
effect, affected role, timing bucket, and edit scope, so the same family can
retain different effect/role elites. Each policy-bound cell materializes
separate `hardest`, `minimal_edit`, and `most_learnable` views, plus a Pareto
frontier. Duplicate poison evidence is deduplicated and lineage graphs reject
missing parents and cycles.

`PROVEN_NON_EQUIV` means an explicit, hash-bound counterexample, not merely
an incomplete equivalence proof. Oracle result, witness, toolchain, and
command hashes are mandatory and are rechecked when the archive is written.

Lineage execution uses a frozen operator space and a hash-bound plan. Adapter
output is accepted only after runner-owned postcondition checks for policy
binding, parent descriptor, family/effect/role transition, lineage and
composition depth, and single-module/single-block scope. A lineage parent may
have challenged an earlier policy; the child poison always binds the current
active policy.

One dispatcher covers all six frozen operators: fresh generation, dependency
deepening, role relocation, temporalization, two-effect composition, and
counterexample-guided revision. These are deterministic descriptor
transformations; adapters remain responsible for tool-specific RTL
materialization and cannot bypass runner postconditions.

`hard_residual` and `borderline_residual` entries enter the residual archive
only with structured reachable/weakly-reachable teacher evidence.
`mostly_covered` and `covered` entries are routed to a separate covered
archive, so red search can avoid them without consuming adaptation capacity.

Before red generation the runner freezes a field-whitelisted
`red_search_context.json`. It contains archive descriptors and outcomes but
never target replay labels, reference patches, or child validation data.

Residual selection uses all accumulated residual entries for the policy hash
that remains active, not just the current round's discoveries. Each round
freezes the accumulated rows and their entry hashes. Promotion changes the
active hash and therefore starts a new isolated accumulation pool.

## Learnability teacher

The formal teacher protocol binds the active policy hash and hashes both the
primary and teacher budgets. `reachable` requires a same-model,
strictly-expanded budget with at least one success; `weakly_reachable`
requires stronger-model or expanded-population evidence. `unknown` and
`unlearnable_or_budget_exceeded` have distinct incomplete/exhausted semantics.
Teacher evidence is stored separately and hash-bound.

## Observability

Policy, red, oracle, arena, rollback, and RAAM memory modules append hash-chained
`r3e-event-v1` JSONL records. A promotion event binds parent and child policy
hashes, registry hashes before/after, round ID, timestamp, and code version.
Event streams are diagnostic evidence, not state authority.

An active-memory-bank update is represented as a normal child `PolicyState`
whose optional `memory_binding` contains the bank, retriever, activation
guard, and control-whitelist hashes. Empty bindings preserve the V1 policy
hash. A non-empty binding changes the child hash and therefore must pass the
same paired replay, single-active registry commit, stale-child rejection, and
exact rollback path as any other whole-policy change.

Each round also freezes a run context binding code version, policy, registry,
round config, and adapter/toolchain fingerprint. `round_audit.json`
reconstructs every promotion decision from the original paired rows, checks
manifest source relationships and design isolation, verifies learnability and
renewed-challenge bindings, and is appended idempotently to a hash-chain round
ledger.

## Adapter conformance

Every adapter declares an exact `r3e-adapter-toolchain-v1` fingerprint. Each
of its six method outputs carries an operation-specific schema plus model ID,
budget hash, verifier hash, toolchain fingerprint hash, command hash, and
result hash. The runner validates this envelope before accepting red,
validity, challenge, learnability, screening, or replay output. Rehashed
outputs from an undeclared toolchain are rejected.

Formal prompt loading follows the same fail-closed rule whether invoked
through `PolicyRuntime.from_registry(...)` or `repair_one(..., policy)`.
Every child inherits the base frozen-asset map, and a prompt hash mismatch is
rejected before repair. Legacy preflight is imported only in
`formal_mode=False`.

## Current status

System Upgrade Complete V1 freezes the Phase 0–4 software interfaces and
validates them with deterministic fake adapters. Its hash-bound manifest is
`configs/evolution/system_upgrade_complete_v1.json`. Real model identity,
experiment seeds, promotion thresholds, non-target datasets, child counts,
and experiment budgets remain future experiment-design inputs.
