"""No-prediction Red generation from a frozen public observation bank."""
from __future__ import annotations

import os
import time

from .blue_provider import apply_text_edits, BlueOutputViolation
from .red_design import validate_design, actual_diff, RedDesignOutputViolation
from .red_regression import save_new
from r3e.protocol.hashing import canonical_json, hash_payload
from r3e.providers.openai_compatible import OpenAICompatibleProviderViolation

VERSION = "continuous_red_v3"
FOCUS_FIELDS = {"version", "carrier_id", "mechanism", "selection", "seed_edits", "history", "provenance"}
TARGET_FIELDS = {"version", "snapshot_id", "committed_round", "snapshot_hash", "state_hash",
                 "repair_budget", "learning_policy", "retrieval_policy", "eligible_entry_count",
                 "spec_selected_experience"}
TARGET_TOKEN_CAP = 6144
SYSTEM = """Generate one coherent defective RTL implementation from the clean carrier and unchanged spec.
Use actual prior public repair observations to challenge the incoming Blue state. You may revisit an
unresolved behavior, test a previously repaired mechanism with a new case, or explore uncovered behavior.
Public passes are not proofs of correctness; single failures are unconfirmed observations, not stable weaknesses.
Blue may now have additional experience. Do not assume a previous failure still applies to its current state.
The target_blue view states the actual incoming repair budget, update/retrieval policy and spec-selected
experience. Challenge what that state has not demonstrated it can handle. Its entries are provisional
public-passing episodes, not verified general rules. The view is not a prediction of Blue's next patch.
Return one JSON object: {"edits":[{"find":"unique clean RTL text","replace":"mutated text"}],
"violated_requirement":"brief spec requirement","spec_basis":"short spec citation"}, or {"abstain":"reason"}.
Preserve interfaces, parameters, dependencies, directives and all comments verbatim and in order.
Stay within the supplied edit budget. The mutant must compile, fail W_bug and pass W_preserve.
Use linked changes where useful; more changed code is not a reward. Start from clean RTL, not an old mutant.
When mechanism_focus is supplied, use its historical seed as a starting example, not a current failure
claim. Explore new variants of that mechanism and use the real previous-round observations to choose
the next challenge. Do not merely reproduce an earlier mutant or change formatting. Preserve the
unchanged spec and public checks. Initial source selection is human work, not automatic discovery.
Do not output a hypothetical Blue patch, guessed reasoning, an entire file, or extra fields.
"""
OBSERVATION_FIELDS = {"round", "slot_id", "carrier_id", "target_snapshot_hash", "mutant_hash",
                      "mutation_edits", "blue_patch", "public_rows", "public_evidence", "label", "source_hash"}


def validate_focus(focus, public):
    if focus is None:
        return
    if (not isinstance(focus, dict) or set(focus) != FOCUS_FIELDS or focus["version"] != "continuous_mechanism_seed_v1"
            or focus["carrier_id"] != public["carrier_id"] or focus["selection"] != "human_selected_development_seed"
            or not isinstance(focus["mechanism"], str) or not focus["mechanism"].strip()
            or set(focus["history"]) != {"challenge_id", "runs", "unrepaired", "label"}
            or focus["history"]["label"] != "historical_budgeted_failure_not_current_confirmation"):
        raise ValueError("invalid mechanism focus or historical evidence boundary")
    if (any(type(focus["history"][k]) is not int for k in ("runs", "unrepaired"))
            or not 0 <= focus["history"]["unrepaired"] <= focus["history"]["runs"]
            or focus["history"]["runs"] < 1):
        raise ValueError("invalid historical run counts")
    mutant = apply_text_edits(public["clean_rtl"], focus["seed_edits"])
    validate_design(public["clean_rtl"], mutant, public["top_module"])
    if (set(focus["provenance"]) != {"clean_hash", "seed_hash", "record_hashes"}
            or focus["provenance"]["clean_hash"] != hash_payload(public["clean_rtl"])
            or focus["provenance"]["seed_hash"] != hash_payload(mutant)
            or not focus["provenance"]["record_hashes"]):
        raise ValueError("mechanism seed provenance mismatch")


