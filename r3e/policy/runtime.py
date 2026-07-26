"""Load the sole active policy and resolve frozen prompt assets."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from r3e.protocol.hashing import hash_file

from .registry_v2 import get_active_policy, load_registry
from .schema import PolicyState


class PolicyRuntimeViolation(RuntimeError):
    """Raised when formal runtime attempts an unfrozen override."""


@dataclass(frozen=True)
class PolicyRuntime:
    policy: PolicyState
    prompt_template: str
    registry_hash: str

    @classmethod
    def from_registry(
        cls,
        registry_path: str | Path,
        *,
        project_root: str | Path | None = None,
        formal_mode: bool = True,
    ) -> "PolicyRuntime":
        registry = load_registry(registry_path, formal_mode=formal_mode)
        policy = get_active_policy(registry)
        root = Path(project_root) if project_root else Path(__file__).resolve().parents[2]
        lens = policy.configuration["prompt_lens_id"]
        prompt_path = root / "configs" / "base_policy" / "prompt_templates" / f"{lens}.txt"
        if not prompt_path.is_file():
            raise PolicyRuntimeViolation(f"frozen prompt lens missing: {lens}")
        base_entry = registry["policies"][registry["base_policy"]["policy_id"]]["policy"]
        base_policy = PolicyState.from_dict(base_entry)
        asset_key = f"prompt_templates/{lens}.txt"
        if (base_policy.frozen_assets or {}).get(asset_key) != hash_file(prompt_path):
            raise PolicyRuntimeViolation(f"frozen prompt lens hash mismatch: {lens}")
        return cls(
            policy=policy,
            prompt_template=prompt_path.read_text(encoding="utf-8").strip(),
            registry_hash=registry["registry_hash"],
        )

    def assert_no_overrides(
        self,
        *,
        recall_fn=None,
        preflight_registry=None,
        prompt_override=None,
    ) -> None:
        if recall_fn is not None:
            raise PolicyRuntimeViolation("formal runtime forbids free-text recall injection")
        if preflight_registry is not None:
            raise PolicyRuntimeViolation("formal runtime forbids manual skill registry")
        if prompt_override is not None:
            raise PolicyRuntimeViolation("formal runtime forbids prompt override")
