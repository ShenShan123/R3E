# R³E-AIC Competition M2 architecture

```text
Buggy RTL + TB
      │
Icarus runtime evidence + oracle mismatch
      │
Failure Descriptor v2 ──> descriptor router ──> R³E Blue Portfolio
                                                    │
                                         provider candidate receipts
                                                    │
                                            AST / Scope Authority
                                                    │
                               Parse → Compile → Simulation → Oracle
                                                    │
                                      Yosys Structural Check
                                                    │
                                      Same-case Repeatability
                                                    │
                                      Accept / Reject / Receipt
```

The facade owns presentation metadata and run artifacts. It does not grant
authority to model output. Candidate allocation and execution are delegated to
`r3e.blue.portfolio`; the competition adapter only binds the current case and
maps runner receipts to UI cards. Candidate selection is based on the existing
R³E differential oracle plus scope and structural gates; a compile-only result
is never presented as a repair.

The live repair path uses the repository's strict OpenAI-compatible JSON
candidate provider. The default guided replay path applies a checked-in
minimal patch to buggy RTL; the public reference RTL is used only as oracle
authority. Evolution, memory, and benchmark pages read independent frozen
artifacts and remain unavailable when those artifacts are absent.
