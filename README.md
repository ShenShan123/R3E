# R3E: Red–Blue Adversarial Evolution for RTL Repair

Anonymous research-code release for R³E. This upload package contains the core
implementation, frozen public benchmark inputs, offline audit/aggregation
utilities, and protocol tests.

It intentionally excludes private cluster launchers, generated results,
model responses, repair candidates, runtime memory/registry contents,
credentials, the entire local `docs/` tree, machine-specific paths, and
third-party tool installations. Public milestones bind repository-shipped
schemas/configuration instead of unpublished local design documents.

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
- `r3e/blue/portfolio/`: candidate-level lens registry, allocation plan,
  provider conformance, runner-owned verification/selection, ExecutionTrace
  V2, offline audit, and deterministic ACP fixtures.
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
environment variables. Runtime rounds keep hash-bound proposal/usage artifacts
for audit, while this public release contains neither credentials nor generated
provider responses.

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
memory provenance. Its later Grounded Runtime child closes runner-owned
Icarus/Yosys-to-descriptor/episode authority binding. Its next child adds a
bounded signal/cycle observation protocol and a separate formal-rejection
route. The current engineering child adds a reconstructable coverage planner
and difficulty curriculum, parser-backed RAAM challenge operators, controlled
two-parent composition, cross-round revalidation, and a complete authority DAG.
Complete waveform semantics, complete language coverage, and empirical
continual-learning claims remain open.

### Adaptive Candidate Portfolio

**Adaptive Candidate Protocol V1** freezes the ACP-0 candidate-level authority
foundation in `configs/evolution/adaptive_candidate_protocol_v1.json`.
`configs/blue/lens_registry_v1.json` binds the existing generic, temporal,
control, and dataflow prompt assets by exact hash.

The formal portfolio executor deterministically derives candidate slots and
seeds from the effective policy, Grounded `FailureDescriptor`, portfolio, and
run seed. A candidate provider may return only a patch proposal and usage
receipt; it cannot report parse/formal/oracle success, rank candidates, or
select a winner. For artifact-bound formal providers, the proposal contains
only replacement RTL and an edit description; the runner derives module,
block, node, signal-role, operator-class, and normalized patch metadata from a
bounded buggy/candidate token-AST diff. Model-supplied semantic metadata is
rejected. A candidate with an unavailable/no-op AST diff receives a
runner-owned rejection signature and cannot pass scope/oracle admission, while
the remaining planned slots still execute. The runner owns candidate
verification, deterministic
oracle-backed selection, aggregate call/Token/post-call wall-time gates,
BlueEvaluation V2, and per-slot/lens ExecutionTrace V2. Offline audit rebuilds
the complete provider→generation→semantic-signature→verification→diversity
→selection chain.

Run the model-free ACP-0 conformance fixture with:

```bash
python -m r3e.blue.portfolio.fake_system \
  --project-root . \
  --out /tmp/r3e-acp0-blue-evaluation.json
```

The V2 policy compatibility path authorizes only its implicit homogeneous
portfolio. **ACP-1 Fixed Mixed Portfolio** adds explicit Policy V3 portfolio
binding and the frozen equal-budget specialist allocation:

```text
temporal_v1 + control_v1 + dataflow_v1
```

`configs/blue/fixed_mixed_portfolio_v1.json` binds all three lens slots plus
the lens registry, router, allocator, selector, and semantic-signature
provider. `configs/evolution/round_acp1_fixed_mixed_v1.json` selects
`candidate_portfolio_v1` as the formal arena blue authority. Under that
authority, `BLUE_CHALLENGE` requires Grounded validity with a hash-bound
`FailureDescriptor`, rejects any active policy other than the exactly matching
Policy V3, and emits a complete BlueEvaluation V2 for every challenge seed.
The formal path does not call the legacy opaque `evaluate_blue()` method.

The deterministic comparison control remains `generic_v1 × 3`, with the same
three-call candidate budget. These fixtures establish protocol, replay, and
authority conformance only; they do not measure lens collapse, unique solves,
or repair gain.

Run the explicit model-free ACP-1 fixed-mixed fixture with:

```bash
python -m r3e.blue.portfolio.fake_system \
  --project-root . \
  --portfolio configs/blue/fixed_mixed_portfolio_v1.json \
  --out /tmp/r3e-acp1-blue-evaluation.json
```

**ACP-2 Descriptor-Routed Portfolio** freezes a descriptor-only router in
`configs/blue/descriptor_router_v1.json`. Its three-slot allocation is always:

