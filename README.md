# R3E: Verifier-Mediated Red–Blue Policy Evolution for RTL Repair

R3E studies whether an LLM repair agent (Blue) improves from experience
gathered while an adversary (Red) probes its weaknesses. Neither side
changes model weights. Each side's strategy is an external memory that
the loop rewrites from verified outcomes:

- **Blue** repairs buggy RTL using visible test evidence. Its memory is
  case records of its own verified repairs, stored as causal chains
  (observed failure, fault, repair, what did not work, verification). Memory
  is offered as a reference only after Blue's own first attempt fails.
  Cases are activated only by paired qualification on other designs.
- **Red** proposes bugs and keeps an experience base of Blue's weak
  points. It decides whether to pursue a weak point on a new design (same,
  harder or simpler) or to explore.
- The runner only referees: admission checks, information boundaries,
  paired measurement and the call budget.

## Layout

| Path | Contents |
|---|---|
| `r3e/loop/` | The closed loop: Red, Blue, curriculum, qualification gate, mastery tests, evaluation, budget, CLI |
| `r3e/knowledge/` | Failure profiles, case memory, matching, delivery to Blue |
| `r3e/providers/`, `r3e/protocol/` | OpenAI-compatible JSON client; hashing and hash-chained ledgers |
| `datasets/` | Public bug benchmarks (CirFix, Literature-32, Strider, RTLFixer) and `generated/corpus_v1` (151 clean designs with testbenches) |
| `experiments/loop_probes/` | Diagnostic probes (transfer probe, match diagnosis, patch forensics) |
| `scripts/` | Dataset verification and release audit |
| `tests/` | Tests of the loop and the knowledge package |

## Requirements

- Python ≥ 3.11.
- `openai` and `pytest` (`requirements.txt`).
- Icarus Verilog (`iverilog`, `vvp`) and Yosys on `PATH`.

## Usage

```bash
# worst-case call budget of a run, with no model calls
python -m r3e.loop.cli --state-dir STATE --curriculum --rounds 4 --proposals 4 --plan

# plumbing check with scripted fake models
python -m r3e.loop.cli --state-dir STATE --curriculum --fake --rounds 2

# real run: needs explicit approval flags and a hard call cap
python -m r3e.loop.cli --state-dir STATE --curriculum --llm-env FILE \
    --allow-real-calls --max-calls 150 --rounds 4 --proposals 4
```

Credentials are read only from the `--llm-env` file (`export NAME=value`
lines). A run is resumable, and every model call is written to a
hash-chained `calls` ledger. `python -m r3e.loop.cli --help` lists the
options (memory order, cross-model checks, evaluation).

## Checks

```bash
python -m pytest
python scripts/verify_datasets.py
python scripts/check_release.py
```

## License

Code: see `LICENSE`. Datasets keep their upstream licenses (`datasets/licenses/`).
