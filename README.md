# R³E: Red-Team-Guided Evolution for Correctness-Gated RTL Repair

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
exact rollback, and a renewed challenge bound to the newly active policy.

Formal runtime reads only Policy Registry V2. A child must bind the exact
active parent hash, stale children cannot be promoted, and rollback restores
the exact pre-promotion registry snapshot.

> 当前仓库提供系统基础设施和协议实现，不代表已经获得真实模型驱动的多轮演化实验结果。

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

Every round writes hash-chained JSONL events under `runtime/events/`:
`policy.jsonl`, `red.jsonl`, `oracle.jsonl`, `arena.jsonl`, and
`rollback.jsonl`. Events are observability records and never grant authority.
The adapter supplies environment-specific generation and evaluation; the
runner retains all registry, manifest, split, promotion, and rollback
authority.

Completed rounds also contain `toolchain.json`, `red_search_context.json`,
`residual_selection.json`, and `round_audit.json`. Promotion decisions require
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