```text
generic_v1 + primary specialist + orthogonal specialist
```

The router scores only current Grounded FailureDescriptor V1 fields. It cannot
read mutation family/operator, red truth, history, a reference patch, or model
judgment. Equal scores use the frozen `temporal → control → dataflow` order.
Every decision emits a reconstructable router receipt containing the matched
rules, specialist scores, primary/orthogonal lenses, and allocated slots.
`configs/blue/descriptor_routed_portfolio_v1.json` authorizes the four-lens
pool, while each AllocationPlan and BlueEvaluation binds the case-specific
three-slot receipt.

`configs/evolution/round_acp2_descriptor_routed_v1.json` connects the router
to formal arena challenge, run toolchain, checkpoint/event hashes, and offline
audit. `portfolio_conditioned_neighbors()` also provides the frozen
`fixed_to_descriptor_routed` Policy V3 child operation without changing the
parent's scalar repair configuration, budget, or model route.

Run the model-free ACP-2 fixture with:

```bash
python -m r3e.blue.portfolio.fake_system \
  --project-root . \
  --portfolio configs/blue/descriptor_routed_portfolio_v1.json \
  --router configs/blue/descriptor_router_v1.json \
  --out /tmp/r3e-acp2-blue-evaluation.json
```

**ACP-3 Semantic Patch Diversity** adds a runner-owned structured semantic
signature provider and freezes `r3e-semantic-patch-signature-v1`. The provider
validates the patch scope against the active policy and derives the normalized
AST patch hash; the candidate verifier can only reference that runner-owned
signature and cannot self-report semantic authority.

Every three-candidate run emits deterministic `semantic_duplicate`,
`near_duplicate`, or `orthogonal` pair receipts plus candidate success,
unique/co-solve lens, lens-collapse, semantic-diversity, and portfolio-cost
statistics. Formal challenge aggregates the same metrics across seeds, while
offline audit reconstructs every receipt and the run toolchain binds the
semantic provider hash. ACP-3 V1 is measurement-only: duplicate candidates are
still verified, `retry_calls` is always zero, and no additional provider call
is allowed.

Run the model-free ACP-3 fixture with:

```bash
python -m r3e.blue.portfolio.fake_system \
  --project-root . \
  --portfolio configs/blue/semantic_diversity_portfolio_v1.json \
  --router configs/blue/descriptor_router_v1.json \
  --out /tmp/r3e-acp3-blue-evaluation.json
```

**ACP-4 Offline Adaptive Allocator** consumes only a frozen adaptation
manifest from the preceding round. It groups Grounded descriptors without
signal identity or artifact hashes, accumulates per-lens compile/oracle,
unique/co-solve, semantic-duplicate, Token, wall-time, and pair-overlap
statistics, and freezes them as `r3e-offline-allocator-state-v1`. Target and
non-target manifests are rejected as allocator inputs.

The allocator uses integer-only utility arithmetic and exhaustively scores all
legal three-slot combinations. V1 always preserves a general slot, limits a
specialist to two slots, keeps the model route and total call/Token budgets
unchanged, and emits a reconstructable receipt. The state hash is part of the
Policy V3 portfolio binding and formal arena toolchain; it cannot update during
a round. `descriptor_routed_to_adaptive` proposes exactly one allocator child,
which remains subject to the existing paired-replay and atomic-promotion
protocol.

The checked-in allocator state is deterministic synthetic conformance data,
not an experiment result. Run the ACP-4 fixture with:

```bash
python -m r3e.blue.portfolio.fake_system \
  --project-root . \
  --portfolio configs/blue/adaptive_portfolio_v1.json \
  --allocator-state configs/blue/offline_allocator_state_v1.json \
  --out /tmp/r3e-acp4-blue-evaluation.json
```

**ACP-5 RAAM Portfolio Control** permits an `active_dormant` ControlMemory to
select one template from the exact registry frozen by the active Policy V3.
The memory stores only the template ID and three bounded controls: specialist
slot budget, zero diversity-retry budget, and the frozen early-stop mode. It
cannot carry lens text, redefine a lens, change the model route or selector,
increase candidate budget, or add a provider call.

Qualification is paired and partitioned into adaptation repair gain,
non-target safety, and false-activation safety. Formal execution binds
`ExecutionPlan`, template registry/template hashes, a runner-owned control
receipt, `AllocationPlan`, BlueEvaluation V2, event/checkpoint toolchain, and
offline audit. The checked-in template registry and deterministic tests are
protocol fixtures only; they are not stored runtime memory or experimental
qualification evidence.

