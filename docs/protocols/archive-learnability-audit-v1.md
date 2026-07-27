# Archive, learnability, and round audit protocols

## Red search context

`r3e-red-search-context-v2` is created by the authoritative runner before red
generation. It binds the current policy and contains only whitelisted archive
summary fields:

- poison ID and archive kind;
- challenged policy hash and archive cell;
- family, effect, affected role, and failure signature;
- hardness class/value and learnability label.
- parent poison/policy hash, lineage depth/operator, and bounded composition
  and sequential depth.

Raw target results, reference repairs, child validation, and target oracle
labels are rejected before the adapter is called.

Formal poison admission is fail closed. `PROVEN_NON_EQUIV` requires an
explicit SAT counterexample and canonical `oracle_result_hash`,
`counterexample_hash`, `toolchain_fingerprint_hash`, and `command_hash`.
Unproven equivalence cells without a witness are `INCONCLUSIVE`; they cannot
enter either archive. Archive admission re-verifies the complete validity
record and its result hash instead of trusting an adapter boolean.

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
Residuals are accumulated only while the same policy hash remains active.
Every round freezes `accumulated_residuals.jsonl` and
`residual_accumulation.json`, binding all included archive-entry hashes and
discovery rounds. Later archive mutations cannot alter the round decision.
Once promotion changes the active hash, the new policy starts a distinct
accumulation pool.

## Executable lineage operators

`configs/red/lineage_operator_space_v1.json` freezes the allowed operators and
scope ceilings. A generated poison carries an `r3e-lineage-plan-v1` binding
the current challenged policy, parent poison and its challenged-policy hash,
operator, expected depth, and frozen operator-space hash.

`execute_lineage_operator(...)` delegates materialization to an adapter, then
the formal validator checks all postconditions. `relocate` must change the
affected role, `temporalize` must increase sequential depth, `compose` must
increase bounded composition depth by exactly one, and
`counterexample_revise` must change the effect or failure signature.
Non-compose operators preserve family. Cross-policy ancestry is allowed so a
new active policy can be challenged by a deepened residual from its parent
policy, while the new poison itself must bind the current active policy hash.

The deterministic reference materializers are `materialize_fresh`,
`materialize_deepen`, `materialize_relocate`, `materialize_temporalize`,
`materialize_compose`, and `materialize_counterexample_revise`.
`materialize_lineage_operator` dispatches all six with an exact parameter
contract. They produce structured mutation descriptors only; RTL/tool-specific
realization remains behind the adapter boundary. Each materializer is checked
against the same frozen plan before archive admission.

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
`residual_selection.json`; that selection also binds the frozen accumulation
snapshot rather than the mutable global archive.

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
