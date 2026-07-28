# R3E: Red–Blue Adversarial Evolution for RTL Repair

Anonymous research-code release for R³E. This upload package contains the core
implementation, frozen public benchmark inputs, offline audit/aggregation
utilities, and protocol tests.

It intentionally excludes private cluster launchers, generated results,
model responses, repair candidates, runtime memory/registry contents,
credentials, internal documents, machine-specific paths, and third-party tool
installations.

## Layout

- `r3e/semantic_repair_bench/`: functional RTL repair, correctness gates,
  legacy-compatible red/blue experiment components and deterministic repair
  primitives.
- `r3e/policy/`: whole-policy schema, frozen local search, paired promotion
  gate, single-active formal registry, and rollback.
- `r3e/red/`: active-policy capability packets, validity, hardness, novelty,
  learnability, lineage, and residual archive.
- `r3e/arena/`: hash-bound manifests, paired replay, resumable round state,
  renewed-challenge binding, and the sanitized adapter-driven round runner.
- `r3e/protocol/`: canonical hashing, atomic writes, hash-chain ledgers, and
  toolchain fingerprints.
- `r3e/memory/`: Replay-Activated Adversarial Memory schemas, immutable
  stores, lifecycle, retrieval/reactivation, execution-plan compilation,
  paired shadow replay, qualification, compatibility, and audit.
- `r3e/microsurgeon_frontend/`: syntax and semantic repair components.
- `r3e/microsurgeon_flow/`: backend repair, skill routing, and guarded
  preflight components.
- `r3e/tools/`: synthesis, timing, and structural-analysis adapters.
- `experiments/`: experiment-design components plus offline aggregate, audit,
  and analysis utilities. Private cluster launchers are not included.
- `configs/base_policy/`: frozen B0 policy, prompt assets, and policy search
  space. `configs/legacy/` contains non-authoritative manual adapters.
- `datasets/`: frozen CirFix-39, Literature-32, Strider-14, and RTLFixer-50
  public inputs with repository-relative SHA-256 manifests.
- `tests/`: offline unit, integrity, and interface tests.
- `scripts/`: dataset packaging/verification and release-safety checks.

## Setup