**ACP-6 Portfolio-Aware Red Challenge** exposes only a hash-bound, sanitized
coverage packet derived from prior challenges executed under the same
effective portfolio. It contains descriptor-cluster coverage, aggregate
portfolio success/collapse/duplicate/cost signals, and no prompt, candidate
patch, provider/verifier receipt, successful-lens identity, private RAAM
evidence, reference repair, hidden target, or model route.

The formal red path supports four deterministic, policy-bound operators:
`portfolio_bypass`, `router_ambiguity`, `specialist_deepening`, and
`portfolio_conflict`. Every generated poison binds the active policy and
effective portfolio, capability-packet hash, target region, operator plan,
materialized stress semantics, poison payload hash, and the composite
toolchain authority. The generation checkpoint freezes a portfolio-red
authority record, and offline round audit reconstructs it before accepting the
round. Cross-round coverage may be reused under a new policy only when the
effective portfolio hash is unchanged.

These deterministic fixtures do not establish router accuracy, held-out
generalization, real-model lens collapse, unique solves, repair gain, adaptive
allocation benefit, diversity-recovery benefit, or red challenge
effectiveness. Real candidate/red providers and empirical ACP results remain
open.

### Grounded Red Discovery

The red-team V2 engineering path is represented publicly by its frozen
milestone configurations and executable protocol tests. Its first checkpoint,
**Grounded Red Protocol Foundation V1**, introduces frozen
family/operator/effect registries,
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

The Whole-Policy arena first introduced `grounded_red_execution_v1` as a
runner-owned Icarus validity authority. The current formal interface is
`grounded_runtime_authority_v1`: for every admitted candidate it additionally
requires a Yosys clean-proof/poison-counterexample/revert-proof triplet bound
to the exact materialized RTL and frozen property. It then derives the RAAM
`FailureDescriptor` only from verified execution/formal artifact hashes and
stores that exact descriptor in the corresponding `VerifiedEpisode`. Offline
audit reconstructs the authority bundle, episode manifest, and episode-store
object. The model adapter never receives validity or descriptor authority.
The historical adapter evidence path remains explicit as
`legacy_adapter_evidence_v1`. The initial integration is frozen as
**Grounded Authority Integration V1** in
`configs/evolution/grounded_authority_integration_v1.json`; the formal/RAAM
closure is frozen as **Grounded Runtime Authority Closure V1** in
`configs/evolution/grounded_runtime_authority_closure_v1.json`. The bounded
waveform and formal-rejection child is frozen as **Grounded Waveform Rejection
V1** in `configs/evolution/grounded_waveform_rejection_v1.json`.
The next model-free checkpoint, **Grounded Planner & RAAM Cross-Round Closure
V1**, is frozen in
`configs/evolution/grounded_planner_raam_cross_round_v1.json`. Its round
interface is `configs/evolution/round_grounded_runtime_v3.json`.

Its later **Grounded Proposal Authority V1** child moves coverage and
curriculum decisions in front of red generation. Before calling an adapter,
the runner freezes a sanitized archive view and deterministic proposal intents
that bind family, AST operator, runtime effect, specialist role, difficulty
target, lineage class, archive quota, and dispatch kind. The adapter must
return exactly one candidate for each selected intent and cannot widen those
fields. Proposal plan/execution hashes are bound into Red Search Context V6,
the round toolchain, RED_GENERATE checkpoint/event, and offline audit. This is
deterministic planning authority, not evidence of real-model proposal quality
or bug yield.

The current child protocol records the first divergent signal and cycle,
cycle offset, temporal relation, assignment class, cone-depth bucket, and
mismatch pattern from two deterministic poison simulations. The oracle parser
binds those values to provider receipts before RAAM constructs a descriptor.
Yosys emits a complete formal assessment before a triplet is accepted. A fully
executed candidate that does not satisfy F1 clean proof, F2 poison
counterexample, or F3 exact-revert proof is written to the append-only
`red_rejected_archive.jsonl`; it does not reach blue challenge, residual
selection, memory candidate construction, or promotion. It is also represented
as an `inconclusive` `VerifiedEpisode`, with no blue attempts, so the rejected
trajectory remains in the authority DAG without acquiring memory or promotion
rights. Missing tools, tool failures, malformed receipts, and tampered evidence
still fail the round closed.

