#!/usr/bin/env python3
import argparse
import json
import shutil
from pathlib import Path


SUPPORTED_OPS = {
    "replace_line_exact",
    "replace_range_exact",
    "insert_after_line",
    "insert_before_line",
}

MAX_RANGE_LINES = 80
MAX_INSERT_LINES = 80


def read_jsonl(path):
    path = Path(path)
    rows = []
    if not path.exists():
        return rows

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


def load_patch_specs(path):
    obj = json.loads(Path(path).read_text())

    if isinstance(obj, list):
        specs = obj
    elif isinstance(obj, dict) and "patches" in obj:
        specs = obj["patches"]
    elif isinstance(obj, dict):
        specs = [obj]
    else:
        raise ValueError("patch spec must be dict, list, or dict with patches")

    by_design = {}
    for spec in specs:
        name = spec.get("design_name")
        if not name:
            raise ValueError("each patch spec must contain design_name")

        if name in by_design:
            raise ValueError(f"duplicate patch spec for design: {name}")

        by_design[name] = spec

    return by_design


def copy_design_to_work(obj, work_root):
    name = obj["design_name"]
    out_dir = Path(work_root) / name / "rtl"

    if out_dir.exists():
        shutil.rmtree(out_dir)

    out_dir.mkdir(parents=True, exist_ok=True)

    used_names = set()
    new_files = []
    source_to_work = {}

    for idx, src in enumerate(obj.get("rtl_files", [])):
        src_p = Path(src)

        dst_name = src_p.name
        if dst_name in used_names:
            dst_name = f"{idx:03d}_{dst_name}"

        used_names.add(dst_name)

        dst = out_dir / dst_name
        shutil.copy2(src_p, dst)

        source_to_work[str(src_p)] = str(dst)
        source_to_work[str(src_p.resolve())] = str(dst)
        source_to_work[src_p.name] = str(dst)

        new_files.append(str(dst))

    new_obj = dict(obj)
    new_obj["rtl_files"] = new_files
    new_obj["status"] = "semantic_patch_spec_repaired"
    new_obj["semantic_patch_spec_original_to_repaired"] = source_to_work

    return new_obj, source_to_work


def resolve_patch_file(op, source_to_work):
    if "file" in op:
        key = op["file"]
        if key in source_to_work:
            return Path(source_to_work[key])

        p = Path(key)
        if str(p.resolve()) in source_to_work:
            return Path(source_to_work[str(p.resolve())])

        if p.name in source_to_work:
            return Path(source_to_work[p.name])

    if "file_match" in op:
        target = op["file_match"]
        matches = []
        for k, v in source_to_work.items():
            if k == target or Path(k).name == target or target in k:
                matches.append(v)

        matches = sorted(set(matches))

        if len(matches) == 1:
            return Path(matches[0])

        if len(matches) == 0:
            raise ValueError(f"file_match did not match any RTL file: {target}")

        raise ValueError(f"file_match is ambiguous: {target} -> {matches}")

    raise ValueError("operation must contain file or file_match")


def read_lines(path):
    return Path(path).read_text(errors="ignore").splitlines()


def write_lines(path, lines):
    Path(path).write_text("\n".join(lines) + "\n")


def validate_safe_text(text):
    if "\x00" in text:
        raise ValueError("patch text contains NUL byte")

    # 不允许 patch spec 直接塞进一整个新 module。
    # 如果将来确实需要模块级修复，应走单独的 source-acquisition / module-closure 通道。
    if "\nmodule " in "\n" + text:
        raise ValueError("patch text appears to introduce a module definition, rejected")

    if "\nendmodule" in "\n" + text:
        raise ValueError("patch text appears to introduce endmodule, rejected")


def apply_replace_line_exact(path, op):
    line_no = int(op["line"])
    old = op["old"]
    new = op["new"]

    validate_safe_text(new)

    lines = read_lines(path)

    if line_no < 1 or line_no > len(lines):
        raise ValueError(f"line out of range: {line_no}")

    actual = lines[line_no - 1]

    if actual != old:
        raise ValueError(
            "replace_line_exact old text mismatch "
            f"at {path}:{line_no}\n"
            f"expected: {old!r}\n"
            f"actual  : {actual!r}"
        )

    lines[line_no - 1] = new
    write_lines(path, lines)

    return {
        "op": "replace_line_exact",
        "file": str(path),
        "line": line_no,
    }


def apply_replace_range_exact(path, op):
    start = int(op["start_line"])
    end = int(op["end_line"])
    old_text = op["old_text"]
    new_text = op["new_text"]

    validate_safe_text(new_text)

    if end < start:
        raise ValueError("end_line must be >= start_line")

    if end - start + 1 > MAX_RANGE_LINES:
        raise ValueError(f"range too large: {end - start + 1} lines")

    new_lines = new_text.splitlines()
    if len(new_lines) > MAX_RANGE_LINES:
        raise ValueError(f"replacement too large: {len(new_lines)} lines")

    lines = read_lines(path)

    if start < 1 or end > len(lines):
        raise ValueError(f"range out of file bounds: {start}-{end}")

    actual_text = "\n".join(lines[start - 1:end])

    if actual_text != old_text:
        raise ValueError(
            "replace_range_exact old_text mismatch "
            f"at {path}:{start}-{end}"
        )

    lines[start - 1:end] = new_lines
    write_lines(path, lines)

    return {
        "op": "replace_range_exact",
        "file": str(path),
        "start_line": start,
        "end_line": end,
        "new_line_count": len(new_lines),
    }


