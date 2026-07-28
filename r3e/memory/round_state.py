"""Recoverable state for a memory-evolution sub-round."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from r3e.protocol.hashing import atomic_write_json, hash_payload, read_json, utc_now


MEMORY_ROUND_STAGES = [
    "LOAD_PARENT",
    "BUILD_CANDIDATES",
    "SHADOW_QUALIFY",
    "BUILD_BANK_CHILD",
    "PAIRED_REPLAY",
    "DECIDE",
    "ATOMIC_COMMIT",
    "COMPLETE",
]


class MemoryRoundStateViolation(RuntimeError):
    pass


class MemoryRoundState:
    def __init__(self, path: str | Path, *, round_id: str):
        self.path = Path(path)
        self.round_id = round_id

    @staticmethod
    def _hash(payload: dict[str, Any]) -> str:
        return hash_payload({
            key: value for key, value in payload.items()
            if key not in {"state_hash", "updated_at"}
        })

    def initialize(self, config: dict[str, Any]) -> dict[str, Any]:
        if self.path.exists():
            value = self.load()
            if value["config_hash"] != hash_payload(config):
                raise MemoryRoundStateViolation("memory round config changed")
            return value
        value = {
            "schema_version": "r3e-memory-round-state-v1",
            "round_id": self.round_id,
            "config_hash": hash_payload(config),
            "checkpoints": [],
            "updated_at": utc_now(),
        }
        value["state_hash"] = self._hash(value)
        atomic_write_json(self.path, value)
        return value

    def load(self) -> dict[str, Any]:
        value = read_json(self.path)
        if (
            value.get("schema_version") != "r3e-memory-round-state-v1"
            or value.get("round_id") != self.round_id
            or value.get("state_hash") != self._hash(value)
        ):
            raise MemoryRoundStateViolation("memory round state is invalid")
        return value

    def next_stage(self) -> str | None:
        index = len(self.load()["checkpoints"])
        return MEMORY_ROUND_STAGES[index] if index < len(MEMORY_ROUND_STAGES) else None

    def complete(self, stage: str, output: Any) -> None:
        value = self.load()
        expected = MEMORY_ROUND_STAGES[len(value["checkpoints"])]
        if stage != expected:
            raise MemoryRoundStateViolation(f"expected {expected}, got {stage}")
        value["checkpoints"].append({
            "stage": stage,
            "output_hash": hash_payload(output),
        })
        value["updated_at"] = utc_now()
        value["state_hash"] = self._hash(value)
        atomic_write_json(self.path, value)

    def verify(self, stage: str, output: Any) -> None:
        rows = [
            row for row in self.load()["checkpoints"]
            if row["stage"] == stage
        ]
        if len(rows) != 1 or rows[0]["output_hash"] != hash_payload(output):
            raise MemoryRoundStateViolation(
                f"memory round artifact mismatch: {stage}"
            )
