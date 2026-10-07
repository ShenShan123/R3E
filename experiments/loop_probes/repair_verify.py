"""Supplementary verification of Blue's repairs: passing the visible test vs matching the reference.

A repair that passes the visible test can still differ from the reference
design (a residual error the test does not exercise). For every Blue candidate
that passed the visible test, this compares it with the reference (the
carrier's clean design) in two ways, with no model calls:

- ``random_simulation``: both designs under the same generated random-stimulus
  testbench, several seeds; input constraints: reset held active for 2 cycles,
  then every input drawn uniformly at random each cycle (no protocol
  constraints). Scope: seeds x cycles.
- ``bounded_equivalence``: a Yosys miter, ``sat -seq K`` from an all-zero
  state with reset asserted in the first step, all inputs free afterwards,
  reference X outputs ignored. Single-clock abstraction (one step = one active
  clock edge; asynchronous resets made synchronous). Scope: K steps; this is a
  bounded check, not a full equivalence proof.

Each record keeps two separate things:
- ``original_result``: the attempt's result under the visible test and budget,
  copied unchanged;
- ``supplementary``: ``mismatch_found`` / ``no_mismatch_found`` /
  ``incomplete_or_not_applicable``, with per-method scope, outcome and, for a
  mismatch, a reproducible counterexample (seed and cycle, or the input
  sequence) and the hashes of both designs.

A mismatch is not by itself a Blue error: the counterexample may violate the
design's real input protocol, or the reference may deviate from its spec (as
found for several ChipBench references). Every mismatch is therefore marked
``review: needed``. Results never change hit counts, and the output file is not
a run ledger: nothing in the loop or in Red's view reads it.

Candidates come from the run's ``public_repair_candidates`` store (hash
checked); ``--candidate CHALLENGE_ID=PATH`` supplies a file for older runs, and
it must match the hash the run recorded for a passing attempt.

Usage: python -m experiments.loop_probes.repair_verify STATE_DIR [--manifests M.jsonl ...]
           [--seeds 11 23 37] [--cycles 1000] [--depth 20] [--candidate CID=PATH ...]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path

from r3e.knowledge.feedback import compare_traces
from r3e.loop.carriers import (ACTIVE_LOW, DEFAULT_CARRIER_MANIFEST, REPO_ROOT, detect_clock_reset,
                               generate_testbench, load_carrier_manifest, yosys_ports)
from r3e.loop.corpus import load_public_manifest
from r3e.loop.red_repair_feedback import read_public_candidate
from r3e.loop.sim import Simulator
from r3e.loop.state import RunState
from r3e.protocol.hashing import hash_payload

VERSION = "repair_verify_v1"
PASSING = {"visible_pass", "hidden_pass", "formal_pass"}
CONSTRAINTS = ("reset held active for 2 cycles, then released; every other input drawn uniformly at random "
               "each cycle; no protocol constraints")


def _reset_active(reset: str) -> int:
    return 0 if ACTIVE_LOW.search(reset) and not reset.lower().endswith("en") else 1


def random_simulation(ref: str, cand: str, carrier, sim: Simulator, *, seeds, cycles, work: Path) -> dict:
    scope = {"seeds": list(seeds), "cycles_per_seed": cycles, "input_constraints": CONSTRAINTS}
    try:
        (work / "ref.sv").write_text(ref)
        ports = yosys_ports(work / "ref.sv", carrier.top_module, work / "ports")
        clock, reset = detect_clock_reset(ref, ports)
    except Exception as exc:  # noqa: BLE001 - reported, never guessed
        return {"method": "random_simulation", "scope": scope, "outcome": "not_applicable",
                "reason": f"ports/clock not determined: {str(exc)[:150]}"}
    scope.update(clock=clock, reset=reset)
    for seed in seeds:
        tb = work / f"tb_{seed}.v"
        tb.write_text(generate_testbench(top=carrier.top_module, ports=ports, clock=clock, reset=reset, seed=seed,
                                         cycles=cycles, output_name="trace.txt", stimulus_name="stimulus.txt"))
        expected, err_e = sim.run(ref, carrier, (tb,))
        observed, err_o = sim.run(cand, carrier, (tb,))
        if expected is None or observed is None:
            return {"method": "random_simulation", "scope": scope, "outcome": "incomplete",
                    "reason": f"simulation failed: {(err_e or err_o)[:150]}"}
        divergences = compare_traces(expected, observed).divergences
        if divergences:
            first = min(divergences, key=lambda d: d.first_cycle)
            return {"method": "random_simulation", "scope": scope, "outcome": "mismatch",
                    "counterexample": {"seed": seed, "cycle": first.first_cycle, "signal": first.signal,
                                       "reference": first.expected, "candidate": first.observed,
                                       "testbench_hash": hash_payload(tb.read_text()),
                                       "reproduce": "same testbench generator, seed and cycle count"}}
    return {"method": "random_simulation", "scope": scope, "outcome": "no_mismatch"}


_SELF_CHECK: dict[tuple[str, int], dict] = {}


def bounded_equivalence(ref: str, cand: str, carrier, *, depth: int, work: Path, timeout: int = 180) -> dict:
    """Guarded: the reference must first be equivalent to itself under the same
    abstraction (undefined or latched state can make two copies disagree), else
    the method is not applicable to this design."""
    key = (hash_payload(ref), depth)
    if key not in _SELF_CHECK:
        own = work / "self_check"
        own.mkdir(exist_ok=True)
        _SELF_CHECK[key] = _equivalence(ref, ref, carrier, depth=depth, work=own, timeout=timeout)
    own = _SELF_CHECK[key]
    if own["outcome"] != "no_mismatch_within_bound":
        return {**own, "outcome": "not_applicable", "counterexample": None,
                "reason": "the reference is not equivalent to itself under this abstraction "
                          f"(self-check: {own['outcome']}); e.g. undefined or latched state"}
    return _equivalence(ref, cand, carrier, depth=depth, work=work, timeout=timeout)


def _equivalence(ref: str, cand: str, carrier, *, depth: int, work: Path, timeout: int = 180) -> dict:
    top = carrier.top_module
    scope = {"steps": depth, "start": "all-zero state, reset asserted in step 1", "inputs": "free after step 1",
             "abstraction": "single clock, one step per active edge, asynchronous resets made synchronous",
             "reference_x": "ignored"}
    (work / "ref.sv").write_text(ref)
    (work / "cand.sv").write_text(cand)
    deps = " ".join(str(d) for d in carrier.deps)
    prep = "hierarchy -top {t}; proc; flatten; async2sync; opt_clean"
    try:
        ports = yosys_ports(work / "ref.sv", top, work / "ports")
        _, reset = detect_clock_reset(ref, ports)
    except Exception:  # noqa: BLE001
        reset = None
    setr = f" -set-at 1 in_{reset} {_reset_active(reset)}" if reset else ""
    script = "; ".join([
        f"read_verilog -sv {deps} {work / 'ref.sv'}", prep.format(t=top), f"rename {top} gold", "design -stash gold",
        f"read_verilog -sv {deps} {work / 'cand.sv'}", prep.format(t=top), f"rename {top} gate", "design -stash gate",
        "design -copy-from gold -as gold gold", "design -copy-from gate -as gate gate",
        "miter -equiv -flatten -make_outputs -ignore_gold_x gold gate miter", "hierarchy -top miter",
        f"sat -verify -seq {depth} -set-init-zero{setr} -prove-skip 1 -prove trigger 0 -show-inputs "
        f"-dump_json {work / 'cex.json'} miter"])
    if not reset:
        scope["start"] = "all-zero state (no reset input found)"
    try:
        proc = subprocess.run(["yosys", "-q", "-p", script], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"method": "bounded_equivalence", "scope": scope, "outcome": "timeout"}
    text = proc.stdout + proc.stderr
    if proc.returncode == 0:  # quiet mode prints nothing on success; with -verify a failed proof is an error
        return {"method": "bounded_equivalence", "scope": scope, "outcome": "no_mismatch_within_bound"}
    if "FAIL!" in text or "proof did fail" in text:
        cex = (work / "cex.json").read_text() if (work / "cex.json").exists() else ""
        return {"method": "bounded_equivalence", "scope": scope, "outcome": "mismatch",
                "counterexample": {"input_sequence_wavejson_text": cex[:20000]}}  # raw Yosys output
    return {"method": "bounded_equivalence", "scope": scope, "outcome": "incomplete",
            "reason": re.sub(r"\s+", " ", text[-300:])}


def summarise(methods: list[dict]) -> str:
    """``mismatch_found`` if any method found one; ``no_mismatch_found`` only if every method completed without
    one; ``no_mismatch_found_partial`` if some completed without one and others did not complete (never masked);
    else ``incomplete_or_not_applicable``."""
    outcomes = [m["outcome"] for m in methods]
    clean = [o in ("no_mismatch", "no_mismatch_within_bound") for o in outcomes]
    if "mismatch" in outcomes:
        return "mismatch_found"
    if clean and all(clean):
        return "no_mismatch_found"
    if any(clean):
        return "no_mismatch_found_partial"
    return "incomplete_or_not_applicable"


def passing_attempts(state: RunState) -> list[dict]:
    red = {r["challenge_id"]: r for r in state.read("red") if r.get("admitted")}
    # Seeds deliberately have no admitted-Red entry: importing one there would
    # misrepresent historical validation as a new discovery. Bind their identity
    # to the pilot's frozen provenance, including for older seed encounter rows.
    config_path = state.root.parent / "pilot_config.json"
    seed = json.loads(config_path.read_text()).get("seed", {}) if config_path.is_file() else {}
    out = []
    for row in state.read("encounters"):
        e = row["encounter"]
        is_seed = (row.get("branch") == "seed_validation"
                   and row.get("counts_as_new_hit") is False
                   and e["challenge_id"] == seed.get("challenge_id"))
        if e["challenge_id"] not in red and not is_seed:
            continue
        carrier_id = seed["carrier_id"] if is_seed else red[e["challenge_id"]]["carrier_id"]
        if is_seed and row.get("carrier_id", carrier_id) != carrier_id:
            raise ValueError("seed encounter carrier differs from frozen provenance")
        for a in e["attempts"]:
            if a.get("verdict_tier") in PASSING and a.get("candidate_hash"):
                out.append({"challenge_id": e["challenge_id"], "carrier_id": carrier_id,
                            "run": (f"seed:{row['round']}" if is_seed else
                                    "confirmation" if row.get("confirmation") else "primary"),
                            **({"counts_as_new_hit": False,
                                "frozen_reference_hash": seed.get("clean_rtl_hash")} if is_seed else {}),
                            "attempt": a.get("index"), "candidate_hash": a["candidate_hash"],
                            "original_result": {"verdict": a["verdict_tier"], "hidden": a.get("hidden"),
                                                "solved_within_budget": e["solved_within_budget"]}})
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("state_dir", type=Path)
    p.add_argument("--manifests", nargs="*", type=Path, default=[])
    p.add_argument("--seeds", nargs="+", type=int, default=[11, 23, 37])
    p.add_argument("--cycles", type=int, default=1000)
    p.add_argument("--depth", type=int, default=20)
    p.add_argument("--candidate", action="append", default=[], metavar="CHALLENGE_ID=PATH")
    p.add_argument("--out", type=Path, default=None, help="default: STATE_DIR/repair_verification.jsonl")
    args = p.parse_args()
    carriers = {c.carrier_id: c for c in load_carrier_manifest(DEFAULT_CARRIER_MANIFEST)}
    for manifest in args.manifests:
        carriers.update({c.carrier_id: c for c in load_public_manifest(REPO_ROOT / manifest, REPO_ROOT)[0]})
    supplied = dict(item.split("=", 1) for item in args.candidate)
    state = RunState(args.state_dir)
    out = args.out or args.state_dir / "repair_verification.jsonl"
    records = []
    with tempfile.TemporaryDirectory() as tmp:
        sim = Simulator(Path(tmp) / "sim")
        for i, item in enumerate(passing_attempts(state)):
            carrier = carriers[item["carrier_id"]]
            if item.get("frozen_reference_hash") and item["frozen_reference_hash"] != hash_payload(carrier.clean_rtl):
                raise ValueError("seed reference differs from frozen pilot provenance")
            rtl = read_public_candidate(args.state_dir, item["carrier_id"], item["candidate_hash"])
            if rtl is None and item["challenge_id"] in supplied:
                text = Path(supplied[item["challenge_id"]]).read_text()
                rtl = text if hash_payload(text) == item["candidate_hash"] else None
            record = {**item, "verifier": VERSION, "reference_hash": hash_payload(carrier.clean_rtl)}
            if rtl is None:
                record["supplementary"] = {"status": "incomplete_or_not_applicable",
                                           "reason": "candidate RTL not stored for this run"}
            else:
                work = Path(tmp) / f"c{i}"
                work.mkdir()
                methods = [random_simulation(carrier.clean_rtl, rtl, carrier, sim, seeds=args.seeds,
                                             cycles=args.cycles, work=work),
                           bounded_equivalence(carrier.clean_rtl, rtl, carrier, depth=args.depth, work=work)]
                status = summarise(methods)
                record["supplementary"] = {"status": status, "methods": methods,
                                           **({"review": "needed: check the counterexample against the input "
                                               "protocol and the reference against the spec"}
                                              if status == "mismatch_found" else {})}
            records.append(record)
    out.write_text("".join(json.dumps(r) + "\n" for r in records))
    counts: dict[str, int] = {}
    for r in records:
        counts[r["supplementary"]["status"]] = counts.get(r["supplementary"]["status"], 0) + 1
    print(json.dumps({"passing_attempts": len(records), "status": counts, "out": str(out)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