def apply_insert_after_line(path, op):
    line_no = int(op["line"])
    expected = op.get("expected_line")
    insert_text = op["insert_text"]

    validate_safe_text(insert_text)

    insert_lines = insert_text.splitlines()
    if len(insert_lines) > MAX_INSERT_LINES:
        raise ValueError(f"insert too large: {len(insert_lines)} lines")

    lines = read_lines(path)

    if line_no < 0 or line_no > len(lines):
        raise ValueError(f"line out of range: {line_no}")

    if expected is not None and line_no >= 1:
        actual = lines[line_no - 1]
        if actual != expected:
            raise ValueError(
                "insert_after_line expected_line mismatch "
                f"at {path}:{line_no}"
            )

    lines[line_no:line_no] = insert_lines
    write_lines(path, lines)

    return {
        "op": "insert_after_line",
        "file": str(path),
        "line": line_no,
        "insert_line_count": len(insert_lines),
    }


def apply_insert_before_line(path, op):
    line_no = int(op["line"])
    expected = op.get("expected_line")
    insert_text = op["insert_text"]

    validate_safe_text(insert_text)

    insert_lines = insert_text.splitlines()
    if len(insert_lines) > MAX_INSERT_LINES:
        raise ValueError(f"insert too large: {len(insert_lines)} lines")

    lines = read_lines(path)

    if line_no < 1 or line_no > len(lines) + 1:
        raise ValueError(f"line out of range: {line_no}")

    if expected is not None and line_no <= len(lines):
        actual = lines[line_no - 1]
        if actual != expected:
            raise ValueError(
                "insert_before_line expected_line mismatch "
                f"at {path}:{line_no}"
            )

    idx = line_no - 1
    lines[idx:idx] = insert_lines
    write_lines(path, lines)

    return {
        "op": "insert_before_line",
        "file": str(path),
        "line": line_no,
        "insert_line_count": len(insert_lines),
    }


def apply_one_op(op, source_to_work):
    op_name = op.get("op")

    if op_name not in SUPPORTED_OPS:
        raise ValueError(f"unsupported op: {op_name}")

    path = resolve_patch_file(op, source_to_work)

    if not path.exists():
        raise ValueError(f"resolved patch file does not exist: {path}")

    if op_name == "replace_line_exact":
        return apply_replace_line_exact(path, op)

    if op_name == "replace_range_exact":
        return apply_replace_range_exact(path, op)

    if op_name == "insert_after_line":
        return apply_insert_after_line(path, op)

    if op_name == "insert_before_line":
        return apply_insert_before_line(path, op)

    raise ValueError(f"unreachable unsupported op: {op_name}")


def apply_design_patch(obj, spec, work_root):
    new_obj, source_to_work = copy_design_to_work(obj, work_root)

    ops = spec.get("operations", [])
    if not ops:
        raise ValueError(f"patch spec for {obj['design_name']} has no operations")

    applied = []

    for op in ops:
        applied.append(apply_one_op(op, source_to_work))

    new_obj["semantic_patch_spec"] = {
        "version": spec.get("version", "semantic_patch_spec_v1"),
        "source": spec.get("source", "unknown"),
        "rationale": spec.get("rationale", ""),
        "operation_count": len(ops),
        "applied": applied,
    }

    return new_obj, applied


def main():
    ap = argparse.ArgumentParser("Apply constrained semantic frontend patch specs")

    ap.add_argument("--designs", required=True)
    ap.add_argument("--patch-spec", required=True)
    ap.add_argument("--work-root", required=True)
    ap.add_argument("--out-manifest", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--strict", action="store_true")

    args = ap.parse_args()

    rows = read_jsonl(args.designs)
    specs = load_patch_specs(args.patch_spec)

    repaired = []
    report = []

    for obj in rows:
        name = obj["design_name"]

        if name not in specs:
            report.append({
                "design_name": name,
                "has_patch": False,
                "repaired": False,
                "error": "no patch spec for design",
            })

            if args.strict:
                raise SystemExit(f"missing patch spec for {name}")

            continue

        spec = specs[name]

        try:
            new_obj, applied = apply_design_patch(obj, spec, args.work_root)
            repaired.append(new_obj)
            report.append({
                "design_name": name,
                "has_patch": True,
                "repaired": True,
                "applied": applied,
                "error": "",
            })
            print(f"{name}: applied {len(applied)} operation(s)")

        except Exception as e:
            report.append({
                "design_name": name,
                "has_patch": True,
                "repaired": False,
                "applied": [],
                "error": str(e),
            })
            print(f"{name}: PATCH_FAILED: {e}")

            if args.strict:
                raise

    write_jsonl(repaired, args.out_manifest)

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    print(f"out_manifest = {args.out_manifest}")
    print(f"report       = {args.report}")
    print(f"repaired     = {len(repaired)}")


if __name__ == "__main__":
    main()