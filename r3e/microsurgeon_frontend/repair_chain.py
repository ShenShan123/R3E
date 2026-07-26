#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path


SEMANTIC_CLASSES = {
    "F0_SYNTAX_PARSE_ERROR",
    "F4_WIRE_ASSIGNED_IN_PROCEDURAL_BLOCK",
}

MODULE_CLOSURE_CLASSES = {
    "F1_MODULE_CLOSURE_ERROR",
}

SOURCE_INCOMPLETE_CLASSES = {
    "F0_INCLUDE_NOT_FOUND",
}


def read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_jsonl(rows, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for obj in rows:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def write_json(obj, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False))


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text())


def count_jsonl(path):
    return len(read_jsonl(path))


def run_cmd(cmd, log_path):
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    start = time.time()

    with log_path.open("w") as log:
        log.write("[CMD] " + " ".join(str(x) for x in cmd) + "\n")
        log.write("[START] " + datetime.now().isoformat(timespec="seconds") + "\n\n")
        log.flush()

        proc = subprocess.run(
            [str(x) for x in cmd],
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )

        runtime = time.time() - start
        log.write("\n[END] " + datetime.now().isoformat(timespec="seconds") + "\n")
        log.write(f"[RETURNCODE] {proc.returncode}\n")
        log.write(f"[RUNTIME_S] {runtime:.1f}\n")

    return proc.returncode, runtime


def summarize_frontend(summary_path):
    rows = read_jsonl(summary_path)
    counts = Counter()
    by_name = {}
    pass_names = set()

    for row in rows:
        cls = row.get("failure_class", "UNKNOWN")
        name = row["design_name"]
        counts[cls] += 1
        by_name[name] = row
        if cls == "PASS":
            pass_names.add(name)

    return {
        "total": len(rows),
        "counts": dict(counts),
        "by_name": by_name,
        "pass_names": pass_names,
    }


def count_patch_specs(path):
    obj = read_json(path, default={})

    if isinstance(obj, list):
        return len(obj)

    if isinstance(obj, dict) and "patches" in obj:
        return len(obj["patches"])

    if isinstance(obj, dict) and obj.get("design_name"):
        return 1

    return 0


def build_failure_manifest(input_manifest, frontend_summary, out_manifest):
    src = {obj["design_name"]: obj for obj in read_jsonl(input_manifest)}
    rows = []

    for row in read_jsonl(frontend_summary):
        cls = row.get("failure_class", "UNKNOWN")
        if cls == "PASS":
            continue

        name = row["design_name"]
        if name not in src:
            continue

        obj = dict(src[name])
        obj["repair_chain_initial_failure_class"] = cls
        rows.append(obj)

    write_jsonl(rows, out_manifest)
    return rows


def build_targets_by_classes(source_manifest, frontend_summary, out_manifest, allowed_classes, tag_key):
    src = {obj["design_name"]: obj for obj in read_jsonl(source_manifest)}
    allowed = set(allowed_classes)
    rows = []

    for row in read_jsonl(frontend_summary):
        cls = row.get("failure_class", "UNKNOWN")
        if cls not in allowed:
            continue

        name = row["design_name"]
        if name not in src:
            continue

        obj = dict(src[name])
        obj[tag_key] = cls
        rows.append(obj)

    write_jsonl(rows, out_manifest)
    return rows


def build_deterministic_fallback_targets(failure_manifest, ready_names, out_manifest):
    rows = []

    for obj in read_jsonl(failure_manifest):
        name = obj["design_name"]
        cls = obj.get("repair_chain_initial_failure_class", "UNKNOWN")

        if name in ready_names:
            continue

        if cls in SOURCE_INCOMPLETE_CLASSES:
            continue

        obj = dict(obj)
        obj["repair_chain_deterministic_fallback_class"] = cls
        rows.append(obj)

    write_jsonl(rows, out_manifest)
    return rows


