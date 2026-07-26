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
retain different effect/role elites. Duplicate poison evidence is deduplicated
and lineage graphs reject missing parents and cycles.

## Observability

Policy, red, oracle, arena, and rollback modules append hash-chained
`r3e-event-v1` JSONL records. A promotion event binds parent and child policy
hashes, registry hashes before/after, round ID, timestamp, and code version.
Event streams are diagnostic evidence, not state authority.

## Current status

The current phase freezes software interfaces and validates them with
deterministic fake adapters. Model identity, seeds, promotion thresholds,
non-target datasets, child counts, and experiment budgets remain future
experiment-design inputs.
