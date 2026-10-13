# Prob003 development witness contract

Inputs are binary. Reset is active low and asynchronous. Data is stable before
each rising edge; it may change between edges. Every rising edge with reset high
consumes exactly one bit. From each reset, groups are six bits long with no idle
cycle between groups. Immediately after bit six, outputs classify that group:
match for 011100, otherwise not_match. Both are zero after the other five edges.
Reset discards a partial group and clears outputs. Outputs remain defined and
exclusive and do not depend on an unevaluated new data value between edges.

W_bug uses an external group-level checker covering all 64 six-bit words after
both matched and mismatched groups. W_preserve checks asynchronous reset,
exclusivity and stability. Their roles are task completion and preservation, not
request progress and FIFO capacity. The legacy witness matrix's shortcut role is
only a reset-defect positive control, not a prediction of Blue's repair.

The evaluator is separate monitor code with a deterministic random bit stream,
matched groups and asynchronous resets within partial groups. Public/evaluator
code is written by the same author; only their implementation and learning-channel
separation are independent. Checks are bounded and do not prove full correctness.

Suite and evaluator freezes precede all qualification overlays and Red proposals.
Two reserved evaluation mutants alter group-boundary consumption in different
terminal branches. Their code and results never enter Red or Blue learning;
only their public repair tasks enter deferred measurement probes. This measures
new variants on a seen design, not cross-design or unexposed-span transfer.
