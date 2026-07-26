#!/usr/bin/env python3
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from microsurgeon_frontend.semantic.micro_slice import analyze_design_failure


def read_jsonl(path):
    path = Path(path)
    rows = []
    if not path.exists():
        return rows

    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))

    return rows


def write_json(obj, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False))


def read_lines(path):
    return Path(path).read_text(errors="ignore").splitlines()


def rtl_files(obj):
    return [Path(x) for x in obj.get("rtl_files", [])]


def whole_word(name):
    return re.compile(rf"(?<![A-Za-z0-9_$]){re.escape(name)}(?![A-Za-z0-9_$])")


def find_file_in_design(obj, file_hint):
    files = rtl_files(obj)

    if file_hint:
        hint = Path(file_hint)
        for p in files:
            if str(p) == str(hint) or p.name == hint.name or hint.name in str(p):
                return p

    if len(files) == 1:
        return files[0]

    return None


def propose_formal_param_trailing_comma_patch(obj, analysis):
    """
    Generalized safe syntax rule.

    Correct pattern:

        localparam SOME_PARAM = ...,
    `ifdef FORMAL
            , localparam FORMAL_ONLY_PARAM = ...
    `endif
        ) (

    The only safe line to patch is the nearest non-empty/non-comment
    localparam line immediately before the `ifdef FORMAL block.
    """
    for file_path in rtl_files(obj):
        if not file_path.exists():
            continue

        lines = read_lines(file_path)

        for ifdef_idx, line in enumerate(lines, start=1):
            if "`ifdef" not in line or "FORMAL" not in line:
                continue

            following = lines[ifdef_idx:min(len(lines), ifdef_idx + 8)]
            has_formal_leading_comma_localparam = any(
                re.match(r"^\s*,\s*localparam\b", x) for x in following
            )

            if not has_formal_leading_comma_localparam:
                continue

            pred_idx = None
            pred_line = None

            j = ifdef_idx - 1
            while j >= 1:
                candidate = lines[j - 1]
                stripped = candidate.strip()

                if stripped == "":
                    j -= 1
                    continue

                if stripped.startswith("//"):
                    j -= 1
                    continue

                pred_idx = j
                pred_line = candidate
                break

            if pred_idx is None or pred_line is None:
                continue

            if not re.match(r"^\s*localparam\b.*,\s*$", pred_line):
                continue

            new_line = re.sub(r",\s*$", "", pred_line)

            return {
                "version": "semantic_patch_spec_v1",
                "design_name": obj["design_name"],
                "source": "semantic_rule_formal_param_trailing_comma_v1_2_fixed",
                "rationale": (
                    "Remove trailing comma from the localparam immediately before "
                    "a FORMAL-only leading-comma parameter block."
                ),
                "operations": [
                    {
                        "op": "replace_line_exact",
                        "file": str(file_path),
                        "line": pred_idx,
                        "old": pred_line,
                        "new": new_line,
                    }
                ],
            }

    return None

def parse_wire_declaration_names(line):
    """
    Best-effort parser for simple Verilog declarations.

    Accepts examples:
        wire tmp_a;
        wire [7:0] tmp_a;
        wire signed [15:0] tmp_a, tmp_b;
        wire tmp_a = expr;

    Rejects:
        input wire ...
        output wire ...
        assign ...
    """
    if not re.match(r"^\s*wire\b", line):
        return []

    body = re.sub(r"^\s*wire\b", "", line, count=1).strip()
    body = re.sub(r"^signed\s+", "", body).strip()

    # Remove packed dimensions at the front.
    body = re.sub(r"^(\[[^\]]+\]\s*)+", "", body).strip()

    # Remove trailing semicolon.
    body = body.rstrip(";").strip()

    names = []
    for part in body.split(","):
        part = part.strip()

        # Drop initialization.
        part = part.split("=")[0].strip()

        # Drop unpacked dimensions after name.
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_$]*)\b", part)
        if m:
            names.append(m.group(1))

    return names


