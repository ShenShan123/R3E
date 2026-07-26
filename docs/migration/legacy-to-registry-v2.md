# Legacy to Policy Registry V2 migration

Run the migration only against fresh output paths:

```bash
python -m r3e.policy.migrate \
  --from legacy \
  --to registry-v2 \
  --legacy-skills configs/skills.json \
  --base-template configs/base_policy/frozen_base_policy_v1.json
```

Default outputs are ignored runtime artifacts:

- `runtime/migration/frozen_base_policy_migrated.json`
- `runtime/registry/policy_registry.json`
- `runtime/migration/legacy_to_registry_v2.json`
- `runtime/registry/decision_ledger.jsonl`

The command refuses to overwrite outputs. Manual skills are copied into a
hash-bound frozen base asset. They are not converted to automatic promoted
policies. If an old registry is supplied with `--legacy-registry`, its
artifacts are listed in the report as
`excluded_no_v2_promotion_evidence`; none enter active evolution history.
The resulting registry contains only active B0.

Historical imports remain available through deprecated wrappers, but legacy
memory, manual skills, and preflight are valid only with
`formal_mode=False`.
