from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_release_contains_no_experiment_launchers() -> None:
    forbidden = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix == ".sh" or path.name.startswith(("run_", "launch_")):
            forbidden.append(str(path.relative_to(ROOT)))
    assert forbidden == []


def test_released_dataset_manifests_use_relative_paths() -> None:
    manifests = ROOT / "datasets" / "manifests"
    for manifest in manifests.glob("*.jsonl"):
        for line in manifest.read_text().splitlines():
            row = json.loads(line)
            for file_record in row["files"]:
                path = Path(file_record["path"])
                assert not path.is_absolute()
                assert ".." not in path.parts
                assert (ROOT / path).is_file()


def test_deepseek_aggregate_uses_release_registry_key() -> None:
    source = (
        ROOT
        / "experiments/public_external_benchmarks/aggregate_and_audit_table2_deepseek_pro_v2.py"
    ).read_text()
    assert 'protocol["files"]["configs/skills.json"]' in source
    assert 'protocol["files"][".iso_semrepair/skills.json"]' not in source
