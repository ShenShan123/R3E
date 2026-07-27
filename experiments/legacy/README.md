# Legacy experiment compatibility

Historical R³E experiment scripts remain at their original import paths while
the Whole-Policy Evolution migration is in progress. They are non-authoritative
and must run with `formal_mode=False`.

The principal monolithic legacy driver is
`r3e/semantic_repair_bench/correctness_gated_accumulation_curve.py`. New round
orchestration belongs in `r3e/arena/`; no Whole-Policy Evolution functionality
may be added to the monolithic driver.

The historical red mutator implementation now lives at
`r3e/legacy/red_mutator.py`. Its original
`r3e/semantic_repair_bench/red_mutator.py` path is a deprecated compatibility
facade. Formal red generation uses `r3e/red/` and never imports the legacy
mutator.
