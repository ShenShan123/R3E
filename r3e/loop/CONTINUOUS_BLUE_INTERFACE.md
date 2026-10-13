# Continuous loop: Blue experience and measurement interface

Implementation contract, 2026-10-10. Companion to RED_CONTINUOUS_LOOP.md.
Version identifiers below are new; do not reinterpret older frozen runs with them.
The runner and measurement owner implements request delivery and confirmation.
The Red/loop owner implements extraction, retrieval, snapshots and scheduling.

## Ownership and runner call

Extend the existing runner with optional keyword arguments:

```python
RepoBlueRunner.run(
    task, setup, *, seed,
    experience=None,           # prepared delivery envelope described below
    experience_journal=None,   # append-only local journal Path
)
```

Legacy `experience=None` preserves existing request bytes and behavior. Continuous
mode always passes an envelope, including for empty B_0, and requires a writable
journal before any request. The caller must not provide a nonempty payload without
a bound snapshot. The runner does not extract, retrieve, qualify or mutate memory.

The scheduler checks the snapshot against the frozen plan and committed state
chain before preparing the envelope. The runner checks the envelope's hash,
task identity, schema, token size and request binding before sending. Neither
component accepts arbitrary prompt keys or filesystem paths inside the payload.

Only direct-context tasks are supported in the initial continuous pilot. Keep the
existing RTL limit, total context limit and output reserve. Experience does not
cause a switch to file selection, a larger output budget or RTL truncation. A full
request exceeding context is an operational context limit, recorded before sending.

## Snapshots and entries

Each immutable snapshot manifest contains:

```text
version = continuous_blue_snapshot_v1
snapshot_id = B_0, B_1, ...
parent_snapshot_hash = null for B_0, otherwise previous manifest hash
committed_round = 0, 1, ...
blue_config_hash           # complete runner + client + tokenizer + runner-code identity
learning_policy_hash
retrieval_policy_hash
entries = ordered list of complete entry records
state_hash = hash_payload({blue_config_hash, learning_policy_hash,
                           retrieval_policy_hash, entries})
```

`snapshot_hash` is `hash_payload(manifest)` and is stored outside that manifest.
A no-update round has a new snapshot ID/manifest but the same `state_hash`.
`B_0.entries` is empty. Freeze the complete initial settings and policy identities
before round 1. Snapshots must remain reconstructible from archived bytes.

An entry contains an `entry_hash` plus a body hashed with `hash_payload(body)`:

```text
version = continuous_repair_episode_v1
created_round
source = {run_id, round, slot_id, task_hash, carrier_id, mutant_hash,
          candidate_hash, encounter_hash, archive_hash, public_replay_hash}
retrieval_terms = sorted unique terms from the source public specification
content = {
  validation: public_pass_single_sample_hidden_unknown,
  specification_excerpt: first 1024 characters of the public specification,
  applied_patch: complete archived Blue patch against its faulty input,
  public_rows: public suite names and checker outcomes
}
```

Hashes and source references are validated from archived records, not trusted
because an LLM returned them. All source episodes must precede the consuming round.
Only online repair encounters can create entries; reference, anchor, confirmation
and hidden-evaluation encounters never do. No Red edit, clean/reference answer,
Blue narrative, model reasoning or evaluator output enters `content`.

## Initial extraction and update policy

Use deterministic episode extraction for the first implementation; zero extraction
model calls. This is retrieval of observed repair examples, not automatic induction
of general repair rules. A later model-authored extractor is a separately budgeted
policy version, not an invisible replacement.

After all online slots finish, process their episodes in frozen slot order:

1. Require an applied candidate that passes the full public suite on offline replay
   matching its archived candidate and baseline hashes. Exclude failed, invalid,
   reference and probe encounters.
2. Copy the allowlisted fields above without interpreting a successful patch as a
   universally correct rule. Preserve the entire patch; omit an entry if its
   serialized model-visible form exceeds the per-entry limit below.
3. Deduplicate using `(task_hash, mutant_hash, candidate_hash)` against existing
   entries. Keep the first entry; record later occurrences in the update audit.
4. Append accepted entries to the incoming snapshot. No hidden-outcome filtering,
   forgetting or retrospective promotion occurs during this pilot.

This policy admits public-passing examples provisionally, without claiming the
old curriculum's paired usefulness qualification. Log that status explicitly.
Unresolved episodes remain in the observation archive, not in Blue's advice pool.
If nothing qualifies, commit a no-update state. Round 3 must be tested against the
state committed after round 2, even if that state is unchanged.

## Retrieval and fixed delivery size

Initial retrieval is deterministic and uses only the target's public specification.
Tokenize source and target specifications with `[A-Za-z_][A-Za-z_0-9]*`, lowercase,
and take unique terms. Rank eligible entries by descending Jaccard overlap,
breaking ties by ascending `entry_hash`; skip zero-overlap entries. Source task
outcomes, Red mutation locations and hidden information never affect ranking.
Freeze this simple baseline before collection rather than tune it using anchors.

The model-visible object is exactly:

```json
{
  "version": "continuous_blue_experience_v1",
  "notice": "Prior repair examples passed public tests only; validate their applicability to this task.",
  "entries": [
    {"entry_hash": "sha256:...", "content": {"validation": "public_pass_single_sample_hidden_unknown", "specification_excerpt": "...", "applied_patch": "...", "public_rows": {}}}
  ]
}
```

Use the pinned tokenizer and `canonical_json`. Each serialized `{entry_hash,
content}` must fit 2,048 tokens. Deliver at most two entries, and count the full
`{"repair_experience": payload}` wrapper against a 4,096-token cap. Iterate in
rank order, dropping whole entries that do not fit, and continue to later entries.
Record every excluded eligible entry as zero_overlap, entry_too_large,
delivery_token_limit or entry_count_limit. Never cut a patch or add a summarization
call to fit it. The runner verifies the prepared limit independently.

