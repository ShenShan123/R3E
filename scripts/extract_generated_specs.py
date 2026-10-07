#!/usr/bin/env python3
"""Attach pinned upstream descriptions to an existing generated carrier corpus.

No model calls or RTL/testbench regeneration. All inputs are validated before
writing; unknown HDL snippets fail closed instead of losing task requirements.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.convert_chipbench import validate_spec


# Repair-task prompts describe a different faulty RTL, not the clean carrier
# that Red mutates. Keep their source records, but exclude them from the pool.
INELIGIBLE_PROBLEMS = {
    "Prob062_bugs_mux2", "Prob123_bugs_addsubz", "Prob132_always_if2",
}
INELIGIBLE_REASON = (
    "spec_task_mismatch: upstream specification describes an existing buggy implementation "
    "to repair, rather than the clean carrier mutated by Red"
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# These adaptations refer only to the upstream prompt, never the reference RTL.
# Hash guards require explicit review if an upstream description changes.
REVIEWED_HASHES = {
    "Prob043_vector5": "0ef3f6be0cd42d8fe1d4a6c8d1a4b6972862a4a0833afcf85f81f8e33b01d2ce",
    "Prob062_bugs_mux2": "66ab950ad70efc33e0599cf07644a4fabd6cf4fc2b90c91099ce229ec85c0415",
    "Prob078_dualedge": "7af1e9009de3c8b5433bf3dcfa3dca917f3a0dbc710d4b9d2a17840e8fc2896c",
    "Prob104_mt2015_muxdff": "1827a64a8a8125705bece7354b0b1d0c736a08d1e4459d85a84db47d8db91325",
    "Prob123_bugs_addsubz": "104c8b48752ca525a21220ee6fb2bfad62c6a9b42bf9d1aed3b8810106b089cb",
    "Prob132_always_if2": "8a3ea8c732f21239cd4141606628d08ad4115da6ed4c16cc2b6ac41f68311cf7",
}
MODULE_PROSE = {
    "Prob062_bugs_mux2": (
        "The supplied buggy example is named TopModule. Its inputs are sel (one bit), "
        "a (8 bits), and b (8 bits); the example declares out as one bit. "
        "It drives out with the bitwise OR of two terms: the bitwise complement of "
        "sel AND a, and sel AND b. This describes the supplied faulty implementation, "
        "not a requirement to preserve its output width or expression."
    ),
    "Prob104_mt2015_muxdff": (
        "The parent full_module has inputs r (3 bits), L (one bit), and clk (one bit), "
        "and a registered output q (3 bits). On each rising edge of clk, when L is "
        "asserted all three bits of q load r. Otherwise, the next q[2] is the XOR "
        "of the old q[1] and q[2], the next q[1] is the old q[0], and the next "
        "q[0] is the old q[2]. All three updates use the values before the edge."
    ),
    "Prob123_bugs_addsubz": (
        "The supplied buggy example uses Verilog-2001 and is named TopModule. "
        "Its inputs are do_sub (one bit), a (8 bits), and b (8 bits); its outputs "
        "are out (8 bits) and result_is_zero (one bit). In its combinational "
        "process, do_sub equal to 0 selects a plus b, and do_sub equal to 1 "
        "selects a minus b, storing the result in out. It then sets result_is_zero "
        "to 1 if the bitwise complement of out is nonzero, with no assignment "
        "to result_is_zero in the other case. This describes the supplied faulty "
        "implementation, not the intended zero-flag behavior."
    ),
    "Prob132_always_if2": (
        "The supplied buggy example is named TopModule. Its one-bit inputs are "
        "cpu_overheated, arrived, and gas_tank_empty; its one-bit outputs are "
        "shut_off_computer and keep_driving. One combinational process sets "
        "shut_off_computer to 1 when cpu_overheated is asserted, with no "
        "assignment otherwise. Another combinational process, when arrived "
        "is 0, sets keep_driving to the bitwise complement of gas_tank_empty, "
        "with no assignment when arrived is 1. This describes the supplied "
        "faulty implementation; the missing assignments are not requirements "
        "to retain the previous output values."
    ),
}


def extract_description(raw: bytes, source: dict, top: str) -> tuple[str, list[dict]]:
    text = raw.decode("utf-8")
    edits = []
    problem = source.get("problem") if source["dataset"] == "verilog-eval-v2" else None
    if problem in REVIEWED_HASHES:
        if sha256(raw) != REVIEWED_HASHES[problem]:
            raise ValueError(f"{problem}: upstream prompt changed; prose adaptation needs review")
        if problem == "Prob043_vector5":
            pattern = r"out\[24\][\s\S]*?out\[ 0\] = ~e \^ e\."
            replacement = (
                "The output bits, from out[24] down to out[0], compare the following "
                "pairs in order: " + ", ".join(f"{a} with {b}" for a in "abcde" for b in "abcde") + "."
            )
        elif problem == "Prob078_dualedge":
            pattern = r"using an\nalways @\(posedge clk or negedge clk\) is not accepted as a legal\nsensitivity list"
            replacement = "using a single procedural sensitivity list containing both rising\nand falling clock edges is not accepted as legal"
        else:
            pattern = r"(?m)^  (?:synthesis[^\n]*\n  )?module\b[\s\S]*?\bendmodule"
            replacement = MODULE_PROSE[problem]
        matches = list(re.finditer(pattern, text))
        if len(matches) != 1:
            raise ValueError(f"{problem}: expected exactly one reviewed code segment")
        match = matches[0]
        edits.append({"kind": "hdl_to_prose", "source_start": match.start(),
                      "source_end": match.end(), "original": match[0], "replacement": replacement})
        text = text[:match.start()] + replacement + text[match.end():]
    if source["dataset"] == "rtllm-v2":
        names = re.findall(r"Module name:\s*([A-Za-z_]\w*)", text)
        if len(names) != 1:
            raise ValueError("RTLLM description must identify exactly one module name")
        if names[0] != top:
            note = (f"Corpus interface note: the upstream description names the module {names[0]}. "
                    f"In this corpus its top-level module is named {top}. "
                    "Use this corpus name for the top-level module; the original behavioral "
                    "description above still applies.")
            text += "\n\n" + note + "\n"
            edits.append({"kind": "top_module_note", "original_module": names[0], "corpus_module": top})
    validate_spec(text)
    return text, edits


def source_path(source: dict) -> str:
    if source["dataset"] == "verilog-eval-v2":
        path = PurePosixPath("dataset_spec-to-rtl") / (source["problem"] + "_prompt.txt")
    elif source["dataset"] == "rtllm-v2":
        path = PurePosixPath(source["path"]).parent / "design_description.txt"
    else:
        raise ValueError(f"unsupported dataset: {source['dataset']}")
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe upstream path: {path}")
    return str(path)


def pinned_description(root: Path, commit: str, path: str) -> bytes:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("source commit must be a full Git object ID")
    proc = subprocess.run(["git", "-C", str(root), "show", f"{commit}:{path}"],
                          capture_output=True, timeout=30)
    if proc.returncode:
        raise ValueError(f"cannot read pinned description {commit}:{path}")
    return proc.stdout


def repo_path(root: Path, value: str) -> Path:
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"path is outside corpus repository: {value}")
    return path


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".spec-", delete=False) as handle:
        temp = Path(handle.name)
        handle.write(data)
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def attach_specs(manifest: Path, roots: dict[str, Path], *, repo: Path = ROOT,
                 overwrite: bool = False) -> dict:
    original = manifest.read_bytes()
    rows = [json.loads(line) for line in original.decode("utf-8").splitlines() if line.strip()]
    if not rows:
        raise ValueError("empty carrier manifest")
    updated = copy.deepcopy(rows)
    prepared = {}
    preserved = {}
    audit = []
    seen = set()
    for row in updated:
        ident = row["carrier_id"]
        if ident in seen:
            raise ValueError(f"duplicate carrier ID: {ident}")
        seen.add(ident)
        source = row["source"]
        if source["dataset"] == "verilog-eval-v2" and source.get("problem") in INELIGIBLE_PROBLEMS:
            row["eligible"] = False
            row["ineligible_reason"] = INELIGIBLE_REASON
        else:
            row.setdefault("eligible", True)
        upstream = source_path(source)
        raw = pinned_description(roots[source["dataset"]], source["commit"], upstream)
        spec, edits = extract_description(raw, source, row["top_module"])
        target = repo_path(repo, row["clean_rtl"]).parent / "spec.txt"
        relative = target.relative_to(repo.resolve()).as_posix()
        data = spec.encode("utf-8")
        if target in prepared:
            raise ValueError(f"multiple carriers share a spec destination: {relative}")
        old_spec = row.get("spec")
        if old_spec and repo_path(repo, old_spec) != target:
            raise ValueError(f"{ident}: existing spec uses another path")
        if target.exists() and target.read_bytes() != data and not overwrite:
            raise ValueError(f"{relative} differs; review then use --overwrite")
        files = row.setdefault("files", [])
        spec_entries = []
        for entry in files:
            path = repo_path(repo, entry["path"])
            if path == target:
                spec_entries.append(entry)
                continue
            actual = sha256(path.read_bytes())
            if entry["sha256"] != actual:
                raise ValueError(f"file hash mismatch: {entry['path']}")
            preserved[path] = actual
        if len(spec_entries) > 1:
            raise ValueError(f"{ident}: duplicate spec hash entries")
        spec_entry = spec_entries[0] if spec_entries else {}
        spec_entry.update(path=relative, sha256=sha256(data))
        if not spec_entries:
            files.append(spec_entry)
        row["spec"] = relative
        row["spec_source"] = {"path": upstream, "commit": source["commit"],
                              "sha256": sha256(raw),
                              "adaptations": [e["kind"] for e in edits]}
        prepared[target] = data
        audit.append({"carrier_id": ident, "dataset": source["dataset"], "spec": relative,
                      "eligible": row["eligible"],
                      **({"ineligible_reason": row["ineligible_reason"]} if row.get("ineligible_reason") else {}),
                      "source": row["spec_source"], "spec_sha256": sha256(data), "edits": edits})
    # Validate the whole input before writing any specifications or manifest.
    if manifest.read_bytes() != original:
        raise RuntimeError("manifest changed during extraction; retry against the new version")
    for path, expected in preserved.items():
        if sha256(path.read_bytes()) != expected:
            raise RuntimeError(f"corpus file changed during extraction: {path.relative_to(repo)}")
    for path, data in prepared.items():
        if not path.exists() or path.read_bytes() != data:
            atomic_write(path, data)
    if manifest.read_bytes() != original:
        raise RuntimeError("manifest changed before publication; specs prepared, manifest untouched")
    serialized = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in updated).encode()
    if serialized != original:
        atomic_write(manifest, serialized)
    return {"rows": len(rows), "specs": len(prepared), "preserved_files": len(preserved),
            "eligible": sum(row["eligible"] is not False for row in updated),
            "ineligible": sum(row["eligible"] is False for row in updated),
            "hdl_adaptations": sum(any(e["kind"] == "hdl_to_prose" for e in item["edits"]) for item in audit),
            "module_name_notes": sum(any(e["kind"] == "top_module_note" for e in item["edits"]) for item in audit),
            "manifest_sha256": sha256(serialized), "cases": audit}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "datasets/generated/corpus_v1/carriers.jsonl")
    parser.add_argument("--verilog-eval-root", type=Path, required=True)
    parser.add_argument("--rtllm-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=ROOT / "artifacts/generated_specs/extraction.json")
    parser.add_argument("--overwrite", action="store_true", help="replace differing spec.txt after source validation")
    args = parser.parse_args()
    report = attach_specs(args.manifest, {"verilog-eval-v2": args.verilog_eval_root,
                                         "rtllm-v2": args.rtllm_root}, overwrite=args.overwrite)
    atomic_write(args.report, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode())
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, sort_keys=True))


if __name__ == "__main__":
    main()
