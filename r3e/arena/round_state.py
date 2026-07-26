"""Recoverable hash-bound evolution round state machine."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from r3e.protocol.hashing import atomic_write_json, hash_payload, read_json, utc_now


STAGES = [
    "INIT",
    "LOAD_ACTIVE_POLICY",
    "RED_GENERATE",
    "VALIDITY_GATE",
    "BLUE_CHALLENGE",
    "ARCHIVE_UPDATE",
    "FREEZE_RESIDUAL_MANIFEST",
    "SPLIT_ADAPT_TARGET",
    "PROPOSE_CHILDREN",
    "SCREEN_CHILDREN",
    "FREEZE_PROMOTION_MANIFEST",
    "PAIRED_REPLAY",
    "DECIDE",
    "ATOMIC_COMMIT",
    "RENEWED_CHALLENGE",
    "COMPLETE",
]


class RoundStateViolation(RuntimeError):
    """Raised when a round attempts to skip or rewrite a checkpoint."""


class RoundState:
    def __init__(self, path: str | Path, *, round_id: str):
        self.path = Path(path)
        self.round_id = round_id

    def initialize(self, config: dict[str, Any]) -> dict[str, Any]:
        if self.path.exists():
            return self.load()
        payload = {
            "schema_version": "r3e-round-state-v1",
            "round_id": self.round_id,
            "round_config_hash": hash_payload(config),
            "current_stage": "",
            "checkpoints": [],
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        payload["state_hash"] = self._hash(payload)
        atomic_write_json(self.path, payload)
        return payload

    @staticmethod
    def _hash(payload: dict[str, Any]) -> str:
        return hash_payload({
            key: value for key, value in payload.items()
            if key not in {"state_hash", "updated_at"}
        })

    def load(self) -> dict[str, Any]:
        payload = read_json(self.path)
        if payload.get("schema_version") != "r3e-round-state-v1":
            raise RoundStateViolation("round state schema mismatch")
        if payload.get("round_id") != self.round_id:
            raise RoundStateViolation("round id mismatch")
        if payload.get("state_hash") != self._hash(payload):
            raise RoundStateViolation("round state hash mismatch")
        return payload

    def complete_stage(
        self,
        stage: str,
        *,
        stage_input: Any,
        stage_output: Any,
    ) -> dict[str, Any]:
        if stage not in STAGES:
            raise RoundStateViolation(f"unknown round stage: {stage}")
        state = self.load()
        checkpoints = state["checkpoints"]
        expected_index = len(checkpoints)
        if expected_index >= len(STAGES):
            raise RoundStateViolation("round is already complete")
        expected = STAGES[expected_index]
        if stage != expected:
            raise RoundStateViolation(f"expected stage {expected}, got {stage}")
        checkpoint = {
            "stage": stage,
            "stage_input_hash": hash_payload(stage_input),
            "stage_output_hash": hash_payload(stage_output),
            "started_at": utc_now(),
            "completed_at": utc_now(),
            "status": "complete",
            "failure_reason": None,
        }
        checkpoints.append(checkpoint)
        state["current_stage"] = stage
        state["updated_at"] = utc_now()
        state["state_hash"] = self._hash(state)
        atomic_write_json(self.path, state)
        return self.load()

    def next_stage(self) -> str | None:
        state = self.load()
        index = len(state["checkpoints"])
        return STAGES[index] if index < len(STAGES) else None

    def verify_stage(
        self,
        stage: str,
        *,
        stage_input: Any | None = None,
        stage_output: Any,
    ) -> dict[str, Any]:
        """Verify a persisted artifact against an immutable checkpoint."""
        state = self.load()
        matches = [
            checkpoint
            for checkpoint in state["checkpoints"]
            if checkpoint["stage"] == stage
        ]
        if len(matches) != 1:
            raise RoundStateViolation(f"stage is not completed exactly once: {stage}")
        checkpoint = matches[0]
        if stage_input is not None and checkpoint["stage_input_hash"] != hash_payload(stage_input):
            raise RoundStateViolation(f"stage input hash mismatch: {stage}")
        if checkpoint["stage_output_hash"] != hash_payload(stage_output):
            raise RoundStateViolation(f"stage output hash mismatch: {stage}")
        return checkpoint