For Grounded rounds, `RED_GENERATE` freezes a deterministic coverage plan
against the current policy and persistent coverage-state hash. `ARCHIVE_UPDATE`
derives difficulty only from admitted execution receipts and commits the next
coverage state. Both transitions are checkpointed, evented, and reconstructed
by offline round audit. The parser-backed RAAM execution layer supports
`memory_bypass`, `memory_deepening`, `memory_conflict`, and `memory_transfer`.
Adapters select bounded recipes; only the frozen AST materializers may edit RTL.
Controlled composition binds exactly two parent authority hashes, admits each
intermediate AST state, and proves an exact reverse restoration.

`r3e.memory.authority_dag` verifies every persisted episode, evidence link,
memory definition/object, qualification decision, lifecycle transition, bank,
and active policy edge. The deterministic four-round conformance test proves
that an early memory can be retrieved later, is suspended when a relevant
policy dependency changes, and becomes executable again only after explicitly
bound paired revalidation.

The fake fixture remains model-free and is not grounded evidence. The
operator library intentionally supports a constrained parser-backed
SystemVerilog subset rather than claiming complete language coverage. Formal
triplet receipts remain mandatory for admitted Grounded Runtime authority. A
verified, completed but proof-unsatisfied assessment is a rejected candidate,
while missing, tool-failed, or tampered formal evidence fails closed.
Descriptors encode bounded provider-observable categories and artifact hashes,
not a claim of complete waveform understanding. No real-model discovery run,
bug-yield result, or empirical gain is claimed. The legacy
`r3e.red.validity_gate` remains available for historical Whole-Policy
compatibility, but is not Grounded Red admission authority.

### Publication boundary

The Git repository excludes `docs/` and local experiment data by policy.
Generated runs belong under ignored `runtime/`, `results/`, `artifacts/`, or
experiment-local `runtime/`, `results/`, `outputs/`, and `artifacts/`
directories. `scripts/check_release.py` fails if those paths, or common
experiment-result formats, are tracked.

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

### Sequential Grounded Red authority

Parser-backed RAAM operators (`memory_bypass`, `memory_deepening`,
`memory_conflict`, and `memory_transfer`) and two-parent controlled
composition use a runner-owned sequential authority. The adapter may select a
hash-bound plan and output path, but it may not author the RTL. The runner:

1. reconstructs every AST edit and intermediate parser admission;
2. materializes the final poison and verifies exact reverse restoration;
3. runs a clean baseline, two deterministic poison simulations, and the
   restored baseline through Icarus and the stdout oracle;
4. admits only a stable functional mismatch with complete provider receipts;
5. binds the same clean/poison/revert hashes into the Yosys proof triplet and
   the failure descriptor.

The frozen execution schema is
`r3e-grounded-sequential-execution-bundle-v1`. It coexists with the single-AST
execution schema so historical rounds remain reconstructable. A failed
compile, simulation, oracle, intermediate admission, inverse, or formal gate
cannot obtain arena authority.

### Deterministic Red Population

GRD-6 adds a deterministic scheduling authority between the frozen proposal
plan and candidate generation. Each selected intent is assigned to exactly one
generalist, family-specialist, coverage, hardness, memory, or composition
generator lane with an immutable model/toolchain/budget envelope. Candidate
usage and provider provenance are checked against the assignment before
validity begins.

Generators cannot return validity, minimization, oracle, or formal decisions.
AST execution, structural minimization, Icarus/Yosys validation, and admission
remain runner-owned. `generalist_only` and `routed_population` schedules retain
the same proposal-plan hash, making the routing factor independently
auditable. The deterministic providers are protocol fixtures and do not
constitute evidence of real multi-model population gain.

### Integrated deterministic coevolution

`Integrated Deterministic Coevolution V1` serializes Arena and RAAM under one
macro-round promotion epoch. A round pre-authorizes exactly one of `collect`,
`memory`, or `policy`; memory and whole-policy search therefore cannot both
commit a child in the same macro round. The renewed challenge binds the final
active policy recorded by that round's frozen registry snapshot.

```bash
python -m r3e.arena.integrated_fake_system \
  --workspace /tmp/r3e-integrated \
  --rounds 4
```

The expected schedule is `collect → memory → collect → policy`, with promotion
counts `0, 1, 0, 1`. This proves scheduler, persistence, resume, cross-round
memory binding, and audit behavior only; it is not a model experiment.

### Real adapter pilot entry

The real-provider boundary now has two proposal-only adapters. Grounded Red
may select one node from a runner-enumerated parser AST set but cannot change
the frozen family, operator, effect, validator, or budget. ACP receives only
the current buggy RTL and observable failure descriptor. Golden RTL,
testbenches, Yosys elaboration, Icarus differential simulation, oracle
judgement, selection, archive, and promotion remain runner-owned.

