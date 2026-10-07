# ChipBench debugging conversion

Run from the repository root:

```bash
python scripts/convert_chipbench.py
```

The standard-library conversion script reads the local upstream checkout at
`datasets/external/chipbench/`. It selects only the four
`dataset_debug_zero_shot_*` directories: assignment (30), timing (29),
arithmetic (24), and state_machine (6). One-shot prompts are never selected.
The raw checkout remains untouched.

Each case is written under `datasets/chipbench/<bug_type>/<problem>/`:

- `buggy.sv`: the first fenced block following the prompt's “has bug” line;
  extraction checks that it declares `TopModule`.
- `golden.sv`: the reference with only the `RefModule` declaration renamed to
  `TopModule`. Helper modules and all other source bytes are preserved.
- `ref.sv`: a byte-for-byte copy of the upstream reference.
- `tb_visible.sv`: the upstream testbench with visible output and input logging.
- `spec.txt`: the natural-language specification preceding the prompt's
  “the code below ... has bug” line, with implementation snippets sanitized.

The MIT license is copied unchanged to `licenses/ChipBench-MIT.txt`.
The manifest is `manifests/chipbench89.jsonl`, containing **all 89 inputs**.
Its paths are repository-relative, `top_module` is `TopModule`, and `tb_sources`
orders `ref.sv` before `tb_visible.sv`. Case IDs include the bug type. The
`design` value is `chipbench_<problem>__<bug_type>`, so the existing loader
groups all variants of a design into the same split family.
All rows explicitly declare `eligible`. The 21 cases with unusable references
have `eligible: false` and a specific `ineligible_reason`; the existing loader
skips these rows. The remaining 68 rows have `eligible: true`, including
timing/Prob016, whose buggy compilation failure is a valid repair task.

## Specification-bearing tasks

Every row has a repository-relative `spec` path to its case's `spec.txt`,
including the 21 excluded rows. The repair task definition is now
**specification + buggy RTL + test evidence**. The converter does not infer
requirements from golden RTL or correct inconsistencies in the original
natural-language description. Target patterns, FSM descriptions, port names,
numeric literals, and ordinary prose are preserved.

The buggy-code boundary and specification boundary use the same unique marker,
so neither the buggy RTL nor the repair instruction enters the specification.
The script removes fenced implementation blocks before that boundary. In the
current snapshot, seven prompts contain provided helper modules; all seven
belong to already excluded FIFO/submodule cases. Three eligible Prob017
frequency-divider prompts also contain four numeric `parameter` declarations
each. These become prose entries carrying the exact parameter name, literal,
and comment, without executable declarations. All other text is preserved
apart from leading/trailing whitespace.

Code fences, recognizable remaining HDL, unclosed fences, and empty
specifications cause conversion to fail before outputs are written. Inline
identifiers and numeric literals are retained as specification content. The
check targets HDL text in this dataset, not arbitrary programming languages.
`spec_audit` in the conversion report records source line ranges and hashes for
each removed block or parameter conversion. Specification files also appear
in the generated-file hashes, and validation checks all 89 specs separately
from the 68 eligible simulator cases.

The optional-spec loader and Blue request integration are maintained by the
runtime owner. This conversion change does not add specifications to CirFix
or Red-generated tasks. Experiments using these specs are a different task
setting from earlier ChipBench runs without specs; record the new manifest
and specification hashes when comparing runs.

## Trace adaptation

The supplied brief's trace section was empty; this adapter follows
`r3e/knowledge/feedback.py` and `r3e/loop/sim.py`.
`trace_visible.txt` is CSV with a single `time,...` header. Only DUT output
signals are recorded. Scalar columns use their port names; bus columns use
one binary digit per declared bit, from the leftmost packed bit to the
rightmost, such as `q[3],q[2],q[1],q[0]`. Unknown and high-impedance bits
remain `x` and `z`. The runner regroups these columns into buses.

Sampling runs on both edges of `tb.clk`, matching the upstream checker
cadence, using `$fstrobe` in the postponed region after nonblocking updates
settle. This also retains the upstream checker cadence for multi-clock FIFO
designs. No random calls or stimulus changes are added. Simulator exit
flushes the trace; a `final` block does not close a pending strobe early.

The testbench also writes `stimulation_visible.txt`, and an identical
`stimulus_visible.txt` for compatibility with the runner's `stimulus_` file
discovery. Each contains a `#` note describing sampling, a
`time,<input ports>` CSV header, and one binary string per input port per row.
Bus values retain their full width. Clocks and resets are included. Named
and shorthand DUT connections are resolved against concrete testbench signal
declarations rather than potentially invalid buggy RTL.

Input and output logs use the same postponed-region sampling events and
timestamps. The values describe inputs at that sampling instant, after NBA
updates, and preserve all original stimuli. Both golden runs must produce
identical output and input logs; functional buggy runs must use the same
input log as golden. The R3E runner reads the compatible filename and includes
the input values in its functional mismatch evidence windows.

The original `$dumpfile` and `$dumpvars` diagnostic calls are disabled.
Their forward reference to `tb_mismatch`, combined with
`default_nettype none`, fails elaboration on the installed Icarus 13.
The input generation, self-checker, timeout, and finish logic are preserved.

## Validation and current limitations

