# Grounded Red Discovery V1 implementation checklist

Status date: 2026-07-28

## Grounded Red Protocol Foundation V1

- [x] Freeze a versioned ontology containing 25 semantic bug families.
- [x] Keep family, operator, and runtime-effect registries independent.
- [x] Freeze ten GRD-2 operator contracts with preconditions, inverse names,
  executor class, and maximum module/block/AST-edit scope.
- [x] Reject unknown family/operator/effect references and cross-registry
  mismatches.
- [x] Bind every MutationPlan to policy instance/effective hashes and the
  complete registry bundle.
- [x] Bound D0-D4 targets and require exactly two parents for D4 composition.
- [x] Add hash-bound multi-parent lineage and reject missing parents/cycles.
- [x] Add runner-owned command receipt schema with timeout/crash/tool-error
  distinctions.
- [x] Add parser semantic-diff and runtime-effect proof schemas.
- [x] Reconstruct G1-G11 from exact evidence instead of trusting adapter
  `oracle_ok` or validity booleans.
- [x] Require two deterministic poison executions.
- [x] Require clean-pass, poison-functional-fail, and revert-pass chains.
- [x] Reject testbench/oracle tampering, nondeterminism, collateral scope,
  trivial mutation, effect mismatch, and failed minimization.
- [x] Add valid/residual/covered/rejected append-only archives.
- [x] Bind novelty to semantic diff, effect, failure signature, design, and
  difficulty rather than poison ID or line number.
- [x] Add hash-bound coverage state, uncovered-cell ordering, and family quota.
- [x] Add sanitized policy/bank-bound capability packet.
- [x] Add deterministic fake evidence and multi-round fake-system validation.
- [x] Keep legacy `r3e.red.validity_gate` outside Grounded Red authority.

## GRD-1 execution still open

- [ ] Execute real parser/elaboration/compile/simulation/formal/oracle commands.
- [ ] Persist and independently parse provider receipts and output artifacts.
- [ ] Enforce wall-time, output-size, process-tree, and resource budgets in the
  real command runner.
- [ ] Derive runtime observations without adapter-supplied truth.
- [ ] Integrate Grounded Red admission as the formal arena validity path.

## GRD-2 through GRD-8 still open

- [ ] Implement and test parser-backed materializers/inverses for ten core
  operators.
- [ ] Implement real delta-debugging minimization.
- [ ] Persist and resume real coverage-guided planning.
- [ ] Derive difficulty from real temporal/dependency artifacts.
- [ ] Implement grounded memory bypass/deepening/conflict/transfer.
- [ ] Separate generator, validator, minimizer, and resource scheduler roles.
- [ ] Execute controlled composition with intermediate validity.
- [ ] Run a real-model multi-round pilot only after all prior gates pass.