def materialize(raw, public):
    if isinstance(raw, dict) and set(raw) == {"abstain"} and isinstance(raw["abstain"], str) and raw["abstain"].strip():
        return {"status": "abstained", "reason": raw["abstain"]}
    if (not isinstance(raw, dict) or set(raw) != {"edits", "violated_requirement", "spec_basis"}
            or any(not isinstance(raw[k], str) or not raw[k].strip() for k in ("violated_requirement", "spec_basis"))):
        return {"status": "rejected", "reason": "output_schema"}
    try:
        mutant = apply_text_edits(public["clean_rtl"], raw["edits"])
        budget = validate_design(public["clean_rtl"], mutant, public["top_module"])
    except (BlueOutputViolation, RedDesignOutputViolation) as exc:
        return {"status": "rejected", "reason": str(exc)}
    return {"status": "proposal", "buggy_rtl": mutant, "buggy_rtl_hash": hash_payload(mutant),
            "edit_budget": budget, "diff": actual_diff(public["clean_rtl"], mutant),
            "claims": {k: raw[k] for k in ("violated_requirement", "spec_basis")}, "mutation_edits": raw["edits"]}


def observation_bank(observations, round_index, counter, cap=8192):
    """Most recent prior public observations first, whole records only; omissions stay audit-only."""
    bank, omissions = [], []
    for obs in reversed(observations):
        if set(obs) != OBSERVATION_FIELDS or not 0 < obs["round"] < round_index:
            raise ValueError("Red observation schema or round boundary mismatch")
        if counter.count(canonical_json([*bank, obs])) > cap:
            omissions.append({"source_hash": obs["source_hash"], "reason": "bank_token_limit"})
        else:
            bank.append(obs)
    return bank, omissions


def propose(public, observations, *, round_index, snapshot_hash, red_state_hash, seed,
            directory, client, counter, target_blue, target_omissions=(),
            context_tokens=131072, output_tokens=65536, mechanism_focus=None):
    validate_focus(mechanism_focus, public)
    if (set(target_blue) != TARGET_FIELDS or target_blue["version"] != "continuous_red_target_v1"
            or target_blue["snapshot_hash"] != snapshot_hash
            or target_blue["committed_round"] != round_index - 1
            or target_blue["snapshot_id"] != f"B_{round_index - 1}"):
        raise ValueError("Red target state does not match incoming Blue snapshot")
    directory.mkdir(parents=True, exist_ok=False)
    bank, omissions = observation_bank(observations, round_index, counter)
    payload = {"carrier": public, "target_blue": target_blue, "prior_public_observations": bank}
    if mechanism_focus is not None:
        payload["mechanism_focus"] = mechanism_focus
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": canonical_json(payload)}]
    size = counter.request(messages)
    record = {"version": VERSION, "round": round_index, "target_snapshot_hash": snapshot_hash,
              "red_state_hash": red_state_hash, "messages": messages, "request_hash": hash_payload(messages),
              "bank_bytes": canonical_json(bank), "bank_hash": hash_payload(bank), "omissions": omissions,
              "target_view_hash": hash_payload(target_blue), "target_omissions": list(target_omissions),
              "seed": seed, "counted_request_tokens": size, "output_tokens": output_tokens}
    # Exclusive durable record must precede any provider request.
    with (directory / "request.json").open("x") as f:
        f.write(canonical_json(record))
        f.flush()
        os.fsync(f.fileno())
    if counter.count(canonical_json(target_blue)) > TARGET_TOKEN_CAP:
        result = {"status": "request_too_large", "reason": "red_target_state_limit"}
    elif size + output_tokens > context_tokens:
        result = {"status": "request_too_large", "reason": "red_context_limit"}
    else:
        try:
            start = time.monotonic()
            with client.in_phase("red"):
                response = client.complete_json(messages=messages, seed=seed, maximum_output_tokens=output_tokens)
            save_new(directory / "response.json", {**response, "model_seconds": time.monotonic() - start})
            result = ({"status": "no_answer", "reason": "output_limit"}
                      if response.get("response_diagnostics", {}).get("finish_reason") == "length"
                      else materialize(response["result"], public))
        except OpenAICompatibleProviderViolation as exc:
            diag = getattr(exc, "diagnostics", {}) or {}
            result = {"status": "no_answer" if diag.get("finish_reason") == "length" else "provider_failure",
                      "reason": type(exc).__name__, "diagnostics": diag}
    save_new(directory / "result.json", result)
    return result