For an empty selection, omit `repair_experience` from the model request entirely
to preserve the no-experience prompt, but journal the explicit empty selection.
For a nonempty selection, add exactly this user-object field starting at attempt 1.
Selection is frozen once per encounter from its initial public spec; use the same
payload on later attempts or authorized retries. Do not retrieve again from the
evolving candidate or from newly committed states. No omission reasons, exposure
tags or snapshot metadata are appended to the model-visible field.

## Delivery envelope and journal

The `experience` argument has exactly these fields:

```text
version = continuous_blue_delivery_v1
snapshot_id
snapshot_hash
state_hash
task_hash                 # complete exported public task identity used by plan
retrieval_policy_hash
payload                   # model-visible object above, possibly empty entries
selected_entry_hashes     # in exact payload order
source_record_hashes      # source encounter hashes in corresponding entry order
omissions                 # [{entry_hash, reason}], audit-only
payload_hash              # hash_payload(payload)
```

The caller archives the complete envelope; `delivery_hash = hash_payload(envelope)`.
The runner rejects unknown fields, inconsistent hashes/order, a wrong task or
policy binding, or an oversized payload before any HTTP request. Provenance chain
validation belongs to the scheduler; a self-consistent envelope alone does not
establish an authentic source. Allowlisted payload fields require validation too.

Before EACH repair HTTP request, durably append a journal record with:

```text
sequence, run_id, encounter_id, task_hash, attempt, request_ordinal,
snapshot_id, snapshot_hash, state_hash, delivery_hash,
payload_bytes = canonical_json(payload), payload_hash,
selected_entry_hashes, source_record_hashes, omissions,
delivered = bool(payload.entries),
experience_tokens, counted_request_tokens,
request_hash = hash_payload(messages)
```

`experience_tokens` counts the full inserted wrapper, or zero when omitted.
`payload_bytes` is the exact canonical JSON string for the inserted object; it is
not a reconstructed summary. All hashing uses the repository canonical JSON
function, not an unspecified serialization. No credentials or transport headers
enter this journal. A journal write failure prevents sending. Record a separate
preflight event for a request refused before HTTP; do not claim it was delivered.
Join provider outcomes by request ordinal and the shared call ledger, including
failures whose billing is unknown. Each encounter result records the snapshot
and delivery hashes so classification cannot silently use a later state.

## Exposure audit (measurement-only)

Build exposure metadata separately from the learning content; Red mutation edits
must not enter Blue's prompt through these tags or through retrieval ranking.
Report the following for both the full incoming store and the delivered subset:

- `same_mutant`: exact match of the canonical faulty task identity (source family,
  frozen base/configuration and sorted RTL contents), with matching entry IDs.
  Also retain raw RTL hashes for auditing byte repeats; equal file bytes across
  different configurations do not automatically mean the same task.
- `repair_span_overlap`: a prior Blue repair touches a region edited by the current
  mutation, under the same source family, clean revision/configuration and path.
  Record matching entry IDs and intersections, not just a boolean.
- `span_mapping`: known or unknown. Map both sets of edits to half-open character
  intervals in the frozen clean source via the source mutant and actual repair
  diffs. Record insertion points separately. Equal insertion anchors count as
  overlap; a point within an interval counts, including its start and excluding
  its end. Ambiguous/repeated-text or generated-text mappings are unknown, not
  evidence of no overlap. Preserve the original diffs for review.

Report exact-repeat, span-overlap-only, neither-observed and unknown groups.
These tags do not establish semantic equivalence, mechanism novelty or causality.
Within-run duplicate rejection and anchor collision checks remain active; tags
also apply to preexisting experience and measurement replays, rather than forcing
duplicate challenges into the stream to create an exposed group.

## Frozen reference, anchors and confirmation

Before round 1, archive B_0, three human-defect anchor tasks, suite identities,
tokenizer/configuration, fixed encounter ordering and nonoverlapping seed labels
for online, reference, anchors and any later confirmation. Seed labels are audit
identifiers; they do not guarantee deterministic provider sampling.

Reference B_0 repairs every admitted online challenge independently. Its empty
experience envelope remains explicit. It never contributes Red feedback or Blue
learning. Anchor probes cover B_0 and every committed B_t; run them after collection
from saved snapshots, keeping all results out of selection and update decisions.
Reserve their worst-case calls up front. Candidate/anchor collisions are rejected
without returning anchor identities or outcomes to the learning channel.

A confirmation manifest must bind each member to `task_hash`, `snapshot_id`,
`snapshot_hash`, `state_hash`, `delivery_hash`, full configuration and three fresh
seed labels. Restore the exact original snapshot and task-specific delivery;
refuse changed task, code, retrieval policy or payload. Do not default to latest
B. Freeze eligibility, total cost and the complete manifest before separately
approved calls. The three fresh outcomes exclude the screening sample. Keep
operational failures separate and require reviewed functional failures for the
operational two-of-three criterion.

## Post-run source audit

Join entry sources by task and candidate hash, never candidate hash alone. Verify
each unique source once per frozen evaluator identity and link results to every
snapshot/entry that uses it. For each B_t report unique sources, affected entries
and actual deliveries by no_mismatch_within_scope, checker_mismatch,
inconclusive and unavailable, with mismatch review status separate. An entry
present in several snapshots is not several independent source repairs.

The audit writes new measurement artifacts only. It cannot delete an entry from
old snapshots, change an earlier prompt or reinterpret a provisional public-pass
label as knowledge available during collection. No-mismatch is bounded evidence,
not a proof that the learned experience is correct in all contexts.
