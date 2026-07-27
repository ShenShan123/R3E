# Archive, learnability, and round audit protocols

## Red search context

`r3e-red-search-context-v1` is created by the authoritative runner before red
generation. It binds the current policy and contains only whitelisted archive
summary fields:

- poison ID and archive kind;
- challenged policy hash and archive cell;
- family, effect, affected role, and failure signature;
- hardness class/value and learnability label.

Raw target results, reference repairs, child validation, and target oracle
labels are rejected before the adapter is called.

Formal adapter signature:

```python
generate_red(parent, round_config, red_search_context)
```

## Learnability

`probe_learnability(policy, poison)` must return an object accepted by
`validate_learnability_result`. It includes:

- `label`;
- exact `challenged_policy_hash`;
- `teacher_mode`;
- a positive `teacher_budget`;
- teacher attempts, successes, and exhaustion status;
- structured evidence.

The runner derives and hashes the primary budget from `PolicyState`; adapters
cannot replace it. Same-model and population teachers must strictly expand at
least one primary budget dimension and cannot reduce another.

Only `reachable` and `weakly_reachable` residuals can enter the adaptation
archive. Covered samples do not require a teacher probe and are stored in the
covered archive.

If fewer than two residual designs remain after selection, the runner freezes
an empty target manifest, records `insufficient_residual_designs`, keeps the
current parent active, and completes an auditable no-promotion round. It does
not fabricate a non-disjoint split or crash midway through the state machine.

## MAP-Elites

Archive storage is append-only and hash-bound. Current elite views are
materialized deterministically per:

```text
challenged policy
→ family
→ effect
→ affected role
→ temporal bucket
→ edit scope
```

Three independent role winners are retained: maximum hardness, minimum edit
cost, and maximum learnability. The frozen residual manifest contains the
union of role winners and nondominated Pareto rows; it records the hash of
`residual_selection.json`.

## Round provenance and reconstruction

`toolchain.json` uses `r3e-round-provenance-v1` and binds:

- code version;
- round config hash;
- registry-before hash;
- active policy ID/hash;
- toolchain/adapter fingerprint hash.

Every promotion decision requires residual, adaptation, target, non-target,
paired-result, toolchain, and run-context hashes.

After renewed challenge, the runner reconstructs all decisions and freezes
`round_audit.json`. Verify it offline with:

```bash
python -m r3e.arena.audit --round-dir runtime/rounds/R001
```

The verifier rejects manifest leakage, child/parent mismatch, modified paired
outcomes, incomplete provenance, learnability tampering, wrong registry
versions, and renewed challenges bound to the wrong active policy.
