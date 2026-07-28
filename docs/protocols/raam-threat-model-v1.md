# RAAM threat model V1

## Trusted authority

- Runner code validates immutable objects, replay pairing, resource use,
  qualification, and source semantics.
- Policy Registry V2 grants exactly one active whole-policy state and performs
  atomic promotion and exact rollback.
- Oracle/verifier evidence remains the sole correctness authority.
- Hash-chain stores and audits reconstruct facts; observability events never
  grant permission.

## Untrusted or non-authoritative components

- red, blue, shadow, retriever, analyzer, and model adapters;
- candidate builders and relation suggestions;
- model text, generated patches, cost claims, trigger claims, and adapter
  verdicts;
- files outside the frozen manifests and content-addressed stores.

Adapters cannot activate memory, write the registry, classify their own
qualification result, replace the case/seed/model/budget/verifier/toolchain,
or claim a source mutation without runner hash verification.

## Protected information

Red receives no source episode IDs/hashes, complete control deltas,
qualification evidence, oracle evidence, successful/reference patches,
target replay results, or child-validation output. LLM prompt construction
receives no memory text, historical patch, source-case signal name, or red
mutation truth.

## Fail-closed cases

- object, ledger, lifecycle, bank, prompt asset, manifest, command, result, or
  registry hash mismatch;
- missing/inconclusive oracle evidence;
- candidate, stale, harmful, retired, superseded, or out-of-bank memory;
- policy/bank/retriever/guard/whitelist mismatch;
- control conflict, unavailable analyzer, low-confidence retrieval, or budget
  overflow;
- cross-policy replay without explicit revalidation provenance;
- red operator without a changed RTL source hash and semantic postcondition.

Physical retention does not imply execution permission. An interrupted object
write may leave an orphan content-addressed file, but cannot create a
hash-chain index or active registry entry.
