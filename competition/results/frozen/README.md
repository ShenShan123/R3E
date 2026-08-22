# Frozen evidence

Evidence is intentionally separated by authority:

- `demo/` proves the three public EDA-gated demonstration cases;
- `benchmark/` contains benchmark metrics only when a validated benchmark
  artifact is frozen;
- `evolution/` contains policy-evolution evidence only when target replay,
  non-target regression, promotion, and provenance are present;
- `memory/` contains activation evidence only when shadow, replay, regression,
  and provenance are present.

Run logs, model responses, credentials, and machine-bound artifacts stay
outside the repository. `available: false` is an explicit unavailable state,
not a zero result.
