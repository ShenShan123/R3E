# Legacy experiment compatibility

Historical R³E experiment scripts remain available through their original
import paths. They are non-authoritative and must run with
`formal_mode=False`.

The principal monolithic legacy driver implementation now lives at
`experiments/legacy/correctness_gated_accumulation_curve.py`. Its original
`r3e/semantic_repair_bench/correctness_gated_accumulation_curve.py` path is a
deprecated module alias that preserves imports, CLI execution, and historical
monkeypatch behavior. New round orchestration belongs in `r3e/arena/`; no
Whole-Policy Evolution functionality may be added to the monolithic driver.

The historical red mutator implementation now lives at
`r3e/legacy/red_mutator.py`. Its original
`r3e/semantic_repair_bench/red_mutator.py` path is a deprecated compatibility
facade. Formal red generation uses `r3e/red/` and never imports the legacy
mutator.
