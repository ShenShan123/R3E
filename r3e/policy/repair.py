"""Minimal formal repair interface driven exclusively by PolicyState."""
from __future__ import annotations

from pathlib import Path

from r3e.policy.schema import PolicyState
from r3e.policy.runtime import PolicyRuntimeViolation


def repair_one(
    case: dict,
    work_dir: str | Path,
    policy: PolicyState,
) -> dict:
    """Repair one case under the exact validated policy, fail closed."""
    if not isinstance(policy, PolicyState):
        raise TypeError("formal repair requires PolicyState")
    if policy.memory_binding:
        raise PolicyRuntimeViolation(
            "memory-bound policy requires r3e.memory.runtime.MemoryRuntime; "
            "default formal repair cannot silently ignore an active memory bank"
        )
    # Imported lazily so formal schema/registry tooling does not initialize
    # legacy compatibility modules merely by importing r3e.policy.
    from r3e.semantic_repair_bench.functional_repair import repair_one as execute

    return execute(
        case,
        work_dir,
        policy=policy,
        formal_mode=True,
    )
