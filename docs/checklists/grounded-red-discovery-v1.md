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

## GRD-1 executable providers

- [x] Execute real Icarus parse/elaboration/compile/simulation commands.
- [x] Parse a frozen stdout oracle record through a hash-bound provider.
- [x] Persist command stdout/stderr and bind provider receipts to exact output
  artifact hashes.
- [x] Enforce argv-only execution, executable/path allowlists, clean
  environment, wall-time, output-size, address-space, CPU, and process-tree
  limits in the real command runner.
- [x] Derive parse/elaboration/compile/simulation/oracle observations from
  runner-owned results rather than adapter-supplied truth.
- [x] Reject frozen RTL/testbench mismatch before execution.
- [x] Distinguish timeout, crash, resource limit, and tool error from a
  functional mismatch.
- [x] Add parser-backed materializers for all ten frozen GRD-2 operators with
  exact inverse and source/AST/provider receipt binding.
- [x] Reconstruct a V2 execution bundle and G1-G11 decision independently.
- [x] Add a restricted Yosys SAT formal-engine provider, formal property
  receipts, and a clean-proof/poison-counterexample/revert-proof triplet.
- [x] Treat malformed output, timeout, crash, resource limit, and tool error as
  inconclusive formal evidence.
- [ ] Integrate Grounded Red admission as the formal arena validity path.

## GRD-2 through GRD-8 still open

- [x] Implement and test `replace_comparator`.
- [x] Implement and test parser-backed materializers/inverses for the remaining
  nine core operators through one unified dispatch.
- [ ] Implement real delta-debugging minimization.
- [ ] Persist and resume real coverage-guided planning.
- [ ] Derive difficulty from real temporal/dependency artifacts.
- [ ] Implement grounded memory bypass/deepening/conflict/transfer.
- [ ] Separate generator, validator, minimizer, and resource scheduler roles.
- [ ] Execute controlled composition with intermediate validity.
- [ ] Run a real-model multi-round pilot only after all prior gates pass.
