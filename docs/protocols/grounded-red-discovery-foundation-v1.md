# Grounded Red Discovery Protocol Foundation V1

This protocol freezes authority objects and fail-closed boundaries for the
first Grounded Red Discovery engineering checkpoint. It does not claim that a
real parser, simulator, formal engine, oracle provider, model, or multi-round
experiment has been executed.

## Authority boundary

Family specialists and model adapters may propose a structured
`r3e-grounded-mutation-plan-v1`. A plan has no admission authority. It binds:

- current policy instance and effective-policy hashes;
- the frozen family/operator/effect registry bundle;
- target design, module, and AST-node hash;
- family, operator, and expected runtime effect;
- exact preconditions and mutation scope;
- difficulty target and bounded lineage.

Unknown registry entries, stale policy bindings, false preconditions, and scope
widening fail closed.

## Runner-owned evidence

Formal admission reconstructs:

- `r3e-grounded-command-receipt-v1`;
- `r3e-red-semantic-diff-receipt-v1`;
- `r3e-red-effect-receipt-v1`;
- `r3e-red-admission-evidence-v1`;
- `r3e-red-admission-decision-v1`.

Command receipts bind one subject, run context, toolchain, argv, working
directory, exit/timeout/crash state, bounded artifacts, observations, and a
content hash. All clean, poison, and revert receipts in an admission must use
the same run context and toolchain.

The deterministic fake receipt builder exists only to exercise reconstruction
and failure injection. Its output is not real Grounded Runtime evidence.

## Admission gates

Every decision reconstructs all eleven gates:

1. immutable clean/testbench/oracle/build inputs and allowed mutation files;
2. parser, elaboration, AST target, and operator preconditions;
3. clean parse/compile/simulation/oracle pass;
4. poison execution without timeout, crash, or tool-error dependence;
5. reproducible functional failure;
6. repeated effect, first-divergence, and topology determinism;
7. inverse transformation restores clean semantic hash and oracle pass;
8. declared family/operator and bounded parser semantic diff;
9. artifact-derived runtime effect matches the plan and repeated executions;
10. non-triviality rejection;
11. minimized poison preserves the failure signature.

Malformed evidence raises an authority violation. Well-formed evidence that
fails a gate yields a hash-bound rejected decision and may enter only the
rejected archive.

## Archives and lineage

The formal store separates valid, residual, covered, and rejected append-only
JSONL archives. Valid/residual/covered entries require an admitted decision;
rejected entries require a non-admitted decision. Novelty binds semantic
family, operator, normalized AST diff, runtime effect, failure signature,
design, and difficulty band.

`r3e-red-lineage-v1` supports single-parent derivation and exactly two parents
for controlled composition. Missing parents, duplicate identities, conflicting
archive copies, and cycles are rejected.

## Coverage and capability

`r3e-red-coverage-state-v1` tracks family × design × role × temporal context ×
difficulty × effective policy × effective memory bank. The deterministic
planner prefers least-covered cells and enforces per-family quota.

`r3e-red-capability-packet-v1` binds the current policy instance/effective
identity and effective memory-bank identity. It exposes only public region and
coverage summaries; source episodes, patches, prompts, rationale, reference
repairs, hidden cases, and oracle truth are forbidden recursively.

## Deferred Grounded Runtime work

This checkpoint does not complete:

- real command execution and provider receipts;
- parser-backed AST materializers and inverse transformations;
- artifact-derived FailureDescriptor integration;
- real operator minimization;
- real coverage/difficulty curriculum;
- real memory bypass/deepening/conflict/transfer;
- controlled composition execution;
- model-driven or multi-round empirical evaluation.
