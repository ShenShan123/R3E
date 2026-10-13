# Red search using observed repair feedback

Status update, 2026-10-09: the user has clarified that the intended system is
continuous, round-based Red/Blue adaptation. The fixed-Blue comparison below is
retained as a possible component ablation, not the implementation target for the
main loop. See [RED_CONTINUOUS_LOOP.md](RED_CONTINUOUS_LOOP.md). Its two-round
schedule, successful-repair-only feedback and memory-free Blue settings must not
silently become the continuous loop's defaults. No new spending is authorized.

Prospective protocol decision, 2026-10-09. This document specifies the next
implementation; it is not an implemented runner, a runnable frozen plan or
approval for model calls. The 907 runs and their results remain unchanged.

## Question and scope

Does access to prior observed public-test repair examples improve Red's ability
to construct difficult defects, compared with independent generation given the
same proposal and Blue-call opportunities?

The first version tests feedback from public-accepted repairs: which challenges
Blue already handled, and which code changes it made. A single failed attempt is
not a validated weakness and does not become a positive reward or an adaptive
priority. Confirmed failure feedback can be studied in a later protocol; it is
not silently added here. This restriction is intentional and part of the claim.

This is a within-carrier development study. Hazard3 and ZipCPU have already been
seen in earlier work, so neither they nor the unused Ultra-Embedded carrier are
unseen holdouts. Fresh Blue confirmation samples test repeated failure on these
carriers; they do not establish cross-design transfer. Eventual transfer requires
new families reserved before generation-policy or memory development.

## Two arms and the fixed collection schedule

There are two rounds, two carriers and two arms:

- `feedback`: fresh Red calls with the mechanically assembled feedback bank below;
- `independent`: fresh Red calls with an empty feedback bank and no previous
  proposals, admission outcomes, Blue patches or trial outcomes in the prompt.

Both use the same generator system prompt and output schema. There is no 907
source packet, manual mechanism summary or abstraction call in either arm.
Round 1 has empty feedback in both arms. Only the feedback field differs in round
2; arm labels are bookkeeping metadata, not extra instructions to the model.
Model settings, primary-file editable scope, syntax/body ceilings, comment guard,
public material, tools and context/output limits are matched. Calls have fresh
conversation state; seed labels do not guarantee provider-level independence.

Select the first two already-qualified carrier IDs by ascending SHA-256 of
`repair-feedback-pilot-v1:` followed by the carrier ID. This yields:

1. `wren_hazard3_frontend_rv32_no_c`;
2. `zipcpu_wb2axip_axilfetch_32x4`.

Selection is a deterministic budget reduction, not a difficulty ranking or a
claim that these carriers represent hardware generally. Their old results remain
known development evidence. Freeze exact source, spec, configuration and suite
identities in the runnable plan; do not reuse old paid proposals or abstractions.

For every (round, carrier) pair, draw one independent proposal from each arm before
running either Blue. Alternate the first arm by `(round_index + carrier_index) % 2`.
Each Red call gets one answer opportunity, with no format/provider retries. Each
Blue encounter gets one direct repair call, no retries, localization or memory.
No rejected, abstaining, duplicated, oversized or failed slot is replaced.

Round 2 feedback is frozen only after all of round 1 completes. It cannot include
same-round or other-arm results. The two feedback-arm calls in round 2 receive
the same bank; carrier order must not create an extra update within the round.

### Matched Blue exposure

Admit proposals individually and retain both admission records. Run the Blue pair
only if BOTH candidates independently satisfy admission and direct-context
preflight. Preflight both before the first Blue call; reserve the complete pair.
If either fails admission, needs selection or exceeds the full request budget,
neither receives a Blue call. Label the otherwise eligible partner
`admitted_not_evaluated_pair`; do not label it repaired, easy or invalid.

This deliberately matches planned Blue calls rather than spending unmatched calls
on one arm. It conditions the repair comparison on jointly evaluable pairs and
can suppress a real admission-yield advantage: report that limitation and each
arm's admission yield over ALL scheduled slots. Do not generalize the conditional
comparison to all generated challenges or hide co-admission losses.

A transport failure uses its allotted request. If the global halt does not fire,
the paired request still runs as scheduled, with no replacement. A global stop can
leave a pair incomplete; retain it in the funnel and exclude it from paired
functional comparisons. Actual requests and missing outcomes remain explicit.
Unused calls cannot be transferred between arms or converted into trials.

## Red's task and output

Red receives correct carrier RTL, unchanged specification/public material, and
its allowed feedback bank. It proposes one coherent specification violation.
It may change linked behaviors at multiple locations within existing edit limits.
More changed lines, rarity of activation or failure of a hypothetical repair are
not difficulty rewards. Any generated mutant can in principle be undone; there
is no ban on recovery of the clean implementation or on a small correct patch.

