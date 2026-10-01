from __future__ import annotations

import json
from pathlib import Path
import re
import shutil

import pytest

from r3e.knowledge import (
    BugTypeInference,
    DeterministicKnowledgeAuthor,
    KnowledgeInjectingClient,
    KnowledgeMatcher,
    KnowledgeStore,
    KnowledgeValidationError,
    LlmKnowledgeAuthor,
    RepairEpisode,
    analyze_rtl,
    build_bundle,
    build_delivery_receipt,
    build_profile,
    build_repair_episode,
    classify_repair,
    compare_traces,
    verify_delivery_receipt,
)
from r3e.pilot.grd8_acp7_smoke import _load_manifest_case, _run_acp7
from r3e.protocol.hashing import hash_payload
from r3e.providers.openai_compatible import (
    OpenAICompatibleClientConfig,
    OpenAICompatibleJSONClient,
)


ROOT = Path(__file__).resolve().parents[1]
HAS_AUTHORITY_TOOLS = all(shutil.which(name) for name in ("iverilog", "vvp", "yosys"))


def _counter(name: str, out: str, limit: int, cmp: str) -> str:
    return f"""module {name}(input clk, input rst, output reg [3:0] {out}, output wrap);
  assign wrap = ({out} {cmp} 4'd{limit});
  always @(posedge clk) begin
    if (rst) {out} <= 0;
    else if ({out} {cmp} 4'd{limit}) {out} <= {out} + 1;
    else {out} <= 0;
  end
endmodule
"""


def _trace(values: list[int], signal: str, width: int = 4) -> str:
    header = "time," + ",".join(f"{signal}[{b}]" for b in range(width - 1, -1, -1))
    rows = [f"{i}," + ",".join(format(v, f"0{width}b")) for i, v in enumerate(values)]
    return "\n".join([header, *rows])


def _off_by_one_episode(name: str, out: str, limit: int) -> RepairEpisode:
    expected = [i % (limit + 1) for i in range(limit + 4)]
    observed = [i % (limit + 2) for i in range(limit + 4)]
    feedback = compare_traces(_trace(expected, out), _trace(observed, out))
    return build_repair_episode(
        case_id=f"case_{name}",
        design_cluster=name,
        buggy_rtl=_counter(name, out, limit, "<="),
        feedback=feedback,
        attempts=[
            {"verdict_tier": "visible_fail", "edit_class": "constant_value"},
            {"verdict_tier": "visible_pass"},
        ],
        passing_candidate_rtl=_counter(name, out, limit, "<"),
    )


@pytest.fixture
def episodes() -> list[RepairEpisode]:
    return [
        _off_by_one_episode("cnt_alpha", "q", 9),
        _off_by_one_episode("cnt_beta", "count", 5),
    ]


@pytest.fixture
def unseen_profile():
    limit = 7
    expected = [i % (limit + 1) for i in range(limit + 4)]
    observed = [i % (limit + 2) for i in range(limit + 4)]
    feedback = compare_traces(_trace(expected, "ticks"), _trace(observed, "ticks"))
    return build_profile(feedback, analyze_rtl(_counter("timer_gamma", "ticks", limit, "<=")))


# --------------------------------------------------------------- feedback


def test_compare_traces_classifies_symptoms():
    expected = list(range(8))
    offset = compare_traces(_trace(expected, "y"), _trace([v + 1 for v in expected], "y"))
    assert offset.primary.symptom == "constant_offset"
    assert offset.primary.delta == 1
    scrambled = [3, 7, 1, 4, 6, 2, 5, 0]
    lag = compare_traces(_trace(scrambled, "y"), _trace([0] + scrambled[:-1], "y"))
    assert lag.primary.symptom == "observed_lags_one_cycle"
    lead = compare_traces(_trace(scrambled, "y"), _trace(scrambled[1:] + [0], "y"))
    assert lead.primary.symptom == "observed_leads_one_cycle"
    stuck = compare_traces(_trace(expected, "y"), _trace([0, 1, 2, 2, 2, 2, 2, 2], "y"))
    assert stuck.primary.symptom == "stuck_value"
    same = compare_traces(_trace(expected, "y"), _trace(expected, "y"))
    assert same.primary is None and same.passing_outputs == ("y",)


# -------------------------------------------------------------- structure