Python 3.11 or newer is required. Icarus Verilog and Yosys are optional for
tests marked with their corresponding tool markers.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export PYTHONPATH="$PWD:$PWD/r3e:$PWD/experiments/public_external_benchmarks"
```

Provider credentials, if used by library clients, are read only from
environment variables. No credential or provider response is stored in this
release.

## Legacy R³E

The v1 compatibility surface contains strategy memory, manual/frozen skills,
skill preflight, and the historical B0/B1/B2 experiment drivers. Their
boundary is documented under `r3e/legacy/`, `experiments/legacy/`, and
`configs/legacy/`. Skill routing/preflight implementations have moved to
`r3e/legacy/`; monolithic experiment drivers retain their old paths with
explicit legacy markers so historical imports keep working. They are
available only with `formal_mode=False`.

`configs/skills.json` is a legacy compatibility asset. Its entries are
`manual` and `legacy_only`; they have no promoted or active-policy authority.

## Whole-Policy Evolution

The v2 architecture consists of exactly one active `PolicyState`, red
challenges conditioned on that policy hash, a multi-elite residual archive,
hash-bound child search, paired target/non-target replay, atomic promotion,
exact rollback, executable lineage deepening, policy-bound cross-round
residual accumulation, and a renewed challenge bound to the newly active
policy.

Formal runtime reads only Policy Registry V2. A child must bind the exact
active parent hash, stale children cannot be promoted, and rollback restores
the exact pre-promotion registry snapshot.

Non-authoritative policies can be retired through the registry CLI. An
`audit-fail` transition writes a permanent hash-chain tombstone; if the failed
policy is active, the registry restores its exact parent snapshot and the
failed hash remains barred from later promotion.

> 当前仓库提供系统基础设施和协议实现，不代表已经获得真实模型驱动的多轮演化实验结果。

## Replay-Activated Adversarial Memory

RAAM is the formal continual-memory path introduced after System Upgrade
Complete V1. It stores every verified red/blue episode, distills only
structured `ControlMemory` deltas, qualifies them with paired shadow replay,
and gives a memory real execution authority only after both whole-policy bank
promotion and current-case reactivation.

The path is deliberately non-Prompt: a memory may select analyzers, evidence
windows, RTL slicing, candidate allocation/ranking, verifier order, and
stopping rules. It cannot contain prompt fragments or historical patches,
change the oracle/testbench/registry/model route, or execute AST rewrites.
`configs/memory/control_whitelist_v1.json` freezes this boundary.

The initial RAAM foundation implements:

- append-only, content-addressed `VerifiedEpisode` and `ControlMemory` stores;
- append-only lifecycle and relation-graph events;
- runtime-observable `FailureDescriptor` construction;
- active-bank-only retrieval, deterministic abstention, compatibility and
  conflict checks;
- zero-memory-token `ExecutionPlan` compilation;
- same-case/seed/model/budget/verifier/toolchain shadow pairing and a
  runner-owned five-gate qualification decision;
- hash-bound Active Memory Bank candidates carried by a child `PolicyState`;
- policy-bound bank loading, memory event logging, and cross-store audit.
- automatic arena `VerifiedEpisode` emission and resumable memory rounds;
- deterministic candidate clustering, relation/merge/split consolidation,
  bounded banks, and cross-policy incremental revalidation;
- model/budget/verifier/toolchain/command/result adapter conformance;
- sanitized memory-aware red capability packets and executable
  bypass/deepening/conflict operators bound to real RTL source hashes.

Run a memory qualification/promotion round with:

```bash
python -m r3e.memory.runner \
  --registry runtime/registry/policy_registry.json \
  --memory-root runtime/memory \
  --target-manifest runtime/rounds/R001/target_manifest.json \
  --non-target-manifest runtime/rounds/R001/non_target_manifest.json \
  --adapter your_package.memory_adapter:factory \
  --round-id MR001 \
  --work-dir runtime/memory/rounds/MR001 \
  --config memory_round_config.json
```

RAAM is an engineering upgrade, not a real-model result. Its deterministic
adapter proves the protocol and state machine without model calls; it does not
demonstrate empirical memory gain.

The deterministic Phase 1–9 foundation is frozen as **RAAM Protocol Skeleton
V1** in `configs/evolution/raam_protocol_skeleton_v1.json`. Its child milestone,
**RAAM Authority Closure V1**, is frozen in
`configs/evolution/raam_authority_closure_v1.json` and binds strict episode-backed
evidence reconstruction, instance/effective policy and bank identities, stable
cross-policy memory definitions, semantic bank deduplication, and cumulative
memory provenance. Runner-owned oracle/descriptor/RTL semantic grounding remains
open and blocks a Grounded Runtime or empirical continual-learning claim.

### Grounded Red Discovery

The red-team V2 engineering path is defined in
`docs/design/red-v2.md`. Its first checkpoint, **Grounded Red Protocol
Foundation V1**, introduces frozen family/operator/effect registries,
policy-bound MutationPlan objects, reconstructable G1-G11 admission,
semantic/effect receipts, separate formal archives, coverage state, and a
sanitized active-blue capability packet.

Its child checkpoint, **Grounded Red Execution V1**, adds a restricted real
command runner, an Icarus Verilog provider for parse/elaboration/compile/
simulation, a receipt-backed stdout oracle, and the first parser-backed
`replace_comparator` materializer with exact inverse. A real execution emits a
V2 evidence/decision bundle; compile errors, timeouts, crashes, resource limits,
invalid oracle output, or frozen-input mismatch cannot count as functional
bugs.

The next checkpoint, **Grounded Red GRD-2 Formal Provider V1**, completes the
ten frozen parser-backed operator materializers behind one dispatch and exact
inverse interface. It also adds a restricted Yosys SAT provider whose receipts
bind the property, RTL, generated script, stdout/stderr, counterexample trace,
toolchain, and parser implementation. Its formal triplet requires clean proof,
poison counterexample, and exact-revert proof.

Run the model-free protocol fixture with:

```bash
python -m r3e.red.grounded.fake_system \
  --workspace /tmp/r3e-grounded-red-foundation-v1 \
  --rounds 3
