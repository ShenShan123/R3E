# R³E: Red-Team-Guided Evolution for Correctness-Gated RTL Repair

Anonymous research-code release for R³E. This upload package contains the core
implementation, frozen public benchmark inputs, offline audit/aggregation
utilities, and protocol tests.

It intentionally excludes experiment launchers and runners, generated results,
model responses, repair candidates, runtime memory/registry contents,
credentials, internal documents, machine-specific paths, and third-party tool
installations.

## Layout

- `r3e/semantic_repair_bench/`: functional RTL repair, correctness gates,
  red-team mutation and critique, policy memory, strategy distillation,
  promotion, rollback, and deterministic repair primitives.
- `r3e/microsurgeon_frontend/`: syntax and semantic repair components.
- `r3e/microsurgeon_flow/`: backend repair, skill routing, and guarded
  preflight components.
- `r3e/tools/`: synthesis, timing, and structural-analysis adapters.
- `experiments/`: experiment-design components plus offline aggregate, audit,
  and analysis utilities. No batch runner or launcher is included.
- `configs/`: a sanitized frozen policy definition; numerical promotion
  evidence and runtime registry history are excluded.
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

## Strategy-memory scope

The released strategy-memory implementation is shadow-first and fail-closed:

1. only trajectories with rebuildable outcomes and complete provenance enter
   shadow storage;
2. distilled strategies remain inactive until frozen target replay and
   disjoint non-target regression checks pass;
3. promotion binds source trajectories, parent policy hash, validation
   evidence, rollback state, and an atomic decision ledger;
4. runtime loads active strategies only and records strategy IDs and policy
   hashes at use time.

The mechanism supports bounded, correctness-gated red-team-guided policy
revision. It does not claim unrestricted or open-ended autonomous evolution.

## Release boundary

The public datasets retain their upstream licenses and copyright notices. Code
in this repository is released under Apache-2.0. No historical Git metadata is
required for the package.