The transport is one-call, strict-JSON, and OpenAI-compatible. It uses the
configured Python client when available and a standard-library HTTP fallback
otherwise; neither path retries. Credentials and endpoint values remain in
environment variables and never enter receipts.

The pilot entry resolves `OPENAI_MODEL`/`OPENAI_API_KEY`/`OPENAI_BASE_URL`
first, then accepts the equivalent `DEEPSEEK_MODEL`/`DEEPSEEK_API_KEY`/
`DEEPSEEK_BASE_URL` names (with `LLM_MODEL` as a model fallback). Only the
selected environment-variable names are bound into the client configuration;
secret values are never persisted.

An injected-transport smoke test exercises exactly four calls: one GRD target
choice and three ACP candidates. It proves the local authority pipeline,
including the GRD clean/poison/revert formal triplet and ACP verifier-guided
selection, but it is not model evidence:

```bash
pytest -q -p no:cacheprovider tests/test_grd8_acp7_pilot_smoke.py
```

The frozen **Shadow Pilot Matrix V1** is the next call-matched boundary: for
one smoke case and one seed it runs the four ACP arms A/B/C/D with three
candidates each (12 ACP calls), plus one manifest-backed Grounded Red target
choice (1 call). The Red request is bound to
`configs/pilot/real_integrated_shadow_manifest_v1.jsonl`; parser-node choice,
materialization, Icarus/Yosys formal triplet, and all promotion/qualification
decisions remain runner-owned. Resume must not add calls, and both promotion
and RAAM qualification remain disabled.

```bash
pytest -q -p no:cacheprovider tests/test_shadow_pilot_runner.py
```

The combined admission wrapper is available as:

```bash
python -m r3e.pilot.safe_shadow_admission \
  --workspace runtime/pilots/shadow-admission-v1
```

The safe entrypoint writes a terminal, privacy-safe failure checkpoint when a
provider request or runner hard gate fails. A failed workspace cannot be
resumed into another provider request; start a new authorized workspace after
fixing the binding or budget.

Eight separately authorized real shadow admissions have been executed under
this boundary. The first completed 12 ACP calls and stopped at the old Red
output ceiling; the second completed 12 ACP calls plus one Red request that
returned empty content; the third stopped on empty Blue content at call 9; the
fourth completed 12 ACP calls plus one Red request that returned empty content;
the fifth stopped on empty Blue content at call 9 with a length-finish
diagnostic; the sixth completed 12 ACP calls plus one Red request before its
empty-content gate; and the seventh stopped on empty Blue content at call 9
with `finish_reason=length` and `output_tokens=4096`. The eighth completed the
full 12+1 budget: Grounded Red was formally admitted with a clean/poison/revert
triplet, the poison entered the runner-owned ACP path, and the archive,
`VerifiedEpisode`, and zero-call resume checkpoints were reconstructed. All
eight runs kept policy promotion and RAAM qualification disabled. These runs
validate call accounting and fail-closed handling only; they are not ACP-7
gain, GRD-8 yield, or multi-round coevolution evidence. Empty responses are
recorded only with categorical, privacy-safe envelope diagnostics; retries
require a new authorization, workspace, and explicit budget.

The real Grounded shadow profile reserves the frozen 60-second wall-time
ceiling even for a D0 proposal. Legacy deterministic population profiles keep
their difficulty-derived budgets.

It records the expected 12+1 provider budget and keeps all detailed receipts
under the ignored `runtime/` tree. The checked-in test uses an injected
transport and is the reproducible no-network conformance path:

```bash
pytest -q -p no:cacheprovider tests/test_shadow_admission.py
```

With an explicitly authorized third-party endpoint, a one-case real-provider
smoke can be launched with:

```bash
python -m r3e.pilot.grd8_acp7_smoke \
  --project-root . \
  --workspace runtime/pilots/grd8-acp7-smoke \
  --manifest datasets/manifests/strider14.jsonl \
  --case-id strider:mux_4_1_1 \
  --seed 17
```

This sends the small public GRD pilot RTL and the current public buggy RTL,
observable evidence, and prompt-lens instruction to the configured provider.
It does not send golden RTL or the testbench. Detailed outputs stay under the
ignored `runtime/` tree.