```

Run the real Icarus slice with a frozen MutationPlan, policy, RTL, testbench,
and their exact hashes:

```bash
python -m r3e.red.grounded.execution \
  --plan mutation_plan.json \
  --policy configs/base_policy/frozen_base_policy_v1.json \
  --clean-rtl runtime/grd1/counter.v \
  --testbench runtime/grd1/tb.v \
  --top-module tb \
  --workspace runtime/grd1 \
  --run-context-hash sha256:... \
  --frozen-clean-rtl-hash sha256:... \
  --frozen-testbench-hash sha256:... \
  --allowed-file-manifest-hash sha256:...
```

The fake fixture remains model-free and is not grounded evidence. The
operator library intentionally supports a constrained parser-backed
SystemVerilog subset rather than claiming complete language coverage. Formal
triplet receipts are independently reconstructable, but are not yet wired into
arena admission authority. No real-model discovery run, bug-yield result, or
empirical gain is claimed. The legacy `r3e.red.validity_gate` remains available
for historical Whole-Policy compatibility, but is not Grounded Red admission
authority.

### Registry and migration

Initialize the sole formal registry from the frozen base policy:

```bash
python -m r3e.policy.registry_v2 init \
  --base configs/base_policy/frozen_base_policy_v1.json \
  --registry runtime/registry/policy_registry.json \
  --ledger runtime/registry/decision_ledger.jsonl
```

Legacy skills can be frozen into the base policy without importing any old
promotion history:

```bash
python -m r3e.policy.migrate \
  --from legacy \
  --to registry-v2
```

The migration writes only to ignored `runtime/` paths by default. Manual
skills become a frozen B0 asset; entries without Registry V2 promotion
evidence never enter active evolution history.

Lifecycle commands remain registry-authoritative:

```bash
python -m r3e.policy.registry_v2 retire \
  --registry runtime/registry/policy_registry.json \
  --policy-id B0_R001_C02 \
  --reason "stale candidate"

python -m r3e.policy.registry_v2 audit-fail \
  --registry runtime/registry/policy_registry.json \
  --policy-id B0_R001_C01 \
  --expected-policy-hash sha256:... \
  --evidence audit_failure.json
```

Audit evidence uses `r3e-policy-audit-failure-v1`, includes
`audit_result: "fail"`, the exact policy ID/hash, structured checks and reason,
and an `evidence_hash` over all other fields.

### Model-free system validation

During the system-upgrade phase, validate the state machine with deterministic
fixtures instead of real models:

```bash
python -m r3e.arena.fake_system --rounds 2
```

`FakeRedAdapter`, `FakeBlueAdapter`, `DeterministicPromotionAdapter`, and
`FailureInjectionAdapter` exercise policy-conditioned poison generation,
different B0/B1 capability packets, promotion, resumed stages, renewed
challenge, and rollback without model or EDA calls. The interface-only
`configs/evolution/round_v1.json` intentionally contains no model, seed,
threshold, non-target dataset, child count, or experiment budget binding.

Phase 0–4 are frozen as **System Upgrade Complete V1**. The machine-readable
milestone is `configs/evolution/system_upgrade_complete_v1.json`; it binds the
base policy, policy search space, six-operator lineage space, round interface,
validation commands, and explicit experiment exclusions. It does not claim a
real-model multi-round result.

### Stable interfaces and events

The engineering interfaces are:

```python
from r3e.policy.repair import repair_one