Passing the native reference self-check is **not** proof of agreement with the
specification. Ten rows from three design families are excluded for demonstrated
semantic conflicts, in addition to the original eleven unusable references.
`tests/test_chipbench_conversion.py` contains executable witnesses for all ten
upstream references. The accumulation witness produces a valid sum of 36 for
5+6+7+8 after a previous group sum of 10; the correct new sum is 26. The traffic
witness observes red=0 during reset. The serial witness accepts five one bits,
pauses, and observes valid_b=1 with data_b=0 before the sixth bit arrives.
All source RTL, specifications, and testbench stimuli remain unchanged.

The two Prob005 signal-generator rows remain eligible with a review limitation:
the source specification does not determine amplitude, period, reset phase or
mode-switch phase. Reference simulation shows square/sawtooth amplitudes of 20,
a 20-clock square period, and triangular reset startup 0,31,30,... before a
steady 0..20 triangle. This is underspecification/startup ambiguity rather than
a demonstrated conflict with an explicit numeric requirement. Do not infer that
"5-bit" by itself requires amplitude 31. Review each candidate's recoverability
from its remaining RTL, specification and visible evidence before counting a hit;
these two rows have not received blanket semantic certification.

Validation runs by default. It compiles the design, then `ref.sv`, then
`tb_visible.sv` with `iverilog -g2012`, and executes `vvp` in temporary
directories for the 68 eligible cases. It checks native golden mismatches
equal zero, functional buggy mismatches exceed zero, well-formed nonempty
binary CSV traces, row-aligned input logs, identical compatibility aliases,
and two identical golden runs. For timing/Prob016, buggy compilation must
fail and the actual runner must report `compile_fail`; the golden design
still passes and supplies a validated input log.

Validation also verifies that the actual R3E loader loads exactly the eligible
cases, checks family-level splits, and obtains actual `Simulator` verdicts.
It confirms all 68 golden input logs reach the runner's stimulus cache and
all 67 functional failures expose input values in their evidence windows.
The compile failure supplies compiler feedback until a candidate compiles,
so it does not have a functional mismatch window at this stage.

The full report at `artifacts/chipbench_conversion/validation.json` contains
per-case failures, compiler diagnostics, mismatch counts, trace hashes,
runner verdicts, input-log rows/hashes, and SHA-256 hashes of all selected
source/generated files, plus specification sanitation records. Excluded cases record their reasons without being
simulated. Runtime CSV files are produced in each simulator work directory;
they are not added to the versioned dataset inputs.
It is a local validation artifact, excluded by the existing ignore rules.

The updated manifest contains **89 rows: 68 eligible, 21 ineligible**.
The 68 eligible tasks consist of 67 functional repairs and one compile repair:

| Bug type | Eligible / total |
|---|---:|
| assignment | 24 / 30 |
| timing | 22 / 29 |
| arithmetic | 17 / 24 |
| state_machine | 5 / 6 |

The 21 ineligible cases remain present in the manifest:

| Cases | Reason |
|---|---|
| Prob013, assignment/arithmetic/timing | The native XOR checker counts identical `Z` outputs as mismatches. Golden trace determinism and R3E verdicts are checked separately. Timing also assigns a declared wire procedurally and fails compilation. |
| Prob021 and Prob022, assignment/arithmetic | Golden references instantiate `dual_port_RAM` without defining it. |
| Prob021 and Prob022, timing | Renaming the reference top leaves duplicate `dual_port_RAM` declarations across golden and reference files. |
| Prob018, timing | Golden reference lacks `slave_mod`. |
| Prob019, arithmetic | Golden reference lacks `decoder_38`. |
| Prob007, assignment/timing/arithmetic | Under backpressure the reference carries the previous sum into a new four-input group, contradicting the specified four-input sum. |
| Prob033, assignment/timing/arithmetic/state_machine | Reset turns every lamp off; the specification explicitly requires reset to red. |
| Prob006, assignment/timing/arithmetic | Pausing after five accepted serial bits asserts `valid_b` with stale data, although six bits are required. |

Timing/Prob016 remains eligible: buggy RTL refers to an undeclared `rst_n`,
while its golden reference compiles and passes. This is a compile repair,
not grounds for reference-based exclusion.

Strict source preservation was requested for these exceptions. The script
does not repair buggy RTL, synthesize missing helpers, reorganize reference
helpers, alter the native checker, or silently drop failures. Consequently,
**the eligible pool contains 68 repair tasks, rather than 89 functional
bugs**. Consult the report before a baseline or curriculum run.
`bug_type` is manifest metadata; no type label is injected into RTL,
traces, or Blue prompts.

Exit status is `0` when all selected eligible cases qualify (or extraction-only mode
was explicitly selected), `1` when validation finds any failed gates, and
`2` for conversion/input/tool errors. Excluded rows do not count as validation
failures. Existing non-identical generated files
are rejected before any outputs are written; `--overwrite` explicitly
allows regeneration. Identical reruns leave dataset bytes unchanged.

For a quick first-case check, configurable locations, or extraction only:

```bash
python scripts/convert_chipbench.py --limit 3
python scripts/convert_chipbench.py --source-root /path/to/chipbench --repo-root /path/to/output
python scripts/convert_chipbench.py --skip-validation
python -m pytest -q tests/test_chipbench_conversion.py
```

`--limit` uses `chipbench_subset.jsonl` and `validation_subset.json`, so it
does not replace the full manifest or report. `--timeout` configures the
native process limit; R3E runner checks retain the runner's own timeout.
`--report` selects a different validation report path. Extraction-only
reports explicitly record `validated: false`.

The existing `scripts/verify_datasets.py` covers the four preexisting frozen
sets. ChipBench qualification and identity checks are performed by this
converter and its report; those older manifests and hashes are unchanged.
