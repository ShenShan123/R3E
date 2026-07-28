# RAAM test matrix V1

The executable coverage lives in `tests/test_raam_memory.py` and
`tests/test_raam_memory_runner.py`.

| Boundary | Covered behavior |
|---|---|
| Storage | episode/version reconstruction, append-only lifecycle, dedup, resume, injected object/index interruption |
| Payload | reject prompt, historical patch, oracle/testbench/registry/model route, AST rewrite, and red truth |
| Retrieval | runtime descriptor only, active-bank-only top-k, deterministic abstention, exact component hashes |
| Activation | current policy, active bank, whitelist, analyzer, conflict, status, and pre/post budget gates |
| Plan | zero memory tokens, one activation, reconstructable plan hash, executor policy/plan binding |
| Shadow | same case/seed/model/budget/verifier/toolchain, runner command/result hashes, helped/harmed classification |
| Qualification | retrieval/effect/safety/cost/provenance gates, cross-design support, explicit cross-policy revalidation |
| Promotion | bank child parent hash, registry candidate, paired replay, atomic promotion, stale rejection, exact rollback |
| Cross-round | static inheritance, affected-memory suspension, incremental replay, merge/split, bounded bank, no deletion |
| Red-memory | sanitized v3 packet, private evidence rejection, policy+bank binding, bypass/deepening/conflict source semantics |
| Recovery | shadow interruption, stage artifact hash mismatch, episode/lifecycle/bank interruption, idempotent complete round |
| Compatibility | legacy/whole-policy tests remain green; fake arena automatically archives every challenge |
