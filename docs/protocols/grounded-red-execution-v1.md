# Grounded Red Execution V1

Status: frozen executable GRD-1 slice  
Date: 2026-07-28

## Authority boundary

This protocol converts one verified `MutationPlan` into runner-owned,
reconstructable evidence. It does not accept adapter-supplied parse, compile,
simulation, oracle, or semantic-validity booleans.

The executable slice is intentionally narrow:

- Icarus Verilog is the parse, elaboration, compile, and simulation provider;
- `r3e-stdout-oracle-v1` parses exactly one frozen `R3E_ORACLE` line;
- `r3e-verilog-token-ast-v1` materializes only `replace_comparator`;
- the Icarus frontend remains authoritative for syntax and elaboration;
- the constrained token AST supplies stable node identity, one-token semantic
  editing, semantic-diff evidence, and an exact inverse.

Formal property checking and the other nine GRD-2 operators remain outside this
milestone.

## Frozen execution chain

For clean, two poison repeats, and revert, the provider executes:

```text
iverilog parse
→ iverilog top-module elaboration
→ iverilog simulation-binary compile
→ vvp simulation
→ stdout oracle provider
→ aggregate command receipt
```

Each child command receipt binds the run context, RTL subject, testbench,
working directory identity, executable, toolchain fingerprint, stdout/stderr,
produced simulation artifact, result kind, exit code, and wall time. The
aggregate binds all child receipt hashes and the oracle provider receipt.

The toolchain fingerprint binds both the installed wrapper scripts and their
underlying `libexec` Icarus engines, plus the exact local runner and oracle
provider implementations.

## Command-runner constraints

The runner:

- invokes argv directly with `shell=False`;
- uses an executable allowlist and rejects paths outside one workspace;
- inherits no caller environment or credentials;
- uses a private temporary directory and restrictive umask;
- limits wall time, CPU, address space, and output-file size;
- starts a new process session and kills the process group at the receipt
  boundary;
- persists raw stdout/stderr beneath a receipt-ID-addressed artifact directory;
- distinguishes `completed`, `timeout`, `crash`, `resource_limit`, and
  `tool_error`.

A missing declared output receives a deterministic hash of its
workspace-relative path, so compile and simulation receipts can agree that the
same artifact was never created. Any compile or simulation tool error still
fails the executability gate.

## Oracle contract

Simulation stdout must contain exactly one line matching:

```text
R3E_ORACLE pass=<0|1> signature=<token> first=<token> topology=<token>
```

The parser receipt binds the raw stdout hash and frozen testbench hash. Missing,
duplicate, malformed, or non-UTF-8 records are inconclusive oracle output, not
a functional bug. A passing record must use `none` for all three descriptors;
a failing record must provide non-`none` values for all three.

## AST materialization contract

`replace_comparator` accepts a plan-bound comparison-node hash and performs one
of these reversible mappings:

```text
<  ↔ <=
>  ↔ >=
== ↔ !=
```

The materialization receipt binds clean/poison source hashes, normalized token
AST hashes, clean/poison node hashes, target module, old/new operator, and the
single edit count. The inverse must restore the exact clean source hash and AST
hash. Icarus then independently parses and elaborates the materialized RTL.

## Admission and reconstruction

Real execution uses:

- `r3e-red-admission-evidence-v2`;
- `r3e-red-admission-decision-v2`;
- `authority_mode: runner_owned_grounded_execution`;
- `r3e-grounded-red-execution-bundle-v1`.

The bundle verifier reconstructs the plan, materialization, semantic provider,
toolchain, command/provider chain, G1-G11 decision, and all cross-object hashes.
A tool failure may produce a reconstructable rejected decision, but never an
admitted functional bug.

## Claim boundary

This milestone demonstrates local, deterministic execution of a small frozen
RTL fixture using the installed Icarus toolchain. It is not a claim of a formal
engine, full SystemVerilog AST coverage, all ten operators, arena integration,
real-model red discovery, multi-round improvement, or empirical bug yield.
