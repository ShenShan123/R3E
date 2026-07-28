# Replay-Activated Adversarial Memory V1 protocol

## Authority model

RAAM separates three permissions:

1. an episode or memory version may be saved only when its content hash and
   provenance are reconstructable;
2. a memory may enter an Active Memory Bank only after replay qualification
   and an append-only transition to `active_dormant`;
3. a bank memory may affect one authoritative repair only after deterministic
   current-case reactivation.

The adapter produces candidate artifacts. The runner reconstructs replay
facts and qualification. Policy Registry V2 alone grants bank authority by
promoting a `PolicyState` that binds the exact bank and component hashes.

## Immutable identities

- `VerifiedEpisode.episode_hash` binds the round, challenged policy instance
  and effective hashes, poison/RTL/oracle evidence, failure descriptor,
  attempts, outcome, activation record, and resource usage.
- `ControlMemory.memory_hash` binds its source episode IDs/hashes, creation
  policy, runtime-only trigger, whitelisted control delta, and initial
  candidate state.
- lifecycle is not written back into a memory object; every permission change
  is a hash-bound append-only `MemoryLifecycleEvent`.
- `ActiveMemoryBank.bank_hash` binds its exact memory versions, retriever,
  activation guard, whitelist, and policy-instance origin. The derived
  effective-policy binding is checked separately to avoid a circular hash:
  the child policy binds `bank_hash`, while the bank names that child's hash.

## Runtime data flow

```text
current observable oracle/diagnostic artifacts
→ FailureDescriptor
→ retrieve from active-dormant bank (top-k ≤ 3)
→ policy / whitelist / analyzer / lifecycle / conflict / budget gates
→ zero or one reactivated ControlMemory
→ ExecutionPlan
→ current-case evidence production
→ independent model reasoning
→ authoritative oracle
```

The plan contains structured controls only. `memory_token_cost` is always
zero. Prompt text, old patches, source signal names, reference labels, red
mutation truth, credentials, model routing, registry writes, oracle changes,
and AST rewrites are rejected.

## Shadow replay

Control and shadow arms bind the same case, seed, active policy, model, total
budget, verifier, toolchain, RTL, and stopping ceiling. Only execution-plan
controls may differ. The runner classifies `helped`, `harmed`,
`neutral_pass`, or `neutral_fail` and recomputes retrieval, effect, safety,
cost, and provenance gates. A failed gate cannot create bank authority.

## Storage layout

```text
runtime/memory/
├── episodes/
│   ├── objects/
│   └── episodes.jsonl
├── library/
│   ├── objects/
│   ├── memory_versions.jsonl
│   └── lifecycle.jsonl
├── relations.jsonl
├── qualification/
├── active_banks/
│   ├── objects/
│   └── active_banks.jsonl
└── activation/
```

JSONL indexes are hash chained and object files are content addressed.
Physical deletion is not a lifecycle operation.

## Compatibility and rollback

Policy fields on which a control delta depends are compared at policy
transition. Unaffected memories inherit `active_dormant`; affected memories
become `revalidation_required` and lose execution authority while their object
and evidence remain. A bank policy uses the existing Registry V2 promotion
and exact-snapshot rollback protocols.

## Memory evolution runner

`r3e.memory.runner.MemoryEvolutionRunner` owns a separate resumable state:

```text
load parent → build candidates → shadow qualify → build bank child
→ whole-policy paired replay → decision → atomic registry commit → complete
```

Target/non-target manifests and config are frozen into the state hash. Each
memory has an immutable qualification bundle so resumption does not repeat a
completed replay. Bank objects written before commit are non-authoritative;
only Registry V2 promotion grants execution authority.

Configuration-changing policy-search children carry an empty memory binding.
Static inheritance or incremental revalidation must explicitly construct a
new bank child, preventing an old bank from silently running under a new
policy.

## Red-memory loop

The v3 red-search context may contain a sanitized memory capability packet:
memory IDs/versions/hashes, trigger hashes, effective-delta hashes, and public
region summaries only. Episode provenance, qualification evidence, control
deltas, patches, oracle evidence, and child replay data remain private.

`memory_bypass`, `memory_deepening`, and `memory_conflict` plans bind the
challenged policy, active bank, packet, target memories, parent poison, and
parent source hash. Runner verification requires an actual changed RTL file,
a new source/diff hash, one-module/one-block scope, and operator-specific
semantic postconditions.

## Implementation boundary

RAAM Protocol Skeleton V1 implements the deterministic Phase 1–9 software
protocol. It does not claim complete authority closure, real-model memory
effectiveness, cross-design empirical gain, or a completed continual-learning
experiment. Grounded compile/formal/oracle receipts, artifact-derived failure
descriptors, and parser-backed RTL semantic diffs remain required before a
“System Upgrade Complete” milestone may be frozen.