Required response:

```json
{
  "edits": [{"find": "unique clean RTL span", "replace": "mutated span"}],
  "violated_requirement": "brief statement of the unchanged spec requirement",
  "spec_basis": "short citation or location in the supplied specification"
}
```

Alternatively return `{"abstain": "reason"}`. No predicted Blue edits, predicted
wrong-repair regression, imagined Blue reasoning or extra abstraction response.
The generator prompt asks Red to explore coherent defects beyond patterns covered
by supplied public-accepted repairs, without assuming those repairs are globally
correct or forecasting a particular next Blue patch. All candidate mutations
start from the frozen clean design, not from a growing stack of old mutants.

For this first feedback comparison, retain the existing public admission row:
clean passes both suites; mutant compiles, fails W_bug through its checker and
passes W_preserve. Retain interface, dependency, logic-loop, edit-budget and
within-arm duplicate checks. Cross-arm duplicate mutants remain allowed and are
reported; identical mutants are not independent constructions for counting unique
challenges. There is no shortcut check and no placeholder shortcut result.

Thus the defect pool still consists of specified progress defects, while repair
endpoints below no longer require the 907 mechanism. Broader mutation admission
would be another protocol change, not an implicit consequence of removing the
prediction. This separates feedback changes from a simultaneous domain expansion.

## Recorded feedback with no hidden channel

Only feedback-arm round-1 trials can supply the round-2 bank. An example is
releasable only when a valid materialized Blue answer passes the full PUBLIC suite.
Its label is `public_pass_single_sample_hidden_unknown`, not `correct_repair`.
Do not run hidden checks to decide release. A later hidden failure cannot alter
what was delivered or retrospectively recategorize the feedback as verified.

The bank contains, per releasable trial:

- source round/slot/carrier and the public spec/configuration identity;
- Red's actual mutation edits and resulting mutant hash;
- Blue's applied patch relative to that mutant and materialized candidate hash;
- public suite rows and the bounded public evidence payload actually available
  to Blue, using the same formatter and bounds; no additional raw log mining;
- the single-sample/hidden-unknown label and provenance record hashes.

Exclude hidden/evaluator material and outcomes, clean/reference patches used as
answers, prior 907 review prose, provider reasoning text, invented diagnoses,
other-arm outputs and all failed/no-answer/format/infra trial patches. The clean
carrier already supplied to both arms is not a feedback leak; do not append a
separate oracle repair to the bank. No candidate is marked a discovered weakness
from one observed failure. Nonreleased trials stay in the measurement archive;
the Red request does not receive their failure status or a positive score.

The bank has at most two packets, in frozen carrier order. Each serialized packet
is limited to 4,096 tokens using the pinned counter, with at most 8,192 total.
Keep exact patch content: omit an oversized packet in full rather than silently
cutting hunks or making an LLM summary. Record omission and actual delivery rates.
This size-dependent missingness limits conclusions and is reported. Both arms
retain identical full-request limits; feedback displaces available context in the
feedback arm and is included in its cost. Do not pad the control with fake text.

The plan freezes packet schema/version, allowlisted inputs, formatter and builder
code hashes, token limits, deterministic selection/omission/order rules and the
round barrier. Future packet bytes do not yet exist and cannot honestly be hashed
in advance. Before EACH Red request, persist exact packet bytes, their hashes,
source-record hashes, omission records and the complete request hash in the run
journal, and verify all source events precede the round barrier. The independent
arm records the explicit empty bank. Hashes make delivery auditable; content
allowlists and leakage tests enforce the information boundary.

## Screening and separately approved confirmation

Keep the one-attempt screen and an independent confirmation stage distinct.
Round 1 is feedback acquisition; round 2 is the prespecified adaptive/control
comparison. Report both rounds' full funnels and keep round-1 candidate failures
as exploratory observations, not evidence of benefit from feedback.

A screening functional-failure candidate requires either:

1. A usable materialized Blue answer compiles but fails at least one public suite
   through a checker violation under legal inputs, whether the original defect
   remains or a new functional regression appears; or
2. The patch passes all public suites but violates a legal frozen evaluator check,
   assessed only after collection.

Record original-defect persistence versus newly introduced regression separately.
A syntactically valid no-op answer is an unrepaired functional candidate, not an
incorrect-change claim; distinguish it from malformed output or no answer. A
compiler failure, edit-application failure, output cutoff, provider/tool failure,
context limit, unmarked exit, evaluator timeout and replay disagreement do not
count as functional difficulty findings. Keep their existing separate categories.
Public pass/hidden pass means only no mismatch within the checks.