`configs/evolution/grd8_acp7_pilot_v1.json` remains intentionally
non-executable because the comparative pilot still requires frozen target and
non-target manifests, the eight-family set, three run seeds, promotion
thresholds, and experiment budgets. No third-party model result, GRD-8 yield,
ACP-7 gain, multi-round result, or promotion evidence is included in this
repository.

Before requesting a real-provider shadow authorization, run the secret-free
readiness gate. It performs no provider call and returns exit status `2` while
the private shadow bindings are incomplete:

```bash
python -m r3e.pilot.readiness \
  --stage shadow-admission \
  --project-root .
```

The full multi-round pilot has a separate, stricter gate:

```bash
python -m r3e.pilot.readiness \
  --stage pilot \
  --project-root . \
  --config configs/evolution/grd8_acp7_pilot_v1.json
```

### Real Integrated Coevolution Shadow V1

The next engineering lane connects the real provider boundary to the formal
Arena data path without enabling evolution. `RealGroundedArenaAdapter` consumes
an eligible manifest row, enumerates parser-backed AST nodes, and accepts only
the provider's target-choice receipt. The runner then owns AST materialization,
Icarus/Yosys admission, FailureDescriptor construction, ACP three-slot
verification/selection, VerifiedEpisode creation, archive updates, and the
checkpoint/event/audit DAG. `promotion_enabled` and
`memory_qualification_enabled` are hard-disabled in this lane.

The scope is frozen by
`configs/evolution/real_integrated_coevolution_shadow_v1.json`. Its parent
milestone, frozen source assets, claim boundary, and disabled promotion controls
are checked by `tests/test_real_integrated_shadow_milestone.py`; the milestone
does not include runtime artifacts or provider credentials.

The deterministic integration fixture is runnable without network access:

```bash
pytest -q -p no:cacheprovider \
  tests/test_real_integrated_shadow_milestone.py \
  tests/test_real_integrated_shadow.py
```

The test uses an injected OpenAI-compatible transport (one Grounded choice and
three ACP calls), proves the formal poison triplet, and resumes the same round
without another provider call. It is protocol/shadow evidence only; it does
not claim real-model repair gain, multi-round coevolution, or promotion.

### Policy-only promotion rehearsal

After shadow admission, the deterministic rehearsal freezes the smallest
policy-transition unit in
`configs/evolution/policy_promotion_rehearsal_v1.json`: two rounds (`R000`,
`R001`), one challenge seed, at least two residual designs, one independent
non-target design, fixed fake model/toolchain/budgets, and a single
`collect → policy` lane. The second round performs exactly one runner-owned
atomic policy promotion and renewed challenge; RAAM promotion and memory
qualification remain disabled.

```bash
python -m r3e.pilot.policy_promotion_rehearsal \
  --workspace runtime/pilots/policy-promotion-rehearsal-v1
pytest -q -p no:cacheprovider tests/test_policy_promotion_rehearsal.py
```

This is a deterministic interface/recovery rehearsal, not evidence from a
real provider and not a multi-seed experiment.

Before enabling a real policy-transition rehearsal, run the read-only
promotion gate with a separately frozen binding:

```bash
python -m r3e.pilot.promotion_readiness \
  --shadow-workspace /path/to/real-shadow-admission \
  --binding /path/to/real-policy-rehearsal-binding.json
```

The gate rejects terminal/incomplete shadow workspaces and requires a
reconstructable 12+1 admission, an admitted clean/poison/revert formal
triplet, two residual designs, an independent non-target manifest, fixed
one-seed/two-round policy-only controls, and unchanged registry snapshots. It
does not call a provider or promote a policy.

### Deterministic B0→B1→B2 pilot scaffold

The next engineering fixture freezes the shape of the first real dynamic
experiment in `configs/evolution/b0_b1_b2_pilot_v1.json`: three isolated
repetitions (seeds 17/29/43), four target designs, eight declared family
slots, design-disjoint non-target and held-out manifests, and the lane
schedule `collect → policy → policy`. Each repetition must produce exactly
`0, 1, 1` policy promotions, bind every renewed challenge to the preceding
active hash, and pass a two-step exact-parent rollback probe. Memory
qualification and promotion remain disabled.

Run the model-free scaffold with:

```bash
python -m r3e.pilot.b0_b1_b2_pilot \
  --workspace runtime/pilots/b0-b1-b2-v1
pytest -q -p no:cacheprovider tests/test_b0_b1_b2_pilot.py
```

This freezes the state machine, split isolation, resume, audit and rollback
interfaces only. The fake adapter does not provide real-model yield, policy
gain, held-out generalization, or evidence for the eventual B0→B1→B2
experiment.

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
