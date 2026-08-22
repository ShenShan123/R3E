# R³E-AIC competition architecture

```text
Repair Studio / Verification Trace / Evolution Lab / Memory / Dashboard
                                │
                         Competition API
                                │
                         Service Facade
                                │
                             R³E Core
             ┌──────────────────┼──────────────────┐
       Diagnosis          Candidate Portfolio       Policy / Memory
                                │
                   Parse → Compile → Simulation
                                │
                    R³E oracle_gate + Yosys
                                │
                       Accept / Reject
```

The facade owns presentation metadata and run artifacts. It does not grant
authority to model output. Candidate selection is based on the existing R³E
differential oracle; a compile-only result is never presented as a repair.

The live repair path uses the repository's strict OpenAI-compatible JSON
provider. The default guided replay path uses the frozen public reference RTL
only to make the demo deterministic, and the UI labels that provenance.
