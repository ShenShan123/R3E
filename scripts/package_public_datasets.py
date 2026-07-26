#!/usr/bin/env python3
"""Build a portable, hash-verified release copy of the public test sets.

The input manifests may contain machine-specific paths. This utility copies
only benchmark inputs required by the frozen evaluators and emits new manifests
whose paths are relative to the release repository. It never copies run logs,
model prompts/responses, candidates, repairs, or aggregate results.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


RELEASE_ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = ("cirfix39", "literature32", "strider14", "rtlfixer50")
EXPECTED_ROWS = {"cirfix39": 39, "literature32": 32, "strider14": 14, "rtlfixer50": 50}
COPY_ROLES = {
    "buggy_rtl", "reference_rtl", "testbench", "published_oracle", "oracle",
    "dependency",
}
SOURCE_INFO = {
    "cirfix39": {
        "project": "CirFix",
        "url": "https://github.com/hammad-a/verilog_repair",
        "license": "MIT",
    },
    "literature32": {
        "project": "CirFix",
        "url": "https://github.com/hammad-a/verilog_repair",
        "license": "MIT",
    },
    "strider14": {
        "project": "Strider",
        "url": "https://github.com/hejy47/Strider",
        "license": "GPL-3.0-only",
        "commit": "ab13ec8861cfe35d67183a40d0da5b4b631d9639",
    },
    "rtlfixer50": {
        "project": "RTLFixer",
        "url": "https://github.com/NVlabs/RTLFixer",
        "license": "MIT",
        "commit": "24ceebd9176d59bf302e935a800fcb46a10c634c",
    },
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def frozen_write(path: Path, payload: bytes) -> None:
    if path.exists():
        if path.read_bytes() != payload:
            raise RuntimeError(f"refusing to overwrite non-identical release file: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def copy_verified(source: Path, target: Path, expected_sha: str | None) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    actual = sha256_file(source)
    if expected_sha and actual != expected_sha:
        raise RuntimeError(f"source hash mismatch: {source}")
    if target.exists():
        if sha256_file(target) != actual:
            raise RuntimeError(f"release file drift: {target}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def source_mapping(
    benchmark: str,
    path: Path,
    *,
    cirfix_root: Path,
    strider_root: Path,
    rtlfixer_root: Path,
) -> Path:
    if benchmark in {"cirfix39", "literature32"}:
        relative = path.resolve().relative_to(cirfix_root.resolve())
        return Path("datasets/cases/cirfix") / relative
    if benchmark == "strider14":
        relative = path.resolve().relative_to(strider_root.resolve())
        return Path("datasets/cases/strider14") / relative
    if benchmark == "rtlfixer50":
        relative = path.resolve().relative_to(rtlfixer_root.resolve())
        return Path("datasets/cases/rtlfixer50") / relative
    raise ValueError(benchmark)


def role_paths(files: list[dict[str, Any]], role: str) -> list[str]:
    return [row["path"] for row in files if row["role"] == role]


def portable_row(
    benchmark: str,
    source_row: dict[str, Any],
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    case_id = str(source_row.get("case_id") or source_row.get("task_id") or "")
    if not case_id:
        raise RuntimeError(f"{benchmark}: missing case id")
    buggy = role_paths(files, "buggy_rtl")
    references = role_paths(files, "reference_rtl")
    testbenches = role_paths(files, "testbench")
    dependencies = role_paths(files, "dependency")
    oracles = role_paths(files, "published_oracle") + role_paths(files, "oracle")
    if len(buggy) != 1:
        raise RuntimeError(f"{benchmark}/{case_id}: expected one buggy RTL")

    row: dict[str, Any] = {
        "schema": "r3e-public-dataset-case-v1",
        "benchmark": "strider" if benchmark == "strider14" else benchmark,
        "dataset_name": benchmark,
        "case_id": case_id,
        "task_id": str(source_row.get("task_id") or case_id),
        "design": str(source_row.get("design") or source_row.get("bug_name") or case_id),
        "frozen_index": int(
            source_row.get("frozen_index")
            or source_row.get("literature_index")
            or 0
        ),
        "eligible": bool(source_row.get("eligible", True)),
        "gate_type": str(
            source_row.get("gate_type")
            or ("compile" if benchmark == "rtlfixer50" else "simulation")
        ),
        "task_type": str(source_row.get("task_type") or "rtl_functional_repair"),
        "top_module": str(source_row.get("top_module") or ""),
        "sim_timeout": float(
            source_row.get("sim_timeout")
            or source_row.get("native_sim_timeout")
            or 20.0
        ),
        "buggy_path": buggy[0],
        "buggy_rtl": buggy[0],
        "buggy_files": buggy,
        "reference_path": references[0] if references else "",
        "golden_rtl": references[0] if references else "",
        "reference_files": references,
        "testbench_path": testbenches[0] if testbenches else "",
        "testbench_files": testbenches,
        "deps": dependencies,
        "oracle_path": oracles[0] if oracles else "",
        "files": files,
        "source": SOURCE_INFO[benchmark],
    }

    # Preserve the minimal Strider evaluator contract without carrying source
    # archive/config paths or historical run output.
    if benchmark == "strider14":
        old_meta = source_row.get("metadata") or {}
        row["metadata"] = {
            "project_dir": str(Path(buggy[0]).parent),
            "src_paths": buggy + dependencies,
            "oracle_output": oracles[0] if oracles else "",
            "sim_output": Path(str(old_meta.get("sim_output") or "output.txt")).name,
            "section": str(old_meta.get("section") or case_id),
            "top_module": str(old_meta.get("top_module") or ""),
            "timeout": str(old_meta.get("timeout") or "20"),
        }
    elif benchmark == "rtlfixer50":
        old_meta = source_row.get("metadata") or {}
        row["metadata"] = {
            "record_index": int(old_meta.get("record_index") or row["frozen_index"]),
            "source_dataset": Path(
                str(old_meta.get("source_dataset") or "verilogeval-syntax.jsonl")
            ).name,
            "compiler_error_present": bool(old_meta.get("compiler_error_present", True)),
            "simulate_error_present": bool(old_meta.get("simulate_error_present", False)),
        }
    else:
        row.update({
            "tb_sources": testbenches + dependencies,
            "published_oracle": oracles[0] if oracles else "",
            "oracle_command": "Icarus differential against the frozen reference/oracle",
            "compile_command": "iverilog -g2012 <candidate> <dependencies> <testbench>",
            "simulation_command": "vvp <compiled-output>",
        })

    row["upstream_case_sha256"] = str(source_row.get("case_sha256") or "")
    row["case_sha256"] = canonical_hash(row)
    return row


def build_benchmark(
    benchmark: str,
    manifest_dir: Path,
    out_root: Path,
    *,
    cirfix_root: Path,
    strider_root: Path,
    rtlfixer_root: Path,
) -> tuple[list[dict[str, Any]], int]:
    source_rows = read_jsonl(manifest_dir / f"{benchmark}.jsonl")
    if len(source_rows) != EXPECTED_ROWS[benchmark]:
        raise RuntimeError(f"{benchmark}: row count drift")
    output: list[dict[str, Any]] = []
    copied: set[str] = set()
    for source_row in source_rows:
        portable_files = []
        for file_row in source_row.get("files", []):
            role = str(file_row.get("role") or "")
            if role not in COPY_ROLES:
                continue
            source = Path(str(file_row["path"]))
            relative = source_mapping(
                benchmark, source, cirfix_root=cirfix_root,
                strider_root=strider_root, rtlfixer_root=rtlfixer_root,
            )
            target = out_root.parent / relative
            copy_verified(source, target, str(file_row.get("sha256") or "") or None)
            copied.add(relative.as_posix())
            portable_files.append({
                "path": relative.as_posix(),
                "role": role,
                "sha256": sha256_file(target),
                "size_bytes": target.stat().st_size,
            })
        output.append(portable_row(benchmark, source_row, portable_files))
    output.sort(key=lambda row: (row["frozen_index"], row["case_id"]))
    manifest = out_root / "manifests" / f"{benchmark}.jsonl"
    payload = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in output
    ).encode()
    frozen_write(manifest, payload)
    return output, len(copied)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest-dir", type=Path, required=True)
    parser.add_argument("--cirfix-root", type=Path, required=True)
    parser.add_argument("--strider-root", type=Path, required=True)
    parser.add_argument("--rtlfixer-root", type=Path, required=True)
    parser.add_argument("--cirfix-license", type=Path, required=True)
    parser.add_argument("--strider-license", type=Path, required=True)
    parser.add_argument("--rtlfixer-license", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, default=RELEASE_ROOT / "datasets")
    args = parser.parse_args()
    out_root = args.out_root.resolve()
    summary = {}
    for benchmark in BENCHMARKS:
        rows, copied = build_benchmark(
            benchmark, args.manifest_dir.resolve(), out_root,
            cirfix_root=args.cirfix_root.resolve(),
            strider_root=args.strider_root.resolve(),
            rtlfixer_root=args.rtlfixer_root.resolve(),
        )
        summary[benchmark] = {
            "rows": len(rows),
            "unique_input_files": copied,
            "manifest_sha256": sha256_file(
                out_root / "manifests" / f"{benchmark}.jsonl"
            ),
        }

    licenses = {
        "CirFix-MIT.txt": args.cirfix_license,
        "Strider-GPL-3.0.txt": args.strider_license,
        "RTLFixer-MIT.txt": args.rtlfixer_license,
    }
    for name, source in licenses.items():
        frozen_write(out_root / "licenses" / name, source.read_bytes())
    frozen_write(
        out_root / "DATASET_INDEX.json",
        (json.dumps({
            "schema": "r3e-public-dataset-index-v1",
            "contains_experiment_results": False,
            "contains_model_outputs": False,
            "benchmarks": summary,
        }, indent=2, sort_keys=True) + "\n").encode(),
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
