# Upstream RTL bug datasets (raw downloads, not committed)

These datasets were downloaded as-is on 2026-10-02, for choosing test suites later. Nothing here is converted or used by the loop yet. Each keeps its upstream license. The contents are git-ignored; only this file is tracked.

| Directory | Source | Commit / version | Contents | License |
|---|---|---|---|---|
| `chipbench/` | https://github.com/zhongkaiyu/ChipBench | `b4ba3a0` | `Verilog Debugging/`: 89 cases (assignment, timing, arithmetic, state machine), each in zero-shot and one-shot form. Golden `*_ref.sv` and `*_test.sv`, VerilogEval-style, Icarus. | MIT (NVIDIA, inherited from VerilogEval) |
| `rtl-repair/` | https://github.com/ekiwi/rtl-repair (branch `asplos24`), sparse checkout of `benchmarks/fpga-debugging` | `71e1afc` | 14 real bugs in 11 projects from the ASPLOS'22 FPGA bug study. Golden RTL, plus CSV I/O traces as testbenches. | none in the repo; upstream project licenses apply (e.g. verilog-axis MIT, ZipCPU sdspi GPL) |
| `cvdp/hf/` | https://huggingface.co/datasets/nvidia/cvdp-benchmark-dataset | v1.1.0 `no_commercial` files | non-agentic (302 rows) and agentic (92 rows) code-generation sets, with the LICENSE and NOTICE | code Apache-2.0, non-code CC-BY-4.0 |
| `cvdp/cid016.jsonl` | extracted from `cvdp/hf/` | v1.1.0 | the 46 cid016 (debugging / bug-fix) rows: 35 non-agentic, 11 agentic | as above |
| `cvdp/harness/` | https://github.com/NVlabs/cvdp_benchmark | `8e894cf` | cocotb harness runner (Icarus via `SIM=icarus`) | see repository |
| `hwe-bench/` | https://github.com/pku-liang/hwe-bench | `10c78a8` | benchmark code and harness (repository-level, Docker) | Apache-2.0 |
| `hwe-bench-data/` | https://huggingface.co/datasets/henryen/hwe-bench | main | `hwe_bench_full.jsonl`: 417 real PR-fix tasks (OpenTitan 245, XiangShan 54, Ibex 35, CVA6 35, Rocket Chip 32, Caliptra 16). The Docker images (~200 GB) are not downloaded; OpenTitan tasks need VCS. | Apache-2.0 |
