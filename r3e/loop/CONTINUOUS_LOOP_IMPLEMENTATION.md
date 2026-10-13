# Continuous loop implementation handoff

2026-10-10. Callable integration and a separately invoked guarded launcher;
fake-client offline acceptance only, no monetary authorization or learning-effect result.

## Entry points

- `r3e/loop/red_continuous.py`: strict edits-only generation, prior-round public
  observation bank and durable pre-request recording. No predicted repair.
- `r3e/loop/continuous_state.py`: deterministic episode extraction, immutable Blue
  snapshot transitions, Jaccard retrieval and bounded whole-entry delivery.
- `experiments/loop_probes/continuous_loop.py`: plan/material/code freeze,
  clean-carrier reset, public admission, online/reference trials, atomic joint
  Red/Blue commits, deferred anchors and shared budget accounting.
- `tests/test_continuous_loop.py`: synthetic carriers and fake provider replies
  with real public compilation, simulation and structural checks.
- `experiments/loop_probes/continuous_launch.py`: offline freeze/check and explicit
  collection for the 21-call fixed-ZipCPU pilot. Shares the existing transport,
  balance and usage guards; accepts only continuous Red and direct Blue requests.

Construct qualified carrier descriptors with `package`, `suite_freeze`,
`evaluator_freeze`, `evaluator_root`, plus previously exported anchor descriptors
`{task_dir, carrier_index}`. Call `make_plan(..., counter, client_config)` offline;
it qualifies public matrices and hashes evaluator material without executing it.
`collect(plan, new_directory, client=..., counter=...)` accepts only a fresh client
whose identity and exact call cap match the plan. No credentials are loaded here.
The new launcher requires independent review and explicit spending approval.
The old launcher's prompt allowlist remains unchanged.

Default schedule in `continuous_loop_v3`: three rounds, one slot per round, one
online attempt and one independent B_0 attempt per admitted challenge. All online
slots use the same frozen `fixed_carrier_index` (default 0) with `carrier_mode=fixed`.
Callers should select that index explicitly. `carrier_mode=rotate` preserves the
optional across-carrier schedule; changing either mode or index requires a new
plan and seed namespace. Online/reference order alternates deterministically. All B_0..B_3
anchor probes run after the final online commit. For three anchors this reserves
21 calls; unused slots are not replaced. Model-based learning and confirmation
are absent. `B_0` hashes the client settings, runner settings/code and tokenizer,
not just the empty entry list.

The first extractor stores actual public-passing episodes provisionally. There is
no claim of learned general rules or paired usefulness qualification. Only online
episodes teach Blue; functional failures and valid no-ops can inform Red with an
explicit single-sample/unconfirmed label. Operational failures are archived but
do not become weakness observations. No-update rounds retain the same state hash.

## Artifacts for measurement

- `plan.json`, `plan_hash.json`, `code_snapshot/`: complete frozen schedule and
  recoverable source bytes. Old experiments and snapshots are not modified.
- `calls.jsonl`: root ledger with `role` (`red`, `online`, `reference`, `anchor`),
  slot and snapshot labels, including failed requests.
- `rounds/<round>/commit.json`: atomic joint commit containing the Blue manifest,
  Red observations, parent commit hash and update decisions. Round 0 holds B_0.
- `rounds/<round>/<slot>/red/`: exact request/messages, delivered Red bank bytes,
  incoming state hashes, omissions, response and materialized proposal.
- `.../admission/`: original mutation edits, candidate RTL, public/structural
  checks, clean-backed task export. Reserved anchor collisions are recorded and
  do not create learning feedback or replacement slots.
- `.../online/` and `.../reference/`: archived delivery envelopes, experience
  journals, Blue configuration, encounters and every materialized candidate.
- `sources/<encounter-hash>.json`: source task, candidate archive path, actual
  encounter, public replay, source hashes and mutation edits. These audit records
  contain no hidden result. Only their allowlisted content reaches learning.
- `anchors/`, `probes.jsonl`: isolated per-snapshot measurement outcomes.
- `collection.json`: completion/abort, committed rounds, call totals, incomplete
  slots and missing anchor probes. A partially run round has no state commit.

The exposure auditor can join admission edits and `sources` to the clean source
in each exported task, then compare full incoming snapshot entries against the
delivered entry hashes. The source-quality auditor can join task/candidate hashes
and archived candidates to frozen evaluator identities after collection. Both
write separate reports; neither may rewrite snapshots or delivery logs.

## Interface clarifications

`path_policy=absolute_paths_preserve_original_locations` is explicit in the plan.
Source, task, archive and probe paths are absolute. The recorded run and carrier
roots must stay available at their original paths: a copied directory is not a
self-contained relocated replay. Back up those roots as well as the code snapshot.
A future portable export requires coordinated rebinding of source and measurement
paths with a new manifest; do not edit frozen paths in place. This version records
the limitation rather than silently changing the measurement interface.

The Blue journal is run-scoped; its file path supplies run identity. An entry body
is checked by the scheduler against stored provenance, not by the runner, which
receives only the visible entry projection. Every use authenticates committed
state and source archive/encounter bytes. The existing measurement interface
checks the plan's retrieval-policy hash; this scheduler supplies it explicitly.

Red's bank is capped at 8,192 tokens, whole observations, newest earlier-round
records first. Omission metadata is audit-only. It receives online public patches
and bounded public evidence, never model descriptions, reference/anchor outputs,
hidden results or proposed repairs invented by Red.

In v3 the Red payload also contains `target_blue`: the authenticated incoming
snapshot identity, current repair budget, learning/retrieval policy and selected
provisional experience for this carrier specification. `select_experience` is
shared with Blue's actual delivery; this is a spec-conditioned preview, not a
claim that a request has already been sent. `target_view_hash` and preview omission
records are archived before the Red request. Snapshot/round mismatches refuse
before creating the request directory, and a preview over 6,144 tokens is recorded
as a preflight refusal. Blue's format, qualification baseline and hidden isolation
are unchanged. New version/seed/code identities require a fresh future plan; the
completed v2 run and its code copy remain intact and are not resumed.

The regression fixtures are toy arithmetic modules. The original rotating fixture
remains explicitly rotating for audit compatibility. A fixed-carrier regression
checks same-carrier history and delivered repair-span overlap in rounds 2 and 3.
Neither establishes Red difficulty, Blue learning or transfer. Separate real-carrier
acceptance uses public-qualified, human-authored mutations and clean repair edits;
its scripted model replies must not be reported as model discoveries or gains.
All existing v1 acceptance directories and snapshots stay unchanged.