def merge_handoffs_with_priority(sources, out_manifest):
    merged = {}
    origin_counts = Counter()
    duplicate_counts = Counter()

    for origin, path in sources:
        for obj in read_jsonl(path):
            name = obj["design_name"]

            if name in merged:
                duplicate_counts[origin] += 1
                continue

            new_obj = dict(obj)
            new_obj["frontend_repair_chain_origin"] = origin
            merged[name] = new_obj
            origin_counts[origin] += 1

    rows = [merged[name] for name in sorted(merged)]
    write_jsonl(rows, out_manifest)

    return {
        "count": len(rows),
        "origin_counts": dict(origin_counts),
        "duplicate_counts": dict(duplicate_counts),
        "ready_names": set(merged.keys()),
    }


def make_blocked_manifest(failure_manifest, ready_names, out_manifest):
    rows = []

    for obj in read_jsonl(failure_manifest):
        name = obj["design_name"]
        cls = obj.get("repair_chain_initial_failure_class", "UNKNOWN")

        if name in ready_names:
            continue

        reason = "unrecovered"

        if cls in SOURCE_INCOMPLETE_CLASSES:
            reason = "source incomplete guard"
        elif cls in MODULE_CLOSURE_CLASSES:
            reason = "module closure unrecovered"
        elif cls in SEMANTIC_CLASSES:
            reason = "semantic/deterministic unrecovered"

        blocked = dict(obj)
        blocked["blocked_reason"] = reason
        rows.append(blocked)

    write_jsonl(rows, out_manifest)
    return rows


