# 907 generation handoff

This implements the first single-file carrier path in `RED_907_EXPERIMENT.md`.
The current generator protocol is `red_progress_preservation_v3`: comment
preservation remains an admission condition, and source-token accounting now
supports macro references. Existing v1/v2 evidence stays historical; its code
hashes must not be relabeled as v3 acceptance.
It does not launch a paid experiment, select additional carriers, or change the
older residual-fault search protocol. Human-authored qualification variants and
scripted transport checks are not model-generated challenges or Blue outcomes.

## Delivered interfaces

* `r3e.loop.red_regression.RegressionGenerator`: one budgeted abstraction or
  proposal call, exact request sizing, archived requests/responses, no implicit
  retries. Its client must authorize the configured output limit at construction.
* `arm_context`: ordinary gets no source packet; automatic gets the packet and
  its first schema-valid frozen abstraction; manual gets the same packet and the
  fixed human summary. A malformed extraction is recorded without fallback.
* `experiments.loop_probes.red907.source_packet`: mechanical source export from
  the public task, selected encounter/archive, and reviewed source replay. It
  checks candidate/baseline/spec/harness/build identities. Only the candidate's
  declassified regression stimulus and trace are exported from the replay, not
  reference rows, upstream patches, review prose or inferred model reasoning.
* `generate_slot`: at most two Red proposal calls. The first admitted mutant ends
  generation regardless of its shortcut diagnostic. Abstention/inconclusive
  checks stop the slot. Only a rejected proposal may receive a second call using
  that slot's public feedback. There are no replacement slots or Blue probes.
* `check_proposal`: checks the unchanged freeze; applies a 40% code-token AND
  body-token ceiling against the primary source only; runs clean/candidate public
  suites twice and Yosys structural checks with the declared parameters. Candidate
  admission requires W_bug fail, W_preserve pass, and a successful structural
  check. This is public functional admission, not semantic proof or difficulty.
  Candidate comment text, including ordering, must equal clean's comment text.
  Added/deleted/changed comments produce `comments_changed`, equally in every
  arm, even when accompanied by a valid RTL mutation. Strings and escaped names
  are distinguished from comment delimiters. New identifiers are recorded in
  `language_review.new_identifiers`, not prohibited. This closes the explicit
  comment channel, not every possible semantic hint in executable RTL.
  Tokenizer `verilog_source_tokens_v2_macros` counts each backtick macro name as
  one source token and its arguments separately, including line-start calls.
  It does not expand macros or count dependency definitions as mutation-budget
  padding. Known compiler directives, including continued definitions, remain
  immutable. Definitions/expansion validity are checked by the compiler, not
  certified by the source lexer. Budget records carry the tokenizer version.
* Predicted edits apply to the final mutant. Their materialization/compilation,
  structural check, and W_bug/W_preserve outcomes are separate diagnostics. A
  missing or invalid predicted edit, or an unsupported diagnostic matrix, does
  not veto functional admission. Mechanism review is still required.
* Every materialized candidate is saved. Admitted candidates export a `blue_task`
  using the existing carrier exporter, ready for `RepoTask.load`, `setup`, and
  `RepoBlueRunner.run`. Its public tree contains only mutant RTL, dependencies,
  specification and public suites. The clean source remains under `oracle/`.

The v1 adapter explicitly supports one editable primary file, immutable listed
dependencies, Icarus, integer parameters, and no extra defines/include paths.
Unsupported build settings fail before generation rather than being dropped by
the task exporter. No multi-file framework was added.

Declared `header_dependencies` are included verbatim in Red's immutable
`dependencies` context and copied into the candidate-check scratch package.
Every header must be covered by the public suite freeze; missing, changed or
unfrozen headers are refused before a generation call. Header declarations are
also frozen by the measurement-side manifest check. Only `rtl` entries are
passed as compilation units, so parameter-list and module-body include fragments
remain valid. The exporter carries headers into the Blue task as read-only
dependencies. Mutation and predicted-repair scope, and both edit-budget
denominators, remain the primary file alone. Packages without this optional
field retain their existing input format. This adapter change invalidates old
scheduler plans through their code hashes; it does not qualify a new carrier.

## Integration

Use a shared `BudgetedClient` with an already authorized cap and `on_call` ledger,
and the pinned `TokenCounter`. Do not reuse old experiment reserves. For automatic
history, call `generator.extract(packet, seed=..., directory=...)` once, then
`arm_context("automatic", packet=packet, abstraction=record)`. Ordinary uses
`arm_context("ordinary")`; manual uses `arm_context("manual", packet=packet)`.

Call `generate_slot(generator, package, freeze, context, output, seed=...,
seen=arm_seen, proposal_calls=2)`. Use a separate deduplication set for each arm;
the same challenge occurring in different arms is a valid comparison. Persist
the admitted fingerprints with the experiment state. A slot/extraction output
directory must be new: interrupted work is inspectable and cannot silently be
rerun. This component does not implement experiment resume.

