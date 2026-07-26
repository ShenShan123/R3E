#!/usr/bin/env python3
import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Set


INCLUDE_RE = re.compile(r'^\s*`include\s+"([^"]+)"', re.M)


def read_jsonl(path: Path) -> List[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def write_jsonl(rows: List[dict], path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for obj in rows:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def extract_includes(path: Path) -> List[str]:
    if not path.exists():
        return []

    txt = path.read_text(errors="ignore")
    out = []
    seen = set()

    for m in INCLUDE_RE.finditer(txt):
        inc = m.group(1)
        if inc not in seen:
            out.append(inc)
            seen.add(inc)

    return out


def design_roots_from_rtl(rtl_files: List[Path]) -> List[Path]:
    roots = []
    seen = set()

    for f in rtl_files:
        p = f.resolve()
        candidates = [
            p.parent,
            p.parent.parent,
            p.parent.parent.parent,
        ]

        for c in candidates:
            if c.exists():
                s = str(c)
                if s not in seen:
                    roots.append(c)
                    seen.add(s)

    return roots


def tokenize_design_name(name: str) -> List[str]:
    generic = {
        "rtl", "src", "source", "sources", "verilog", "bench", "benchmark",
        "external", "benchmarks", "nangate45", "expand", "success",
        "top", "core", "master", "module", "design",
    }
    toks = re.split(r"[^A-Za-z0-9]+", name.lower())
    return [t for t in toks if len(t) >= 2 and t not in generic]


def score_include_candidate(path: Path, design_name: str, rtl_files: List[Path]) -> int:
    s = str(path.resolve()).lower()
    score = 0

    for f in rtl_files:
        parent = str(f.resolve().parent).lower()
        if str(path.resolve().parent).lower() == parent:
            score += 1000

    for token in tokenize_design_name(design_name):
        if token in s:
            score += 100

    if "/rtl/" in s:
        score += 10

    # Avoid common wrong hits for generic files like timescale.v.
    for bad in ["usb_phy", "uart16550", "ethernet", "pci", "vga_lcd", "systemcaes"]:
        if bad in s and bad not in design_name.lower():
            score -= 50

    return score


def find_include_file(
    include_name: str,
    rtl_files: List[Path],
    design_name: str,
    global_roots: List[Path],
) -> Optional[Path]:
    inc_path = Path(include_name)
    inc_base = inc_path.name

    # 1. Same directory as explicit RTL files.
    for f in rtl_files:
        d = f.resolve().parent
        direct = d / include_name
        if direct.exists():
            return direct.resolve()

    # 2. Nearby design roots.
    candidates = []
    for root in design_roots_from_rtl(rtl_files):
        direct = root / include_name
        if direct.exists():
            candidates.append(direct.resolve())

        try:
            for hit in root.rglob(inc_base):
                if hit.is_file():
                    candidates.append(hit.resolve())
        except Exception:
            pass

    # 3. Global roots fallback.
    for root in global_roots:
        root = root.expanduser().resolve()
        if not root.exists():
            continue
        try:
            for hit in root.rglob(inc_base):
                if hit.is_file():
                    candidates.append(hit.resolve())
        except Exception:
            pass

    # Dedup and rank.
    dedup = []
    seen = set()

    for c in candidates:
        s = str(c)
        if s not in seen:
            dedup.append(c)
            seen.add(s)

    if not dedup:
        return None

    dedup.sort(
        key=lambda p: score_include_candidate(p, design_name, rtl_files),
        reverse=True,
    )
    return dedup[0]


def safe_copy(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def unique_destination(dst_dir: Path, src: Path, used_names: Set[str]) -> Path:
    base = src.name

    if base not in used_names:
        used_names.add(base)
        return dst_dir / base

    stem = src.stem
    suffix = src.suffix
    i = 1

    while True:
        cand = f"{stem}_{i}{suffix}"
        if cand not in used_names:
            used_names.add(cand)
            return dst_dir / cand
        i += 1


def materialize_design(obj: dict, work_root: Path, global_roots: List[Path]) -> dict:
    design_name = obj["design_name"]
    out_dir = work_root / design_name / "rtl"

    if out_dir.exists():
        shutil.rmtree(out_dir)

    out_dir.mkdir(parents=True, exist_ok=True)

    original_rtl_files = [Path(x) for x in obj.get("rtl_files", [])]

    used_names = set()
    materialized_rtl_files = []
    original_to_materialized = {}

    # Copy explicit RTL files.
    for src in original_rtl_files:
        dst = unique_destination(out_dir, src, used_names)
        safe_copy(src, dst)
        materialized_rtl_files.append(str(dst))
        original_to_materialized[str(src)] = str(dst)

    # Recursively materialize include closure.
    copied_includes = []
    missing_includes = []
    visited_include_names = set()
    scan_queue = list(original_rtl_files)

    while scan_queue:
        src_file = scan_queue.pop(0)

        for inc in extract_includes(src_file):
            if inc in visited_include_names:
                continue

            visited_include_names.add(inc)

            hit = find_include_file(
                include_name=inc,
                rtl_files=original_rtl_files,
                design_name=design_name,
                global_roots=global_roots,
            )

            if hit is None:
                missing_includes.append(inc)
                continue

            # Preserve simple include basename in local RTL dir.
            # This supports common `include "foo.vh"` and `include "foo.v"` patterns.
            dst = out_dir / Path(inc).name
            safe_copy(hit, dst)

            copied_includes.append({
                "include": inc,
                "source": str(hit),
                "materialized": str(dst),
            })

            # Scan copied include for nested includes.
            scan_queue.append(hit)

    new_obj = dict(obj)
    new_obj["rtl_files"] = materialized_rtl_files
    new_obj["status"] = "frontend_backend_materialized"
    new_obj["frontend_materialized"] = True
    new_obj["frontend_materialized_dir"] = str(out_dir)
    new_obj["frontend_materialized_original_to_materialized"] = original_to_materialized
    new_obj["frontend_materialized_includes"] = copied_includes
    new_obj["frontend_materialized_missing_includes"] = missing_includes

    return new_obj


def main():
    ap = argparse.ArgumentParser("MicroSurgeon frontend→backend handoff materializer")
    ap.add_argument("--input", required=True, help="Input frontend-ready JSONL manifest")
    ap.add_argument("--out-manifest", required=True, help="Output materialized backend JSONL manifest")
    ap.add_argument("--work-root", required=True, help="Materialization work root")
    ap.add_argument(
        "--global-root",
        action="append",
        default=["/path/to/rtl-data"],
        help="Global RTL root used for include fallback search. Can be repeated.",
    )
    ap.add_argument("--report", default=None, help="Optional JSON report path")
    args = ap.parse_args()

    input_path = Path(args.input)
    out_manifest = Path(args.out_manifest)
    work_root = Path(args.work_root)
    global_roots = [Path(x) for x in args.global_root]

    rows = read_jsonl(input_path)
    materialized = []
    report = []

    for obj in rows:
        new_obj = materialize_design(obj, work_root, global_roots)
        materialized.append(new_obj)

        rep = {
            "design_name": obj["design_name"],
            "input_files": len(obj.get("rtl_files", [])),
            "materialized_files": len(new_obj.get("rtl_files", [])),
            "copied_includes": len(new_obj.get("frontend_materialized_includes", [])),
            "missing_includes": new_obj.get("frontend_materialized_missing_includes", []),
            "materialized_dir": new_obj.get("frontend_materialized_dir"),
        }
        report.append(rep)

        print(
            f"{obj['design_name']}: "
            f"rtl={rep['materialized_files']} "
            f"includes={rep['copied_includes']} "
            f"missing={len(rep['missing_includes'])}"
        )

        for inc in new_obj.get("frontend_materialized_includes", []):
            print(f"  include {inc['include']} <- {inc['source']}")

        for miss in rep["missing_includes"]:
            print(f"  MISSING include {miss}")

    write_jsonl(materialized, out_manifest)
    print(f"\nwritten materialized manifest: {out_manifest}")

    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(f"written report: {report_path}")


if __name__ == "__main__":
    main()