def test_structure_roles_cone_and_register_distance():
    structure = analyze_rtl(_counter("m", "q", 9, "<"))
    assert structure.parsed
    assert structure.role("q") == "output"
    assert structure.is_register("q") and structure.is_counter("q")
    assert structure.cone("q")["nearest_register_stage"] == 0
    assert structure.cone("wrap")["nearest_register_stage"] == 1
    concat = analyze_rtl(
        "module d(input en, input a, output y1, output y0);\n"
        "  assign {y1, y0} = en ? {a, ~a} : 2'b11;\nendmodule\n"
    )
    assert concat.drivers["y0"][0].kind == "combinational"
    assert concat.drivers["y1"][0].data_deps == {"en", "a"}


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ("(a < 4'd9)", "(a <= 4'd9)", "operator_compare"),
        ("q <= q + 1", "q <= q + 2", "constant_value"),
        ("q <= q + 1", "q <= q - 1", "operator_arith"),
        ("input [3:0] a", "input [2:0] a", "width_or_index"),
        ("state <= next_state", "state = next_state", "assignment_kind"),
        ("0: next_state = 1", "0: next_state = 2", "state_transition"),
        ("if (rst) q <= 0", "if (!rst) q <= 0", "reset_or_enable"),
        ("@(posedge clk or posedge rst)", "@(posedge clk)", "sensitivity_list"),
    ],
)
def test_classify_repair_bug_types(old, new, expected):
    base = """module m(input clk, input rst, input [3:0] a, output reg [3:0] q, output y);
  reg [1:0] state, next_state;
  assign y = (a < 4'd9);
  always @(posedge clk or posedge rst) begin
    if (rst) q <= 0;
    else q <= q + 1;
  end
  always @(posedge clk) state <= next_state;
  always @(*) begin
    case (state)
      0: next_state = 1;
      default: next_state = 0;
    endcase
  end
endmodule
"""
    assert old in base
    assert classify_repair(base, base.replace(old, new))["bug_type"] == expected


# --------------------------------------------------------- boundary checks


def test_episode_rejects_red_labels_golden_and_foreign_repairs(episodes):
    body = episodes[0].to_dict()
    fields = {k: body[k] for k in RepairEpisode.REQUIRED}
    with pytest.raises(KnowledgeValidationError):
        RepairEpisode.create(**{**fields, "provenance": {"mutation_type": "off_by_one"}})
    with pytest.raises(KnowledgeValidationError):
        RepairEpisode.create(**{**fields, "provenance": {"golden_rtl": "module m; endmodule"}})
    repair = dict(fields["verified_repair"], source="reference")
    with pytest.raises(KnowledgeValidationError):
        RepairEpisode.create(**{**fields, "verified_repair": repair})


def test_profiles_and_items_carry_no_design_identifiers(episodes):
    items = DeterministicKnowledgeAuthor().author(episodes)
    text = json.dumps([e.to_dict()["profile"] for e in episodes]) + json.dumps(
        [item.to_dict()["applicability"] for item in items]
        + [item.to_dict()["example"] for item in items]
    )
    for name in ("cnt_alpha", "cnt_beta", "q", "count", "wrap"):
        assert not re.search(rf"\b{name}\b", text), name


# ------------------------------------------------- authoring and transfer


def test_author_generalizes_across_designs(episodes):
    items = DeterministicKnowledgeAuthor().author(episodes)
    assert len(items) == 1
    item = items[0]
    assert item.bug_type == "operator_compare"
    assert item.payload["evidence"]["support"] == 2
    assert item.payload["evidence"]["source_design_clusters"] == ["cnt_alpha", "cnt_beta"]
    # features on which the two sources disagree are dropped from applicability
    assert "delta_bucket" not in item.payload["applicability"]["status"]
    card = item.payload["card"]
    assert any("constant_value" in line for line in card["repair_principles"])
    example = item.payload["example"]
    assert example and any("<" in line for line in example["after"])


def test_matched_knowledge_transfers_to_unseen_design(episodes, unseen_profile):
    items = DeterministicKnowledgeAuthor().author(episodes)
    posterior = BugTypeInference().fit(episodes).posterior(unseen_profile)
    matches = KnowledgeMatcher().match(items, unseen_profile, posterior)
    assert [m.item.item_id for m in matches] == [items[0].item_id]
    assert matches[0].score >= 0.9


def test_matcher_abstains_on_unrelated_failure(episodes):
    items = DeterministicKnowledgeAuthor().author(episodes)
    expected = list(range(8))
    feedback = compare_traces(_trace(expected, "y"), _trace([0] + expected[:-1], "y"))
    comb = "module c(input [3:0] a, output [3:0] y); assign y = a; endmodule\n"
    profile = build_profile(feedback, analyze_rtl(comb))
    posterior = BugTypeInference().posterior(profile)
    assert KnowledgeMatcher().match(items, profile, posterior) == []


