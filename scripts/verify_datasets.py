#!/usr/bin/env python3
"""Fail-closed verification for the released public benchmark inputs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "datasets"
EXPECTED = {"cirfix39": 39, "literature32": 32, "strider14": 14, "rtlfixer50": 50}
REQUIRED_ROLES = {
    "cirfix39": {"buggy_rtl", "reference_rtl", "testbench"},
    "literature32": {"buggy_rtl", "testbench"},
    "strider14": {"buggy_rtl", "reference_rtl", "testbench", "oracle"},
    "rtlfixer50": {"buggy_rtl", "reference_rtl", "testbench"},
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(value: dict) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def main() -> int:
    errors: list[str] = []
    observed_paths: dict[str, str] = {}
    summary = {}
    for benchmark, expected_rows in EXPECTED.items():
        manifest = DATA / "manifests" / f"{benchmark}.jsonl"
        if not manifest.is_file():
            errors.append(f"missing manifest: {benchmark}")
            continue
        rows = [json.loads(line) for line in manifest.read_text().splitlines() if line.strip()]
        ids = [str(row.get("case_id") or "") for row in rows]
        if len(rows) != expected_rows:
            errors.append(f"{benchmark}: expected {expected_rows} rows, got {len(rows)}")
        if len(ids) != len(set(ids)) or not all(ids):
            errors.append(f"{benchmark}: missing or duplicate case IDs")
        file_count = 0
        for row in rows:
            claimed = row.get("case_sha256")
            payload = dict(row)
            payload.pop("case_sha256", None)
            if claimed != canonical_hash(payload):
                errors.append(f"{benchmark}/{row.get('case_id')}: case hash mismatch")
            roles = {str(file.get("role")) for file in row.get("files", [])}
            missing_roles = REQUIRED_ROLES[benchmark] - roles
            if missing_roles:
                errors.append(
                    f"{benchmark}/{row.get('case_id')}: missing roles {sorted(missing_roles)}"
                )
            for file in row.get("files", []):
                relative = str(file.get("path") or "")
                pure = PurePosixPath(relative)
                if pure.is_absolute() or ".." in pure.parts or not relative.startswith("datasets/"):
                    errors.append(f"{benchmark}: non-portable path: {relative}")
                    continue
                path = ROOT / pure
                if not path.is_file():
                    errors.append(f"{benchmark}: missing input: {relative}")
                    continue
                actual = sha256_file(path)
                if actual != file.get("sha256") or path.stat().st_size != file.get("size_bytes"):
                    errors.append(f"{benchmark}: file identity mismatch: {relative}")
                prior = observed_paths.setdefault(relative, actual)
                if prior != actual:
                    errors.append(f"cross-manifest file drift: {relative}")
                file_count += 1
        summary[benchmark] = {
            "rows": len(rows),
            "file_references": file_count,
            "manifest_sha256": sha256_file(manifest),
        }
    result = {
        "verdict": "PASS" if not errors else "FAIL",
        "errors": errors,
        "benchmarks": summary,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    if errors:
        raise SystemExit(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
