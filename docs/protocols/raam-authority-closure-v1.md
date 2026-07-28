# RAAM Authority Closure V1

This addendum freezes the authority semantics layered on top of RAAM Protocol
Skeleton V1. It is an engineering milestone, not an empirical continual-memory
result and not a Grounded Runtime claim.

## Stable identities

- `policy_instance_hash` identifies an immutable policy object and its lineage.
- `effective_policy_hash` identifies runtime behavior: configuration, budgets,
  frozen assets, and the effective memory-bank identity.
- `memory_bank_instance_hash` (`ActiveMemoryBank.bank_hash`) identifies one bank
  object, its lineage, construction round, status, and provenance.
- `effective_memory_bank_hash` identifies only executable bank behavior:
  semantic memory definitions, retriever, activation guard, whitelist, plan
  compiler, conflict policy, and activation bound.
- Registry registration rejects a child whose effective policy hash equals its
  parent's, even when instance IDs or bank versions differ.

## Stable memory definitions

`memory_definition_hash` is independent of policy lineage and binds exactly:

- trigger predicate;
- control delta;
- control-memory compiler version;
- whitelist schema version.

Policy instance/effective hashes, episode hashes, and round IDs belong to
evidence links and qualification records. A bank cannot contain two memories
with the same semantic definition hash.

## Evidence authority

Every memory promotion authority bundle freezes the source `VerifiedEpisode`
objects in addition to the memory, evidence set, qualification replay, and
compatibility proof. Verification fails closed unless it reconstructs:

1. the exact evidence-set schema and memory binding;
2. unique, non-empty episode links;
3. every link hash and the canonical link order;
4. support count and evidence-set hash;
5. every source episode object and episode hash;
6. round, policy-instance, and effective-policy bindings for every link;
7. the qualification decision and its exact evidence-set hash.

The authority bundle may not substitute a self-consistent but nonexistent
episode graph.

## Cumulative provenance

A memory-bank child source manifest binds the sorted semantic definition,
current evidence-set, and qualification-decision hashes for every selected
memory. Newly accumulated evidence therefore changes the child provenance even
when the stable memory definition is reused.

Promotion provenance cross-checks the candidate residual-manifest binding and
recomputes the toolchain fingerprint hash. Frozen target/non-target manifests
and paired replay rows remain independently reconstructed by the registry.

## Audit claim boundary

`r3e.memory.audit` emits `authority_graph_summary`. This is a deterministic
relationship summary, not proof that all graph endpoints, paths, runtime
artifacts, oracle evidence, and registry transitions form a fully verified
authority DAG. That stronger claim remains part of Grounded Runtime V1.