def propose_wire_to_reg_lvalue_patch(obj, analysis):
    """
    Safe semantic rule for iverilog l-value errors.

    Log pattern:
        tmp_x is not a valid l-value
        tmp_x is declared here as wire

    Patch model:
        replace_line_exact on declarations that start with 'wire'
        and contain one or more reported l-value names.

    This deliberately does not touch input/output/inout declarations.
    """
    names = analysis.get("lvalue_wire_names", [])
    if not names:
        return None

    name_set = set(names)
    operations = []
    patched_line_keys = set()

    for file_path in rtl_files(obj):
        if not file_path.exists():
            continue

        lines = read_lines(file_path)

        for i, line in enumerate(lines, start=1):
            decl_names = parse_wire_declaration_names(line)
            if not decl_names:
                continue

            hit = [n for n in decl_names if n in name_set]
            if not hit:
                continue

            key = (str(file_path), i)
            if key in patched_line_keys:
                continue

            new_line = re.sub(r"^(\s*)wire\b", r"\1reg", line, count=1)

            if new_line == line:
                continue

            operations.append(
                {
                    "op": "replace_line_exact",
                    "file": str(file_path),
                    "line": i,
                    "old": line,
                    "new": new_line,
                }
            )
            patched_line_keys.add(key)

    if not operations:
        return None

    if len(operations) > 80:
        return None

    return {
        "version": "semantic_patch_spec_v1",
        "design_name": obj["design_name"],
        "source": "semantic_rule_procedural_lvalue_wire_to_reg_v1_2",
        "rationale": (
            "Convert wires reported as procedural l-values into regs using exact "
            "declaration-line replacements."
        ),
        "operations": operations,
    }


def propose_patch_for_design(obj, prev_log_root, slice_radius):
    name = obj["design_name"]

    analysis = analyze_design_failure(
        log_root=prev_log_root,
        design_name=name,
        radius=slice_radius,
    )

    signature = analysis.get("signature")

    # Hard safety guards.
    if signature == "SOURCE_INCOMPLETE_INCLUDE_NOT_FOUND":
        return {
            "design_name": name,
            "proposed": False,
            "reason": "source incomplete include not found; do not propose synthetic source patch",
            "signature": signature,
            "missing_includes": analysis.get("missing_includes", []),
            "patch_spec": None,
            "analysis": analysis,
        }

    if signature == "MODULE_CLOSURE_MISSING_MODULE":
        return {
            "design_name": name,
            "proposed": False,
            "reason": "missing module should be handled by module-closure repair",
            "signature": signature,
            "missing_modules": analysis.get("missing_modules", []),
            "patch_spec": None,
            "analysis": analysis,
        }

    patch = None

    # Local syntax rules.
    if signature == "LOCAL_SYNTAX_ERROR":
        patch = propose_formal_param_trailing_comma_patch(obj, analysis)

    # Local semantic declaration rules.
    if patch is None and signature == "PROCEDURAL_ASSIGN_TO_WIRE":
        patch = propose_wire_to_reg_lvalue_patch(obj, analysis)

    if patch is not None:
        return {
            "design_name": name,
            "proposed": True,
            "reason": patch.get("source", "matched semantic patch rule"),
            "signature": signature,
            "patch_spec": patch,
            "analysis": analysis,
        }

    return {
        "design_name": name,
        "proposed": False,
        "reason": "no safe constrained patch proposal rule matched",
        "signature": signature,
        "patch_spec": None,
        "analysis": analysis,
    }


def main():
    ap = argparse.ArgumentParser(
        "Propose constrained semantic patch specs from frontend logs and micro-slices"
    )

    ap.add_argument("--designs", required=True)
    ap.add_argument("--prev-log-root", required=True)
    ap.add_argument("--out-patch-spec", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--slice-radius", type=int, default=20)

    args = ap.parse_args()

    rows = read_jsonl(args.designs)

    proposals = []
    patch_specs = []

    for obj in rows:
        item = propose_patch_for_design(
            obj=obj,
            prev_log_root=args.prev_log_root,
            slice_radius=args.slice_radius,
        )

        proposals.append(item)

        if item["proposed"] and item["patch_spec"]:
            patch_specs.append(item["patch_spec"])

        print(
            f"{obj['design_name']}: "
            f"signature={item.get('signature')} "
            f"proposed={item['proposed']} "
            f"reason={item['reason']}"
        )

        patch = item.get("patch_spec")
        if patch:
            print(f"  operations={len(patch.get('operations', []))}")

    out_obj = {
        "version": "semantic_patch_spec_bundle_v1",
        "patches": patch_specs,
    }

    write_json(out_obj, args.out_patch_spec)
    write_json(proposals, args.report)

    print(f"out_patch_spec = {args.out_patch_spec}")
    print(f"report         = {args.report}")
    print(f"patch_count    = {len(patch_specs)}")


if __name__ == "__main__":
    main()
