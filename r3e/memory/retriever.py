"""Deterministic high-precision retrieval over active-dormant memories."""
from __future__ import annotations

from typing import Any

from r3e.protocol.hashing import hash_payload

from .memory_store import MemoryStore
from .schema import ActiveMemoryBank, FailureDescriptor, MemoryMatch


class MemoryRetrievalViolation(RuntimeError):
    """Raised when retrieval attempts to escape the active bank."""


def _matches(trigger_value: Any, descriptor_value: Any) -> bool:
    if isinstance(trigger_value, list):
        return bool(set(trigger_value) & set(descriptor_value or []))
    return trigger_value == descriptor_value


class MemoryRetriever:
    schema_version = "r3e-memory-retriever-v1"

    def __init__(self, store: MemoryStore, *, minimum_precision: float = 1.0):
        if not 0 < minimum_precision <= 1:
            raise ValueError("minimum_precision must be in (0, 1]")
        self.store = store
        self.minimum_precision = minimum_precision

    @property
    def retriever_hash(self) -> str:
        return hash_payload({
            "schema_version": self.schema_version,
            "minimum_precision": self.minimum_precision,
            "matching": "exact-runtime-observable-v1",
        })

    def retrieve(
        self,
        descriptor: FailureDescriptor,
        active_bank: ActiveMemoryBank,
        *,
        top_k: int = 3,
    ) -> list[MemoryMatch]:
        if not isinstance(descriptor, FailureDescriptor):
            raise MemoryRetrievalViolation("retrieval requires a validated FailureDescriptor")
        if not 1 <= top_k <= 3:
            raise MemoryRetrievalViolation("formal retrieval top_k must be between 1 and 3")
        if active_bank.retriever_hash != self.retriever_hash:
            raise MemoryRetrievalViolation("active bank retriever hash mismatch")
        matches = []
        for memory_id, binding in active_bank.memories.items():
            memory = self.store.get_version(memory_id, binding["memory_version"])
            if memory.memory_hash != binding["memory_hash"]:
                raise MemoryRetrievalViolation("bank memory hash mismatch")
            if self.store.current_status(memory_id, memory.memory_version) != "active_dormant":
                raise MemoryRetrievalViolation("bank contains non-executable memory")
            compared = list(memory.trigger_predicate)
            matched = [
                field for field in compared
                if field in descriptor.features
                and _matches(memory.trigger_predicate[field], descriptor.features[field])
            ]
            score = len(matched) / len(compared)
            if score >= self.minimum_precision:
                matches.append(
                    MemoryMatch(
                        memory_id=memory_id,
                        memory_version=memory.memory_version,
                        memory_hash=memory.memory_hash,
                        score=score,
                        matched_fields=tuple(sorted(matched)),
                    )
                )
        return sorted(
            matches,
            key=lambda match: (-match.score, match.memory_id, match.memory_version),
        )[:top_k]