repair_one(case, work_dir, policy_state)
generate_poison(case, challenged_policy, capability_packet, archive, mutator=...)
run_round(registry, red_adapter, blue_adapter, manifests)
```

Formal red adapters receive a hash-bound `red_search_context` containing only
whitelisted residual/covered archive summaries. Learnability probes return a
structured `r3e-learnability-v1` object that binds the active policy, primary
budget, separately expanded teacher budget, attempts, successes, and evidence
hash. Bare string labels are rejected.

Residual and covered poisons are stored separately. Adaptation manifests use
the union of the current hardness, minimum-edit, and learnability elite views
within each policy-bound MAP-Elites cell; covered poisons remain available to
red search without occupying adaptation capacity.
Rounds with fewer than two residual designs finish as auditable deferred
rounds with the parent unchanged instead of creating a leaking target split.
Those residuals remain available to later rounds only while that exact parent
hash stays active. `accumulated_residuals.jsonl` and
`residual_accumulation.json` freeze the cross-round pool used by selection.

Lineage generation is governed by
`configs/red/lineage_operator_space_v1.json`. Each poison carries a hash-bound
operator plan, and runner-owned checks enforce parent lineage, current-policy
binding, semantic operator postconditions, and mutation-scope ceilings. A
single dispatcher covers fresh, deepen, relocate, temporalize, compose, and
counterexample-guided revision.

All six adapter methods pass through `r3e-evolution-adapter-v1` conformance.
Their outputs bind an operation-specific schema, model, budget, verifier,
toolchain, command, and result hash before the runner accepts them.

Every round writes hash-chained JSONL events under `runtime/events/`:
`policy.jsonl`, `red.jsonl`, `oracle.jsonl`, `arena.jsonl`, and
`rollback.jsonl`. RAAM additionally writes `memory.jsonl`. Events are
observability records and never grant authority.
The adapter supplies environment-specific generation and evaluation; the
runner retains all registry, manifest, split, promotion, and rollback
authority.

Completed rounds also contain `toolchain.json`, `red_search_context.json`,
`residual_accumulation.json`, `residual_selection.json`, and
`round_audit.json`. Promotion decisions require
complete code/toolchain/manifest provenance and can be reconstructed from the
paired replay:

```bash
python -m r3e.arena.audit \
  --round-dir runtime/rounds/R001
```

The idempotent `runtime/rounds/round_ledger.jsonl` binds each completed round's
audit hash into a hash chain.

## Offline validation

The complete upload preflight is:

```bash
python scripts/check_release.py
python scripts/verify_datasets.py
pytest -q -p no:cacheprovider -m 'not network and not yosys'
```

`scripts/check_release.py` fails closed on experiment runners, generated
artifacts, caches, logs, EDA databases, private workspace paths, identity
emails, common secret formats, and machine-specific tool paths.

The dataset verifier checks row counts, unique case IDs, required evaluator
roles, repository-relative paths, byte sizes, per-file SHA-256 values, and
canonical case hashes. Dataset hashes and upstream licenses are documented in
`datasets/README.md`.

## Formal/legacy boundary

The released compatibility strategy-memory implementation is shadow-first and
fail-closed:

1. only trajectories with rebuildable outcomes and complete provenance enter
   shadow storage;
2. distilled strategies remain inactive until frozen target replay and
   disjoint non-target regression checks pass;
3. promotion binds residual/adaptation/target/non-target manifests, parent and
   child policy hashes, validation evidence, rollback state, and a hash-chain
   decision ledger;
4. formal runtime loads exactly one active whole policy and records its
   effective policy/configuration hashes for every repair.

The v1 mechanism is retained for historical reproduction only. New formal
rounds use Whole-Policy Evolution and do not consume free memory text, manual
skill routing, or legacy preflight output.

## Release boundary

The public datasets retain their upstream licenses and copyright notices. Code
in this repository is released under Apache-2.0. No historical Git metadata is
required for the package.