def test_type_inference_is_updated_by_experience(episodes, unseen_profile):
    prior = BugTypeInference().posterior(unseen_profile)
    learned = BugTypeInference().fit(episodes).posterior(unseen_profile)
    assert learned["operator_compare"] > prior["operator_compare"]
    assert next(iter(learned)) == "operator_compare"


# -------------------------------------------------- lifecycle and delivery


def test_store_lifecycle_controls_activation(tmp_path, episodes):
    item = DeterministicKnowledgeAuthor().author(episodes)[0]
    store = KnowledgeStore(tmp_path)
    store.add(item)
    assert store.active_items() == []
    with pytest.raises(KnowledgeValidationError):
        store.transition(item.item_id, 1, "active", reason="skip qualification")
    store.transition(item.item_id, 1, "qualified", reason="replay passed")
    store.transition(item.item_id, 1, "active", reason="promoted")
    assert [i.item_id for i in store.active_items()] == [item.item_id]


def test_delivery_modes_share_shape_and_differ_only_in_content(episodes, unseen_profile):
    items = DeterministicKnowledgeAuthor().author(episodes)
    distractor_eps = [
        build_repair_episode(
            case_id="lag_case",
            design_cluster="pipe_delta",
            buggy_rtl="module p(input clk, input [3:0] d, output reg [3:0] y);\n"
                      "  always @(posedge clk) y = d;\nendmodule\n",
            feedback=compare_traces(_trace(list(range(8)), "y"),
                                    _trace([0] + list(range(7)), "y")),
            attempts=[{"verdict_tier": "visible_pass"}],
            passing_candidate_rtl="module p(input clk, input [3:0] d, output reg [3:0] y);\n"
                                  "  always @(posedge clk) y <= d;\nendmodule\n",
        )
    ]
    pool = items + DeterministicKnowledgeAuthor().author(distractor_eps)
    posterior = BugTypeInference().fit(episodes).posterior(unseen_profile)
    matcher = KnowledgeMatcher()
    common = dict(profile=unseen_profile, type_posterior=posterior, pool=pool,
                  matcher=matcher, case_id="timer_gamma", static_items=pool[-1:])
    none = build_bundle("none", **common)
    matched = build_bundle("matched", **common)
    shuffled = build_bundle("shuffled", **common)
    static = build_bundle("static", **common)
    assert none.is_empty
    assert len(matched.items) == len(shuffled.items) == 1
    assert matched.items[0]["knowledge_id"] != shuffled.items[0]["knowledge_id"]
    assert static.items[0]["knowledge_id"] == f"{pool[-1].item_id}@v1"
    assert set(matched.prompt_payload()) == {"usage_note", "items"}
    assert "score" not in json.dumps(matched.prompt_payload())


# ------------------------------------------------- real Blue path delivery


def _injected_client(calls):
    reference = (ROOT / "datasets/cases/strider14/mux_4_1/mux_4_1.v").read_text(encoding="utf-8")

    def transport(**request):
        calls.append(request)
        return {
            "content": json.dumps({
                "replacement_rtl": reference,
                "edit": "restore selector case labels",
            }),
            "input_tokens": 100,
            "output_tokens": 50,
            "provider_request_id": f"injected-{len(calls)}",
        }

    config = OpenAICompatibleClientConfig.from_dict({
        "schema_version": "r3e-openai-compatible-client-config-v1",
        "provider_id": "injected-knowledge-test",
        "provider_version": "1",
        "endpoint_id": "local-test",
        "model_id": "injected-model",
        "model_version": "1",
        "api_key_env": "R3E_TEST_API_KEY",
        "base_url_env": "R3E_TEST_BASE_URL",
        "timeout_seconds": 10,
        "maximum_output_tokens": 4096,
        "temperature": 0,
        "require_seed": True,
    })
    return OpenAICompatibleJSONClient(config, transport=transport, environ={})


def _acp7_with(bundle, tmp_path, name):
    calls = []
    client = KnowledgeInjectingClient(_injected_client(calls))
    row = _load_manifest_case(ROOT / "datasets/manifests/strider14.jsonl", "strider:mux_4_1_1")
    with client.active(bundle):
        _run_acp7(root=ROOT, workspace=tmp_path / name, client=client,
                  manifest_row=row, seed=17)
    evaluation = json.loads((tmp_path / name / "blue_evaluation.json").read_text(encoding="utf-8"))
    receipt = build_delivery_receipt(bundle, evaluation, client.deliveries)
    return calls, evaluation, receipt


