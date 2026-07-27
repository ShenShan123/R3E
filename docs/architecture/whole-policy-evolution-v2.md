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

## Residual archive

Every poison binds `challenged_policy_hash`. Only validity evidence with
`PROVEN_NON_EQUIV` can enter the archive. Archive cells include family,
effect, affected role, timing bucket, and edit scope, so the same family can
retain different effect/role elites. Each policy-bound cell materializes
separate `hardest`, `minimal_edit`, and `most_learnable` views, plus a Pareto
frontier. Duplicate poison evidence is deduplicated and lineage graphs reject
missing parents and cycles.

`hard_residual` and `borderline_residual` entries enter the residual archive
only with structured reachable/weakly-reachable teacher evidence.
`mostly_covered` and `covered` entries are routed to a separate covered
archive, so red search can avoid them without consuming adaptation capacity.

Before red generation the runner freezes a field-whitelisted
`red_search_context.json`. It contains archive descriptors and outcomes but
never target replay labels, reference patches, or child validation data.

## Learnability teacher

The formal teacher protocol binds the active policy hash and hashes both the
primary and teacher budgets. `reachable` requires a same-model,
strictly-expanded budget with at least one success; `weakly_reachable`
requires stronger-model or expanded-population evidence. `unknown` and
`unlearnable_or_budget_exceeded` have distinct incomplete/exhausted semantics.
Teacher evidence is stored separately and hash-bound.

## Observability

Policy, red, oracle, arena, and rollback modules append hash-chained
`r3e-event-v1` JSONL records. A promotion event binds parent and child policy
hashes, registry hashes before/after, round ID, timestamp, and code version.
Event streams are diagnostic evidence, not state authority.

Each round also freezes a run context binding code version, policy, registry,
round config, and adapter/toolchain fingerprint. `round_audit.json`
reconstructs every promotion decision from the original paired rows, checks
manifest source relationships and design isolation, verifies learnability and
renewed-challenge bindings, and is appended idempotently to a hash-chain round
ledger.

## Current status

The current phase freezes software interfaces and validates them with
deterministic fake adapters. Model identity, seeds, promotion thresholds,
non-target datasets, child counts, and experiment budgets remain future
experiment-design inputs.
