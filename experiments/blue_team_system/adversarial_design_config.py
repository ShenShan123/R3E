from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "r3e"
RTL_ROOT = Path(os.environ.get("R3E_RTL_ROOT", REPO_ROOT / "third_party" / "rtl"))
POISON_ROOT = Path(os.environ.get("R3E_POISON_ROOT", REPO_ROOT / "artifacts" / "red_team"))
SUPPORTED_DESIGNS = {"c880", "c1908", "c3540"}
EXPECTED_CLEAN_SHA256 = {
    "c880": "6c3d32821549aa97e89a3e3c9c3bf8cdff1eee0b9d6c7857eaf2ba26c154a48e",
    "c1908": "fe00c5d880037566721714d4f4bf497a23632c3bf3a80b69777389f8803cdf76",
    "c3540": "1e3aca94186e78bd5cfa46eaedbea48d37b25f323fb2f3fc33aa42ab4e1cf8b6",
}
EXPECTED_CANONICAL_SDC_SHA256 = {
    "c880": "f9bebb7be5b3f3a4e546b5e3d8e77a04be887ca1f7bed4721a2a601105463c14",
    "c1908": "2c4a47fe7e05196e5e64603bbf1b4245c3e6ebf140ae45d45d710b3494b23ebc",
    "c3540": "49c689ce7b2b137cd67e7cde14bf03f3f849d711fda110207d21101a61fc7a02",
}
DEFAULT_C880_POISON_SDC = POISON_ROOT / "selected_poison/benchmarks/c880/c880.sdc"


@dataclass(frozen=True)
class DesignConfig:
    design_name: str
    module_name: str
    rtl_path: Path
    canonical_sdc_path: Path
    base_clock_period: float
    sdc_clock_port: str
    expected_clean_sha256: str | None
    expected_canonical_sdc_sha256: str | None


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def closure_sanity_target(design_name: str) -> dict[str, Any]:
    import sys
    if str(SOURCE_ROOT) not in sys.path:
        sys.path.insert(0, str(SOURCE_ROOT))
    from auto_batch_targets import BATCH_TARGETS_CLOSURE_SANITY

    if design_name not in SUPPORTED_DESIGNS:
        raise ValueError(f"unsupported design for adversarial A flow: {design_name}")
    matches = [t for t in BATCH_TARGETS_CLOSURE_SANITY if t["design_name"] == design_name]
    if not matches:
        raise ValueError(f"design missing from BATCH_TARGETS_CLOSURE_SANITY: {design_name}")
    return dict(matches[0])


def design_config(design_name: str) -> DesignConfig:
    target = closure_sanity_target(design_name)
    return DesignConfig(
        design_name=design_name,
        module_name=target["module_name"],
        rtl_path=RTL_ROOT / target["rtl_pattern"],
        canonical_sdc_path=REPO_ROOT / "benchmarks" / design_name / f"{design_name}.sdc",
        base_clock_period=float(target["base_clock_period"]),
        sdc_clock_port=target["sdc_clock_port"],
        expected_clean_sha256=EXPECTED_CLEAN_SHA256[design_name],
        expected_canonical_sdc_sha256=EXPECTED_CANONICAL_SDC_SHA256[design_name],
    )


def default_poison_sdc(design_name: str) -> Path:
    if design_name == "c880":
        return DEFAULT_C880_POISON_SDC
    return POISON_ROOT / f"selected_poison/benchmarks/{design_name}/{design_name}.sdc"


def validate_design_inputs(config: DesignConfig, poison_sdc: Path | None = None) -> dict[str, str | bool | None]:
    required = [config.rtl_path, config.canonical_sdc_path]
    if poison_sdc is not None:
        required.append(poison_sdc)
    for path in required:
        if not path.is_file():
            raise FileNotFoundError(path)
    clean_hash = sha256(config.rtl_path)
    canonical_hash = sha256(config.canonical_sdc_path)
    if config.expected_clean_sha256 and clean_hash != config.expected_clean_sha256:
        raise RuntimeError(f"main {config.design_name} hash mismatch: {clean_hash}")
    if config.expected_canonical_sdc_sha256 and canonical_hash != config.expected_canonical_sdc_sha256:
        raise RuntimeError(f"canonical {config.design_name}.sdc hash mismatch: {canonical_hash}")
    poison_hash = sha256(poison_sdc) if poison_sdc is not None else None
    if poison_hash is not None and poison_hash == canonical_hash:
        raise RuntimeError("poison SDC is byte-identical to the canonical SDC")
    return {
        "clean_hash": clean_hash,
        "canonical_sdc_hash": canonical_hash,
        "clean_hash_checked": bool(config.expected_clean_sha256),
        "canonical_sdc_hash_checked": bool(config.expected_canonical_sdc_sha256),
        "poison_sdc_hash": poison_hash,
    }


def require_sta_success(label: str, sta: Any) -> None:
    if not getattr(sta, "success", False):
        raise RuntimeError(f"{label} STA failed")
    for metric in ("wns", "tns", "area"):
        if getattr(sta, metric, None) is None:
            raise RuntimeError(f"{label} STA missing metric: {metric}")


def select_final_netlist(result: dict[str, Any], fallback: Path) -> Path:
    raw = result.get("final_netlist")
    if raw:
        candidate = Path(str(raw))
        if candidate.is_file():
            return candidate
    if fallback.is_file():
        return fallback
    raise RuntimeError("ECO result has no usable final netlist and fallback is missing")