For each admitted candidate, pass its exported task to the existing Blue runner.
Use three independent encounters with identical configuration and memory disabled.
Do not put Red requests, hypotheses, source packets or shortcut code into Blue's
inputs. Only measurement-side review can assign a target mechanism or confirm a
2-of-3 endpoint result; this module never assigns hits.

## Frozen scheduler

`experiments.loop_probes.red907_schedule` provides `make_plan`, `collect`, and
`classify_collection`. There is no paid CLI or credential loader. A caller must
supply a fresh authorized BudgetedClient and the pinned counter. A partial
one-carrier plan can be inspected offline but cannot start collection. Distinct
declared source families/repositories are checked; this is not an automated
ancestry audit. Human review must still establish that the three carriers are
independent, and approve the final monetary/call budget.

The plan records source and public/evaluator freezes, exact qualified package
files, generator and measurement code hashes, model configuration, tokenizer,
and (scheduler v3) the actual Python interpreter path, binary hash, version and
implementation. A different runtime is refused before collection or classification.
Use the interpreter in which the pinned TokenCounter can actually be constructed;
a skipped optional tokenizer test is not pilot environment qualification. This
freezes the interpreter, not every installed package; tokenizer data retains its
separate pinned identity. The plan also records
seeds, slots, retry policy and equal arm caps. Collection checks these first and
before each slot. Arm order is seeded and rotated across carriers. Each arm has
one slot per carrier. Per-arm clients consume the same root ledger and cannot
borrow from another arm. An unused abstraction allowance or an early success
never creates an extra slot. Interrupted collection is journaled and not resumed.

Scheduler v2 records a preflight baseline replay disagreement as `blue_refused`
and continues to the next scheduled slot, with no retry or replacement. This is
allowed only before any Blue call/output directory, after rechecking the plan
and exported task hashes. The generation remains admitted and the slot remains
in the denominator. Changed exports, insufficient reserved calls, and unexpected
exceptions still abort collection: these indicate integrity/accounting problems.
The current measurement exception has no reason field, so only its exact
baseline-refusal message prefix is recognized; other refusals fail closed.

Each slot reserves two Red proposals plus three complete Blue encounters. The
reservation conservatively includes possible file-selection calls because a
mutant can exceed the clean design's context size. With three attempts and two
provider retries per encounter this is 26 calls per slot (2 + 3 * 8), versus 17
if direct routing were guaranteed. For three carriers the default per-arm cap
is 79, total 237, including one reserved extraction call per arm. Only automatic
uses that extraction call. These are worst-case ceilings, not expected usage or
approval. Red provider retries and extraction retries are zero; malformed Red
proposals may use only their remaining second proposal call. Blue uses the
existing two-retry rule per encounter.

The scheduler writes `plan.json`, `plan_hash.json`, `calls.jsonl`, `events.jsonl`,
`slots.jsonl`, and `collection.json`. For the measurement funnel, the plan's slot
list supplies the denominator; each recorded row contains `slot_id`, `arm`,
`carrier_id`, status and actual calls, with generation and Blue artifact paths
when available. `not_started_budget`, `not_started_abstraction_unavailable` and
`red_provider_failure` remain explicit. Aborted/unrecorded slots stay in the
denominator. Each call is labeled by arm, slot and abstraction/generation/Blue
stage. Reviewer-facing items continue to come from the measurement module with
arm and Red narrative masked; the new-identifier list can be attached separately.
`calls` includes any extraction done when that slot opens; `extraction_calls`
identifies that subset, while `slot_calls` excludes it. Do not add extraction to
`calls` again. The shared call ledger remains the accounting authority.

Only `classify_collection`, explicitly called after collection, invokes hidden
checks. It rechecks the freeze and refuses to aggregate a purported public pass
whose public replay fails, or whose evaluator result is timeout/compile failure
or an unmarked failure. The measurement classifier now records
`public_pass_hidden_inconclusive` and `replay_mismatch` explicitly; neither is
eligible for target review. The scheduler guard remains a consistency check for
the functional categories. No evaluator outcome feeds generation or Blue retry.
All older plan drafts predate these code changes and must be regenerated before
collection. No new paid plan is frozen while carrier qualification is incomplete.

## Offline proposal replay

From the repository root, with a fresh output directory:

```bash
PYTHONPATH=. python -m experiments.loop_probes.red907 "$PACKAGE" \
  --freeze "$SUITE_FREEZE" --proposal "$RAW_PROPOSAL_JSON" --out "$NEW_OUTPUT"
```

This CLI never constructs a model client. It validates one saved raw proposal,
archives the public checks and exports the admitted Blue task. Its deduplication
scope is this single replay; use `generate_slot` with persistent per-arm state for
collection. Temporary simulator and synthesis directories are deleted on exit.

The durable tests cover input isolation, frozen extraction, budget authority,
bounded calls, abstention, per-arm deduplication, changed material rejection,
source export provenance, task execution and diagnostic-independent admission.
Actual carrier acceptance evidence lives outside the release repository.
