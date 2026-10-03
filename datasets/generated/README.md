# Generated carriers (`corpus_v1`)

151 clean designs with generated testbenches, the design pool of the closed red–blue loop (`r3e/loop/`). They contain no bugs: bugs are made by Red during a run.

| Source | Designs | Upstream commit | License |
|---|---:|---|---|
| VerilogEval v2 (`NVlabs/verilog-eval`) | 113 | `c498220d0a52248f8e3fdffe279075215bde2da6` | MIT (`licenses/VerilogEval-MIT.txt`) |
| RTLLM v2 (`hkust-zhiyao/RTLLM`) | 38 | `51ed553d0ffd32797a1a0a13e051656bf302c81f` | MIT (`licenses/RTLLM-MIT.txt`) |

Each design directory holds:
- `clean.sv`: the upstream reference design (only the top module may be renamed);
- `tb_visible.v`: a random-stimulus testbench (seed 11, 64 cycles). It writes the outputs per cycle to `trace_visible.txt` and the applied inputs to `stimulus_visible.txt`.
- `tb_hidden.v`: a random-stimulus testbench (seed 97, 160 cycles), outputs only.

Expected outputs are not stored: the runner obtains them by simulating `clean.sv`.

A design is admitted by `r3e/loop/carriers.py::build_carrier` only if:
- its ports are readable;
- the clean design passes both testbenches deterministically;
- no output is X/Z or constant;
- it offers at least three mutation sites.

`carriers.jsonl` records, per carrier: its id, cluster, ports, clock and reset, the repository-relative paths and SHA-256 of each file, and the upstream source.
