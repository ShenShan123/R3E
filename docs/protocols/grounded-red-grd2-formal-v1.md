# Grounded Red GRD-2 and Formal Provider V1

Status: frozen engineering protocol

Date: 2026-07-28

Parent: Grounded Red Execution V1

## Scope

This milestone completes the frozen ten-operator GRD-2 portfolio and adds a
runner-owned Yosys SAT provider. It is a system capability milestone, not a
real-model discovery experiment and not empirical bug-yield evidence.

The executable operator set is:

1. `replace_comparator`;
2. `negate_predicate`;
3. `shift_boundary_constant`;
4. `shift_slice`;
5. `change_signedness_cast`;
6. `change_assignment_kind`;
7. `change_reset_semantics`;
8. `change_enable_condition`;
9. `change_counter_terminal`;
10. `change_fsm_transition`.

## Unified operator authority

`operator_nodes(source, module, operator_id)` locates only nodes admitted by
the frozen operator contract. A `MutationPlan.target_ast_node_hash` selects one
exact node. `materialize_operator(plan, clean_source)` performs one bounded
semantic AST-node edit and emits:

- exact clean and poison source/normalized-AST hashes;
- clean and poison node hashes;
- one source-span edit with old/new text;
- a semantic-diff receipt;
- a provider receipt bound to the parser/materializer implementation.

`inverse_materialization(receipt, poison_source)` accepts only the exact poison
hash, exact poison node, and exact edited span. It must restore both the clean
source hash and normalized-AST hash. A stale node, operator/family mismatch,
receipt mutation, extra edit, or non-exact inverse fails closed.

The original comparator V1 receipt remains verifiable for compatibility.
New unified operator-AST plans use `r3e-red-ast-materialization-v2`.

## Formal provider authority

`YosysFormalProvider` runs Yosys without a shell, ambient credentials, or
unbounded paths. The common command runner enforces:

- executable and workspace allowlists;
- frozen RTL and property hashes before execution;
- bounded wall time, CPU, address space, output size, and process lifetime;
- persisted stdout, stderr, script, RTL, property, and trace hashes;
- a local Yosys engine and provider-implementation fingerprint.

The provider accepts only a safe receipt prefix, a Verilog top identifier, and
a proof depth from 1 through 1024. It runs:

```text
read_verilog -formal -sv <rtl> <property>
prep -top <top> -flatten
chformal -lower
sat -prove-asserts -seq <depth> -show-ports -dump_json <trace>
```

The formal parser recognizes exactly one Yosys SAT success or counterexample
record. Tool errors, timeouts, crashes, resource limits, malformed/non-UTF-8
output, missing verdicts, or ambiguous verdicts are `inconclusive`; they are
never proofs or counterexamples.

## Proof-triplet gate

`execute_triplet(...)` and `verify_formal_proof_triplet(...)` require:

```text
clean RTL   -> proved
poison RTL  -> counterexample
revert RTL  -> proved
```

All three executions must share the exact property hash, top module, depth, and
toolchain fingerprint. Revert RTL must have the exact clean RTL hash; poison
RTL must differ. Each parsed verdict is cross-bound to the command receipt and
the script/stdout/stderr/trace artifacts.

The proof triplet is formal evidence only. Grounded Red admission remains
authoritative through `decide_grounded_admission()` and the current Icarus
execution bundle until a later milestone explicitly integrates formal evidence
into the arena validity schema.

## Conformance

The frozen validation requires:

- dispatch, inverse, stale-target, and receipt-tamper tests for all ten
  operators;
- clean and poison syntax compilation for each operator when Icarus is
  available;
- a real clean-proof / poison-counterexample / revert-proof Yosys test;
- frozen-property mismatch rejection before execution;
- malformed-property execution classified as inconclusive/tool-error;
- exact milestone and protocol-asset hash reconstruction;
- full repository tests, deterministic multi-round fake systems, dataset
  verification, and release/privacy audit.

## Explicitly deferred

- full SystemVerilog parser coverage beyond the constrained token-AST subset;
- real delta-debugging;
- formal evidence as arena admission authority;
- real coverage/difficulty search;
- real-model multi-round discovery;
- empirical effectiveness or bug-yield claims.