For confirmation eligibility, inspect frozen round-2 pairs after collection and
all endpoint checks. If either arm has a reviewed functional-failure screening
candidate, enqueue BOTH members of that pair in frozen carrier order. Do not
select the most dramatic patch, discard the control, or use the screening sample
as one of the confirmation trials. If neither pair qualifies, there is no reason
to request confirmation calls.

A separate approval covers three fresh memory-free Blue encounters for EACH
member of every eligible pair, each with the same ONE-attempt settings as the
screen. At most two pairs imply at most 12 additional calls, no retries or refill.
Freeze this confirmation manifest before its first call. If the approved budget
cannot cover the whole eligible paired manifest, do not start a partial selection
based on desired outcomes. Confirmation results never return to this collection's
Red bank; the collection is already over.

A confirmed within-carrier difficulty finding requires at least two of these three
fresh endpoints to be blindly reviewed as legal, spec-grounded functional failure.
Public failures and hidden-only failures may contribute to this broad endpoint;
report them separately and label mixed confirmations. Two different failures do
not prove repetition of one mechanism. The 907 capacity mechanism is an exploratory
blind label only. Retain no-answer/infra samples among the three; do not replace
them or pretend lack of confirmation proves easy repair. Two-of-three is an
operational rule, not a precise failure-probability estimate.

Without approved confirmation, report screening candidates only and make no
confirmed difficulty or adaptive improvement claim. With confirmation, report
paired results for selected pairs, how pairs were selected, unconfirmed pairs,
admission yields and all scheduled denominators. A raw count difference on this
tiny development sample is descriptive, not proof of a general adaptive advantage.

## Budget and stopping

The collection schedule is fixed at 2 rounds x 2 carriers x 2 arms:
8 generation calls + at most 8 Blue calls = at most 16 HTTP requests, 8 per arm.
Each (round, carrier) pair reserves four calls before opening. One Red proposal
and one Blue attempt per arm; no extraction or localization call. Use the same
Flash/low settings and tokenizer as the prior screen, with output caps 65,536 for
Red and 32,768 for Blue, total context 131,072 and shown RTL budget 40,000.

Under the previously checked peak tariff (CNY 2/M input and 8/M output), filling
all sixteen requests to their limits costs CNY 8.912896 before counter tolerance.
A proposed CNY 12 collection envelope leaves room for the existing tolerance.
This is planning arithmetic, not spending authorization or a live price check.
The previous screen's unused call allowance is not reassigned. Confirmation cost
and funding require their own approval and ledger allocation; no extra repeats
are implicitly authorized by this document.

Stop at the fixed schedule/cap, a global provider/usage/integrity halt, or operator
stop. Also stop BOTH arms after round 1 with `no_released_feedback` if the frozen
bank is empty: round 2 would have no feedback intervention. Do not extend rounds
until a failure appears, refill rejected slots, resume implicitly, or spend savings
on confirmation. Actual delivered feedback, comparable pairs and losses are part
of the results. Funding checks and full possible-cost reservations remain active.

## Implementation handoff and acceptance

Red-side work: introduce a separate no-prediction generator/schema; implement the
frozen feedback-bank consumer and same-prompt independent control; implement paired
round barriers, pair admission/preflight gating, caps and stop rules. Save every
request/answer/candidate and exact bank delivery. Preserve old frozen snapshots.

Measurement-side work: remove the shortcut diagnostic in this new protocol only;
retain the public admission row; build allowlisted public feedback packets;
classify broad functional endpoints and valid no-op versus missing answers;
produce masked reviews and fresh paired confirmations. No hidden result enters
the packet builder. The tests below must use fake clients/synthetic evaluators.

Before any paid-plan proposal, offline acceptance must demonstrate:

- Round 1 prompts have no history in either arm; round 2 independent calls remain
  empty and feedback calls see only their own previous-round released packets.
- Hidden and other-arm sentinels never occur in feedback or Blue requests; old
  Red prediction fields are absent from schema, prompts and admission output.
- All-admitted fake collection makes exactly 16 calls, 8 per arm; failure, rejection,
  context limits and empty banks reduce work without adding retries or slots.
- Pair gating records an admitted but unevaluated partner without false outcomes;
  a global halt leaves missing data visible and does not fabricate paired results.
- Single failures never become positive rewards or confirmed findings. Confirmation
  uses only fresh, separately approved samples, with no case-dependent extra tries.
- The new plan and snapshot pin code/material/rules; future feedback packets bind
  to an append-only delivery journal rather than invented precomputed hashes.

This protocol fixes the next implementation contract. It does not claim that
removing prediction or exposing successful patches will make Red stronger. The
control exists precisely to test that assumption under the same opportunities.