@pytest.mark.skipif(not HAS_AUTHORITY_TOOLS, reason="Icarus or Yosys is unavailable")
def test_matched_knowledge_reaches_real_blue_requests_and_is_hash_bound(
    tmp_path, episodes, unseen_profile
):
    items = DeterministicKnowledgeAuthor().author(episodes)
    posterior = BugTypeInference().fit(episodes).posterior(unseen_profile)
    matched = build_bundle("matched", profile=unseen_profile, type_posterior=posterior,
                           pool=items, matcher=KnowledgeMatcher(), case_id="strider:mux_4_1_1")
    none = build_bundle("none", profile=unseen_profile, type_posterior=posterior,
                        pool=items, matcher=KnowledgeMatcher(), case_id="strider:mux_4_1_1")
    calls, evaluation, receipt = _acp7_with(matched, tmp_path, "matched")
    base_calls, base_eval, base_receipt = _acp7_with(none, tmp_path, "none")

    assert len(calls) == len(base_calls) == evaluation["resource_usage"]["provider_calls"] == 3
    for request in calls:
        user = json.loads(request["messages"][-1]["content"])
        assert user["retrieved_repair_knowledge"] == matched.prompt_payload()
    for request in base_calls:
        assert "retrieved_repair_knowledge" not in json.loads(request["messages"][-1]["content"])
    assert verify_delivery_receipt(receipt, evaluation)["bundle_hash"] == matched.bundle_hash
    assert verify_delivery_receipt(base_receipt, base_eval)["bundle_hash"] == none.bundle_hash
    # the knowledge changes the bound request, not the budget
    assert [r["command_hash"] for r in evaluation["candidate_provider_receipts"]] != [
        r["command_hash"] for r in base_eval["candidate_provider_receipts"]
    ]

    forged = dict(receipt, deliveries=base_receipt["deliveries"])
    forged["receipt_hash"] = hash_payload({k: v for k, v in forged.items() if k != "receipt_hash"})
    with pytest.raises(KnowledgeValidationError):
        verify_delivery_receipt(forged, evaluation)


def test_injecting_client_leaves_non_blue_requests_untouched(episodes, unseen_profile):
    calls = []
    client = KnowledgeInjectingClient(_injected_client(calls))
    items = DeterministicKnowledgeAuthor().author(episodes)
    bundle = build_bundle("static", profile=unseen_profile, type_posterior={}, pool=items,
                          matcher=KnowledgeMatcher(), case_id="x", static_items=items)
    red_like = [{"role": "system", "content": "choose"},
                {"role": "user", "content": json.dumps({"allowed_nodes": [0]})}]
    with client.active(bundle):
        client.complete_json(messages=red_like, seed=17)
        with pytest.raises(KnowledgeValidationError):
            with client.active(bundle):
                pass
    assert json.loads(calls[0]["messages"][-1]["content"]) == {"allowed_nodes": [0]}
    assert client.deliveries[0]["injected"] is False


# ------------------------------------------------------------- LLM author


def test_llm_author_is_disabled_without_client_and_validates_output(episodes):
    with pytest.raises(RuntimeError):
        LlmKnowledgeAuthor().author(episodes)

    class FakeClient:
        def __init__(self, result):
            self.result = result
            self.messages = None

        def complete_json(self, *, messages, seed):
            self.messages = messages
            return {"result": self.result}

    card = {
        "mechanism_hypothesis": "A boundary comparison admits one extra value.",
        "causal_chain": ["counter compare feeds the wrap condition"],
        "diagnostic_steps": ["check the compare at the wrap cycle"],
        "repair_principles": ["tighten the comparison"],
        "preservation_constraints": ["keep reset behaviour"],
        "scope_and_limits": ["two sources"],
    }
    client = FakeClient(card)
    items = LlmKnowledgeAuthor(client).author(episodes)
    assert items[0].payload["card"] == card
    assert items[0].payload["author"]["author_id"] == "llm-knowledge-author"
    request_text = json.dumps(client.messages)
    assert "golden" not in request_text and "mutation_type" not in request_text
    with pytest.raises(KnowledgeValidationError):
        LlmKnowledgeAuthor(FakeClient({**card, "diagnostic_steps": "not a list"})).author(episodes)


