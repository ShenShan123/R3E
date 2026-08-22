# 4-minute demo script

1. **0:00–0:30 — Problem**: explain that RTL can compile while its functional
   behavior is wrong, making waveform inspection and regression expensive.
2. **0:30–0:55 — System**: show `Diagnosis → Repair → EDA Verification →
   Evidence`.
3. **0:55–1:55 — Counter**: load `demo_counter`, show the temporal mismatch,
   compare three candidates, and show the oracle accepting only the verified
   candidate.
4. **1:55–2:45 — FSM / Shift**: show a control defect or dataflow defect and
   the corresponding specialist lens.
5. **2:45–3:25 — Correctness Gate**: emphasize “model proposes, EDA decides”
   and show Reject versus Accept.
6. **3:25–3:50 — Evidence**: show case hashes, toolchain, stage receipts and
   the guided-replay label. Do not call this a model experiment.
7. **3:50–4:10 — Application**: connect the workflow to RTL debugging,
   verification assistance, EDA and digital-design education.