def write_report(report, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = []

    lines.append("# Frontend Repair Chain v0 Report")
    lines.append("")
    lines.append("## Result")
    lines.append("")
    lines.append(report["result"])
    lines.append("")
    lines.append("## Policy")
    lines.append("")
    lines.append(f"`{report['policy']}`")
    lines.append("")
    lines.append("## Flow")
    lines.append("")
    lines.append("```text")
    lines.append("raw manifest")
    lines.append("→ frontend initial gate")
    lines.append("→ source-incomplete guard")
    lines.append("→ module-closure repair")
    lines.append("→ semantic patch-spec repair")
    lines.append("→ deterministic fallback repair")
    lines.append("→ merge frontend-ready manifest")
    lines.append("→ handoff materializer")
    lines.append("```")
    lines.append("")
    lines.append("## Input")
    lines.append("")
    lines.append(f"- Input manifest: `{report['input_manifest']}`")
    lines.append(f"- Input designs: {report['input_count']}")
    lines.append("")
    lines.append("## Initial Frontend Gate")
    lines.append("")
    lines.append(f"- Initial total: {report['initial_total']}")
    lines.append("")
    lines.append("| Class | Count |")
    lines.append("|---|---:|")
    for cls, count in sorted(report["initial_counts"].items()):
        lines.append(f"| {cls} | {count} |")
    lines.append("")
    lines.append(f"- Initial PASS: {report['initial_pass_count']}")
    lines.append(f"- Initial failures: {report['initial_failure_count']}")
    lines.append("")
    lines.append("## Repair Path Summary")
    lines.append("")
    lines.append("| Path | Targets | Output Manifest | Gate PASS |")
    lines.append("|---|---:|---:|---:|")
    for item in report["path_summary"]:
        lines.append(
            f"| {item['path']} | {item['targets']} | "
            f"{item['output_manifest_count']} | {item['gate_pass']} |"
        )
    lines.append("")
    lines.append("## Final Outputs")
    lines.append("")
    lines.append(f"- Frontend-ready manifest: `{report['frontend_ready_manifest']}`")
    lines.append(f"- Frontend-ready count: {report['frontend_ready_count']}")
    lines.append(f"- Frontend-ready rate: {report['frontend_ready_rate']:.2%}")
    lines.append(f"- Backend materialized manifest: `{report['backend_materialized_manifest']}`")
    lines.append(f"- Backend materialized count: {report['backend_materialized_count']}")
    lines.append(f"- Blocked manifest: `{report['blocked_manifest']}`")
    lines.append(f"- Blocked count: {report['blocked_count']}")
    lines.append("")
    lines.append("## Frontend Origin Breakdown")
    lines.append("")
    lines.append("| Origin | Count |")
    lines.append("|---|---:|")
    for origin, count in sorted(report["origin_counts"].items()):
        lines.append(f"| {origin} | {count} |")
    lines.append("")
    lines.append("## Blocked Designs")
    lines.append("")
    if report["blocked_designs"]:
        lines.append("| Design | Initial Class | Reason |")
        lines.append("|---|---|---|")
        for item in report["blocked_designs"]:
            lines.append(
                f"| {item['design_name']} | {item['initial_failure_class']} | {item['reason']} |"
            )
    else:
        lines.append("No blocked designs.")
    lines.append("")
    lines.append("## Key Files")
    lines.append("")
    for key in [
        "frontend_initial_log",
        "module_closure_log",
        "frontend_module_closure_log",
        "semantic_proposal_log",
        "semantic_apply_log",
        "frontend_semantic_log",
        "deterministic_log",
        "frontend_deterministic_log",
        "materializer_log",
        "json_report",
    ]:
        lines.append(f"- {key}: `{report.get(key, '')}`")
    lines.append("")

    path.write_text("\n".join(lines))


def main():
    ap = argparse.ArgumentParser("Frontend repair chain v0")

    ap.add_argument("--input-manifest", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--policy", default="stable", choices=["stable"])
    ap.add_argument("--python", default=sys.executable)

    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    manifests_dir = out_dir / "manifests"
    reports_dir = out_dir / "reports"
    logs_dir = out_dir / "logs"

    frontend_initial_dir = out_dir / "frontend_initial"
    frontend_module_closure_dir = out_dir / "frontend_module_closure"
    frontend_semantic_dir = out_dir / "frontend_semantic"
    frontend_deterministic_dir = out_dir / "frontend_deterministic"

    frontend_initial_summary = frontend_initial_dir / "results" / "frontend_gate_summary.jsonl"
    frontend_initial_handoff = frontend_initial_dir / "results" / "backend_handoff_manifest.jsonl"

    frontend_module_closure_summary = frontend_module_closure_dir / "results" / "frontend_gate_summary.jsonl"
    frontend_module_closure_handoff = frontend_module_closure_dir / "results" / "backend_handoff_manifest.jsonl"

    frontend_semantic_summary = frontend_semantic_dir / "results" / "frontend_gate_summary.jsonl"
    frontend_semantic_handoff = frontend_semantic_dir / "results" / "backend_handoff_manifest.jsonl"

    frontend_deterministic_summary = frontend_deterministic_dir / "results" / "frontend_gate_summary.jsonl"
    frontend_deterministic_handoff = frontend_deterministic_dir / "results" / "backend_handoff_manifest.jsonl"

    failure_manifest = manifests_dir / "frontend_failures.jsonl"

    module_closure_targets = manifests_dir / "module_closure_targets.jsonl"
    module_closure_manifest = manifests_dir / "module_closure_repaired_manifest.jsonl"
    module_closure_report = reports_dir / "module_closure_repair_report.json"

    semantic_targets = manifests_dir / "semantic_targets.jsonl"
    semantic_patch_spec = reports_dir / "semantic_proposed_patch_spec.json"
    semantic_proposal_report = reports_dir / "semantic_patch_proposal_report.json"
    semantic_patched_manifest = manifests_dir / "semantic_patched_manifest.jsonl"
    semantic_apply_report = reports_dir / "semantic_apply_patch_spec_report.json"

    deterministic_targets = manifests_dir / "deterministic_fallback_targets.jsonl"
    deterministic_manifest = manifests_dir / "deterministic_repaired_manifest.jsonl"
    deterministic_report = reports_dir / "deterministic_repair_report.json"

    frontend_ready_manifest = manifests_dir / "frontend_ready_manifest.jsonl"
    backend_materialized_manifest = manifests_dir / "backend_materialized_manifest.jsonl"
    blocked_manifest = manifests_dir / "blocked_manifest.jsonl"
    materializer_report = reports_dir / "materializer_report.json"

    frontend_initial_log = logs_dir / "frontend_initial.log"
    module_closure_log = logs_dir / "module_closure_repair.log"
    frontend_module_closure_log = logs_dir / "frontend_module_closure.log"
    semantic_proposal_log = logs_dir / "semantic_patch_proposal.log"
    semantic_apply_log = logs_dir / "semantic_apply_patch_spec.log"
    frontend_semantic_log = logs_dir / "frontend_semantic.log"
    deterministic_log = logs_dir / "deterministic_repair.log"
    frontend_deterministic_log = logs_dir / "frontend_deterministic.log"
    materializer_log = logs_dir / "materializer.log"

    json_report = reports_dir / "frontend_repair_chain_report.json"
    md_report = reports_dir / "frontend_repair_chain_report.md"

    print("=" * 80)
    print("Frontend Repair Chain v0")
    print("=" * 80)
    print(f"input_manifest = {args.input_manifest}")
    print(f"out_dir        = {out_dir}")
    print(f"policy         = {args.policy}")

    # 1. Initial frontend gate.
    print("\n[1/7] Frontend initial gate")
    rc, _ = run_cmd(
        [
            args.python,
            "-m",
            "microsurgeon_frontend.cli",
            "--designs",
            args.input_manifest,
            "--out-dir",
            str(frontend_initial_dir),
        ],
        frontend_initial_log,
    )
    if rc != 0:
        raise SystemExit(rc)

    initial = summarize_frontend(frontend_initial_summary)
    failures = build_failure_manifest(
        args.input_manifest,
        frontend_initial_summary,
        failure_manifest,
    )

    print(f"initial PASS     = {initial['counts'].get('PASS', 0)}")
    print(f"initial failures = {len(failures)}")

    # 2. Module closure repair.
    print("\n[2/7] Module-closure repair")
    module_targets = build_targets_by_classes(
        failure_manifest,
        frontend_initial_summary,
        module_closure_targets,
        MODULE_CLOSURE_CLASSES,
        "repair_chain_module_closure_failure_class",
    )

    print(f"module closure targets = {len(module_targets)}")

    if module_targets:
        rc, _ = run_cmd(
            [
                args.python,
                "-u",
                "-m",
                "microsurgeon_frontend.repairs.run_module_closure_repair",
                "--designs",
                str(module_closure_targets),
                "--prev-log-root",
                str(frontend_initial_dir / "logs"),
                "--out-manifest",
                str(module_closure_manifest),
                "--report",
                str(module_closure_report),
            ],
            module_closure_log,
        )
        if rc != 0:
            raise SystemExit(rc)

        rc, _ = run_cmd(
            [
                args.python,
                "-m",
                "microsurgeon_frontend.cli",
                "--designs",
                str(module_closure_manifest),
                "--out-dir",
                str(frontend_module_closure_dir),
            ],
            frontend_module_closure_log,
        )
        if rc != 0:
            raise SystemExit(rc)
    else:
        write_jsonl([], module_closure_manifest)
        write_json([], module_closure_report)
        write_jsonl([], frontend_module_closure_summary)
        write_jsonl([], frontend_module_closure_handoff)
        module_closure_log.write_text("No module closure targets.\n")
        frontend_module_closure_log.write_text("No module closure manifest.\n")

    module_summary = summarize_frontend(frontend_module_closure_summary)

    # 3. Semantic patch-spec repair.
    print("\n[3/7] Semantic patch-spec repair")
    semantic_rows = build_targets_by_classes(
        failure_manifest,
        frontend_initial_summary,
        semantic_targets,
        SEMANTIC_CLASSES,
        "repair_chain_semantic_failure_class",
    )

    print(f"semantic targets = {len(semantic_rows)}")

    if semantic_rows:
        rc, _ = run_cmd(
            [
                args.python,
                "-u",
                "-m",
                "microsurgeon_frontend.semantic.propose_patch_spec",
                "--designs",
                str(semantic_targets),
                "--prev-log-root",
                str(frontend_initial_dir / "logs"),
                "--out-patch-spec",
                str(semantic_patch_spec),
                "--report",
                str(semantic_proposal_report),
            ],
            semantic_proposal_log,
        )
        if rc != 0:
            raise SystemExit(rc)

        if count_patch_specs(semantic_patch_spec) > 0:
            rc, _ = run_cmd(
                [
                    args.python,
                    "-u",
                    "-m",
                    "microsurgeon_frontend.semantic.apply_patch_spec",
                    "--designs",
                    str(semantic_targets),
                    "--patch-spec",
                    str(semantic_patch_spec),
                    "--work-root",
                    str(out_dir / "semantic_patch_work"),
                    "--out-manifest",
                    str(semantic_patched_manifest),
                    "--report",
                    str(semantic_apply_report),
                ],
                semantic_apply_log,
            )
            if rc != 0:
                raise SystemExit(rc)
        else:
            write_jsonl([], semantic_patched_manifest)
            write_json([], semantic_apply_report)
            semantic_apply_log.write_text("No semantic patch specs.\n")

        if count_jsonl(semantic_patched_manifest) > 0:
            rc, _ = run_cmd(
                [
                    args.python,
                    "-m",
                    "microsurgeon_frontend.cli",
                    "--designs",
                    str(semantic_patched_manifest),
                    "--out-dir",
                    str(frontend_semantic_dir),
                ],
                frontend_semantic_log,
            )
            if rc != 0:
                raise SystemExit(rc)
        else:
            write_jsonl([], frontend_semantic_summary)
            write_jsonl([], frontend_semantic_handoff)
            frontend_semantic_log.write_text("No semantic patched manifest.\n")
    else:
        write_json({"version": "semantic_patch_spec_bundle_v1", "patches": []}, semantic_patch_spec)
        write_json([], semantic_proposal_report)
        write_jsonl([], semantic_patched_manifest)
        write_json([], semantic_apply_report)
        write_jsonl([], frontend_semantic_summary)
        write_jsonl([], frontend_semantic_handoff)
        semantic_proposal_log.write_text("No semantic targets.\n")
        semantic_apply_log.write_text("No semantic targets.\n")
        frontend_semantic_log.write_text("No semantic patched manifest.\n")

    semantic_summary = summarize_frontend(frontend_semantic_summary)

    # 4. Deterministic fallback.
    print("\n[4/7] Deterministic fallback repair")
    pre_deterministic_merge = merge_handoffs_with_priority(
        [
            ("initial_frontend_pass", frontend_initial_handoff),
            ("module_closure_repair_pass", frontend_module_closure_handoff),
            ("semantic_patch_spec_pass", frontend_semantic_handoff),
        ],
        manifests_dir / "_pre_deterministic_ready.tmp.jsonl",
    )

    fallback_targets = build_deterministic_fallback_targets(
        failure_manifest,
        pre_deterministic_merge["ready_names"],
        deterministic_targets,
    )

    print(f"deterministic fallback targets = {len(fallback_targets)}")

    if fallback_targets:
        rc, _ = run_cmd(
            [
                args.python,
                "-u",
                "-m",
                "microsurgeon_frontend.repairs.run_deterministic_repair",
                "--designs",
                str(deterministic_targets),
                "--prev-log-root",
                str(frontend_initial_dir / "logs"),
                "--work-root",
                str(out_dir / "deterministic_work"),
                "--out-manifest",
                str(deterministic_manifest),
                "--report",
                str(deterministic_report),
            ],
            deterministic_log,
        )
        if rc != 0:
            raise SystemExit(rc)

        rc, _ = run_cmd(
            [
                args.python,
                "-m",
                "microsurgeon_frontend.cli",
                "--designs",
                str(deterministic_manifest),
                "--out-dir",
                str(frontend_deterministic_dir),
            ],
            frontend_deterministic_log,
        )
        if rc != 0:
            raise SystemExit(rc)
    else:
        write_jsonl([], deterministic_manifest)
        write_json([], deterministic_report)
        write_jsonl([], frontend_deterministic_summary)
        write_jsonl([], frontend_deterministic_handoff)
        deterministic_log.write_text("No deterministic fallback targets.\n")
        frontend_deterministic_log.write_text("No deterministic fallback manifest.\n")

    deterministic_summary = summarize_frontend(frontend_deterministic_summary)

    # 5. Final merge.
    print("\n[5/7] Merge frontend-ready manifest")
    merge_info = merge_handoffs_with_priority(
        [
            ("initial_frontend_pass", frontend_initial_handoff),
            ("module_closure_repair_pass", frontend_module_closure_handoff),
            ("semantic_patch_spec_pass", frontend_semantic_handoff),
            ("deterministic_repair_pass", frontend_deterministic_handoff),
        ],
        frontend_ready_manifest,
    )

    ready_names = merge_info["ready_names"]

    blocked_rows = make_blocked_manifest(
        failure_manifest,
        ready_names,
        blocked_manifest,
    )

    print(f"frontend-ready = {count_jsonl(frontend_ready_manifest)}")
    print(f"blocked        = {len(blocked_rows)}")

    # 6. Materialize backend handoff.
    print("\n[6/7] Materialize backend handoff")
    rc, _ = run_cmd(
        [
            args.python,
            "-u",
            "-m",
            "microsurgeon_frontend.handoff_materializer",
            "--input",
            str(frontend_ready_manifest),
            "--out-manifest",
            str(backend_materialized_manifest),
            "--work-root",
            str(out_dir / "materialized"),
            "--report",
            str(materializer_report),
        ],
        materializer_log,
    )
    if rc != 0:
        raise SystemExit(rc)

    print(f"materialized = {count_jsonl(backend_materialized_manifest)}")

    # 7. Report.
    print("\n[7/7] Write report")

    input_count = count_jsonl(args.input_manifest)
    frontend_ready_count = count_jsonl(frontend_ready_manifest)
    materialized_count = count_jsonl(backend_materialized_manifest)

    blocked_report_rows = []
    for obj in blocked_rows:
        blocked_report_rows.append(
            {
                "design_name": obj["design_name"],
                "initial_failure_class": obj.get("repair_chain_initial_failure_class", "UNKNOWN"),
                "reason": obj.get("blocked_reason", "unknown"),
            }
        )

    path_summary = [
        {
            "path": "module_closure_repair",
            "targets": count_jsonl(module_closure_targets),
            "output_manifest_count": count_jsonl(module_closure_manifest),
            "gate_pass": module_summary["counts"].get("PASS", 0),
        },
        {
            "path": "semantic_patch_spec",
            "targets": count_jsonl(semantic_targets),
            "output_manifest_count": count_jsonl(semantic_patched_manifest),
            "gate_pass": semantic_summary["counts"].get("PASS", 0),
        },
        {
            "path": "deterministic_fallback",
            "targets": count_jsonl(deterministic_targets),
            "output_manifest_count": count_jsonl(deterministic_manifest),
            "gate_pass": deterministic_summary["counts"].get("PASS", 0),
        },
    ]

    result = "PASS"
    if materialized_count != frontend_ready_count:
        result = "FAIL"

    report = {
        "result": result,
        "policy": args.policy,
        "input_manifest": args.input_manifest,
        "input_count": input_count,
        "initial_total": initial["total"],
        "initial_counts": initial["counts"],
        "initial_pass_count": initial["counts"].get("PASS", 0),
        "initial_failure_count": len(failures),
        "path_summary": path_summary,
        "frontend_ready_manifest": str(frontend_ready_manifest),
        "frontend_ready_count": frontend_ready_count,
        "frontend_ready_rate": frontend_ready_count / input_count if input_count else 0.0,
        "backend_materialized_manifest": str(backend_materialized_manifest),
        "backend_materialized_count": materialized_count,
        "blocked_manifest": str(blocked_manifest),
        "blocked_count": len(blocked_rows),
        "blocked_designs": blocked_report_rows,
        "origin_counts": merge_info["origin_counts"],
        "duplicate_counts": merge_info["duplicate_counts"],
        "frontend_initial_log": str(frontend_initial_log),
        "module_closure_log": str(module_closure_log),
        "frontend_module_closure_log": str(frontend_module_closure_log),
        "semantic_proposal_log": str(semantic_proposal_log),
        "semantic_apply_log": str(semantic_apply_log),
        "frontend_semantic_log": str(frontend_semantic_log),
        "deterministic_log": str(deterministic_log),
        "frontend_deterministic_log": str(frontend_deterministic_log),
        "materializer_log": str(materializer_log),
        "json_report": str(json_report),
    }

    write_json(report, json_report)
    write_report(report, md_report)

    print("\nDone.")
    print(f"frontend-ready manifest     : {frontend_ready_manifest}")
    print(f"backend materialized manifest: {backend_materialized_manifest}")
    print(f"blocked manifest            : {blocked_manifest}")
    print(f"report                      : {md_report}")

    if result != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
