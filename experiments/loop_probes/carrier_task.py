"""Export a qualified carrier package plus one variant as a repository-runner task (no model calls).

Layout written (the runner reads only ``public/``)::

    public/task.json             task id, editable scope from the manifest, spec and build paths
    public/spec.txt              the package's spec.md
    public/visible/build.json    Icarus, the RTL list (compile units), declared headers, both public suites
    public/visible/<suite>.sv    the frozen suite testbenches
    public/repo/<rtl>            clean sources with the variant's overlay applied
    public/repo/<header>         declared header dependencies, always the clean bytes (read-only for Blue)
    oracle/clean/<rtl|header>    the clean implementation (evaluator side, never shown to Blue)
    export.json                  provenance: package, variant, freeze hash, file hashes

The package is checked against its suite freeze first, so Blue only ever sees frozen suites and spec.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from experiments.loop_probes import witness_matrix as wm
from r3e.protocol.hashing import hash_payload


def export(pkg: Path, variant: str, out: Path, freeze: dict[str, Any]) -> Path:
    pkg = pkg.resolve()
    wm.check_freeze(pkg, freeze)
    man = wm.load(pkg)
    if variant not in man["variants"]:
        raise ValueError(f"unknown variant {variant}")
    if out.exists():
        raise FileExistsError(out)
    pub = out / "public"
    headers = man.get("header_dependencies", [])
    for rel in [*man["rtl"], *headers]:
        for root, base in ((pub / "repo", pkg / "variants" / variant), (out / "oracle" / "clean", None)):
            src = base / rel if base is not None and (base / rel).exists() else pkg / "src" / rel
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, root / rel)
    (pub / "visible").mkdir(parents=True)
    suites = []
    for name, suite in man["suites"].items():
        files = []
        for f in suite["files"]:
            shutil.copy2(pkg / "suites" / name / f, pub / "visible" / f"{name}_{f}")
            files.append(f"{name}_{f}")
        suites.append({"name": name, "top": suite["top"], "files": files})
    build = {"tool": "iverilog", "rtl": man["rtl"], "suites": suites, "pass_marker": man["pass_marker"],
             "fail_marker": man["fail_marker"], "source_files": [f for s in suites for f in s["files"]]}
    if headers:
        build["headers"] = headers
    (pub / "visible" / "build.json").write_text(json.dumps(build, indent=1))
    shutil.copy2(pkg / man["spec"], pub / "spec.txt")
    task_id = f"{man['carrier_id']}__{variant}"
    (pub / "task.json").write_text(json.dumps({"task_id": task_id, "source_root": "repo", "spec": "spec.txt",
                                               "visible_test": "visible/build.json", "editable": man["editable"]}))
    (out / "export.json").write_text(json.dumps({
        "task_id": task_id, "package": str(pkg), "variant": variant, "role": man["variants"][variant]["role"],
        "freeze_manifest_fields_hash": freeze["frozen_manifest_fields_hash"],
        "public_files": {str(f.relative_to(out)): hash_payload(f.read_text()) for f in sorted(pub.rglob("*")) if f.is_file()}},
        indent=1))
    return out
