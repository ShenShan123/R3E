from __future__ import annotations

import json
from pathlib import Path
import re
import shutil

import pytest

from r3e.knowledge import (
    BugTypeInference,
    CaseMemoryAuthor,
    KnowledgeInjectingClient,
    KnowledgeMatcher,
    KnowledgeStore,
    KnowledgeValidationError,
    RepairEpisode,
    analyze_rtl,
    build_bundle,
    build_profile,
    build_repair_episode,
    classify_repair,
    compare_traces,
)
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
    items = CaseMemoryAuthor().author(episodes)
    text = json.dumps([e.to_dict()["profile"] for e in episodes]) + json.dumps(
        [item.to_dict()["applicability"] for item in items]
        + [item.to_dict()["example"] for item in items]
    )
    for name in ("cnt_alpha", "cnt_beta", "q", "count", "wrap"):
        assert not re.search(rf"\b{name}\b", text), name


# ------------------------------------------------- authoring and transfer


def test_case_memory_records_causal_chains_without_authored_rules(episodes):
    items = CaseMemoryAuthor().author(episodes)
    assert len(items) == 1
    item = items[0]
    assert item.bug_type == "operator_compare"
    assert item.payload["evidence"]["support"] == 2
    assert item.payload["evidence"]["design_clusters"] == ["cnt_alpha", "cnt_beta"]
    # retrieval keys: features on which the two sources disagree are dropped
    assert "delta_bucket" not in item.payload["applicability"]["status"]
    # the card is only the sources' causal chains, one per design
    card = item.payload["card"]
    assert set(card) == {"cases"} and len(card["cases"]) == 2
    for case in card["cases"]:
        assert set(case) >= {"observed_failure", "fault", "repair", "did_not_work", "verification"}
        assert any("<" in line for line in case["repair"]["fixed_lines"])  # the verified change itself
        assert case["fault"]["faulty_lines"] != case["repair"]["fixed_lines"]
        assert case["identifier_roles"]  # quoted code comes with each name's role
    text = json.dumps(item.to_dict())
    for authored in ("mechanism_hypothesis", "repair_principles", "preservation_constraints",
                     "diagnostic_steps", "Keep reset values"):
        assert authored not in text


def test_matched_knowledge_transfers_to_unseen_design(episodes, unseen_profile):
    items = CaseMemoryAuthor().author(episodes)
    posterior = BugTypeInference().fit(episodes).posterior(unseen_profile)
    matches = KnowledgeMatcher().match(items, unseen_profile, posterior)
    assert [m.item.item_id for m in matches] == [items[0].item_id]
    assert matches[0].score >= 0.9


def test_matcher_abstains_on_unrelated_failure(episodes):
    items = CaseMemoryAuthor().author(episodes)
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
    item = CaseMemoryAuthor().author(episodes)[0]
    store = KnowledgeStore(tmp_path)
    store.add(item)
    assert store.active_items() == []
    with pytest.raises(KnowledgeValidationError):
        store.transition(item.item_id, 1, "active", reason="skip qualification")
    store.transition(item.item_id, 1, "qualified", reason="replay passed")
    store.transition(item.item_id, 1, "active", reason="promoted")
    assert [i.item_id for i in store.active_items()] == [item.item_id]


def test_delivery_modes_share_shape_and_differ_only_in_content(episodes, unseen_profile):
    items = CaseMemoryAuthor().author(episodes)
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
    pool = items + CaseMemoryAuthor().author(distractor_eps)
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
    assert matched.items[0]["reference_id"] != shuffled.items[0]["reference_id"]
    assert static.items[0]["reference_id"] == f"{pool[-1].item_id}@v1"
    fit = matched.items[0]["fit_with_current_failure"]
    assert fit["agrees_on"] and set(fit) == {"agrees_on", "partly_agrees_on", "differs_on"}
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


def test_injecting_client_leaves_non_blue_requests_untouched(episodes, unseen_profile):
    calls = []
    client = KnowledgeInjectingClient(_injected_client(calls))
    items = CaseMemoryAuthor().author(episodes)
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


def test_ternary_condition_in_assign_is_a_control_dependency():
    from r3e.knowledge.structure import analyze_rtl
    sel = analyze_rtl("module m(input a,b,sel,output out); assign out = sel ? b : a; endmodule")
    plain = analyze_rtl("module m(input c,d,output y); assign y = c & d; endmodule")
    assert sel.cone("out")["has_control_dependency"]
    assert not plain.cone("y")["has_control_dependency"]


def test_register_trace_helpers_read_the_cone_the_dump_and_state_names():
    from r3e.knowledge.register_trace import cone_registers, inject_dump, parameter_values, parse_vcd, show, value_at
    rtl = """module m(input clk, input areset, input in, output out);
  parameter A=0, B=2'd1;
  reg [1:0] state, next;
  reg [3:0] unrelated;
  always @(*) begin
    case (state)
      A: next = in ? A : B;
      B: next = in ? B : A;
    endcase
  end
  always @(posedge clk, posedge areset) begin
    if (areset) state <= B;
    else state <= next;
  end
  always @(posedge clk) unrelated <= unrelated + 1;
  assign out = (state == B);
endmodule
"""
    regs, state = cone_registers(rtl, ["out"])
    assert regs == ["state"] and state == {"state"}  # only registers driving the failing output
    assert "$dumpvars(0, state, out); end\nendmodule" in inject_dump(rtl, "m", regs, ["out"])
    vcd = "$var reg 2 ! state [1:0] $end\n$var wire 1 \" out $end\n#0\nb1 !\n1\"\n#10\nb0 !\n0\"\n#15\nbx !\n"
    signals = parse_vcd(vcd)
    assert value_at(signals["state"], 9) == "01" and value_at(signals["state"], 10) == "00"
    assert value_at(signals["out"], 12) == "0" and value_at(signals["state"], 15) == "xx"
    names = parameter_values(rtl)
    assert show("01", names) == "B (01)" and show("01") == "01" and show("x1", names) == "x1"


def test_rename_keeps_ports_parameters_and_connections_and_drops_comments():
    from r3e.knowledge.rename import rename_internal_signals
    src = """module top(input clk, input [3:0] d, output reg [3:0] q);
  parameter W = 4; // width
  reg [W-1:0] acc, tmp; /* block
  comment */
  wire carry;
  sub u0(.carry(carry), .x(acc));
  always @(posedge clk) begin acc <= d + tmp; q <= acc; end
endmodule
"""
    out, mapping = rename_internal_signals(src)
    assert set(mapping) == {"acc", "tmp", "carry"}
    assert "module top(input clk, input [3:0] d, output reg [3:0] q)" in out and "parameter W = 4;" in out
    assert ".carry(" + mapping["carry"] + ")" in out and ".x(" + mapping["acc"] + ")" in out
    assert "width" not in out and "comment" not in out and "acc" not in out.replace(".x", "")
