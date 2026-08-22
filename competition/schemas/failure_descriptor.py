"""Runtime-generated Failure Descriptor used by the Competition M2 facade.

The nested ``grounded_descriptor`` is the existing R³E Core descriptor passed
to ``r3e.blue.portfolio``.  The outer schema keeps the richer, presentation-
and-audit-facing evidence without asking the core router to trust free text.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Mapping

from ..services.common import payload_hash


FAILURE_DESCRIPTOR_SCHEMA = "r3e-aic-failure-descriptor-v2"
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class FailureDescriptorError(ValueError):
    """Raised when runtime diagnosis evidence cannot be reconstructed."""


@dataclass(frozen=True)
class FailureDescriptor:
    case_id: str
    failure_family: str
    first_divergence: dict[str, Any]
    raw_mismatch: str
    structured_evidence: str
    rtl_context: dict[str, Any]
    routing_features: dict[str, Any]
    authority: dict[str, Any]
    grounded_descriptor: dict[str, Any]
    descriptor_hash: str
    schema_version: str = FAILURE_DESCRIPTOR_SCHEMA

    @classmethod
    def create(
        cls,
        *,
        case_id: str,
        failure_family: str,
        first_divergence: Mapping[str, Any],
        raw_mismatch: str,
        structured_evidence: str,
        rtl_context: Mapping[str, Any],
        routing_features: Mapping[str, Any],
        authority: Mapping[str, Any],
        grounded_descriptor: Mapping[str, Any],
    ) -> "FailureDescriptor":
        if not case_id or not failure_family:
            raise FailureDescriptorError("case_id and failure_family are required")
        if not isinstance(first_divergence, Mapping):
            raise FailureDescriptorError("first_divergence must be an object")
        if not isinstance(rtl_context, Mapping) or not isinstance(routing_features, Mapping):
            raise FailureDescriptorError("RTL context and routing features must be objects")
        if not isinstance(authority, Mapping) or not isinstance(grounded_descriptor, Mapping):
            raise FailureDescriptorError("descriptor authority fields must be objects")
        body = {
            "schema_version": FAILURE_DESCRIPTOR_SCHEMA,
            "case_id": str(case_id),
            "failure_family": str(failure_family),
            "first_divergence": deepcopy(dict(first_divergence)),
            "raw_mismatch": str(raw_mismatch or ""),
            "structured_evidence": str(structured_evidence or ""),
            "rtl_context": deepcopy(dict(rtl_context)),
            "routing_features": deepcopy(dict(routing_features)),
            "authority": deepcopy(dict(authority)),
            "grounded_descriptor": deepcopy(dict(grounded_descriptor)),
        }
        return cls(
            case_id=body["case_id"],
            failure_family=body["failure_family"],
            first_divergence=body["first_divergence"],
            raw_mismatch=body["raw_mismatch"],
            structured_evidence=body["structured_evidence"],
            rtl_context=body["rtl_context"],
            routing_features=body["routing_features"],
            authority=body["authority"],
            grounded_descriptor=body["grounded_descriptor"],
            descriptor_hash=payload_hash(body),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "FailureDescriptor":
        payload = deepcopy(dict(raw))
        expected = {
            "schema_version", "case_id", "failure_family", "first_divergence",
            "raw_mismatch", "structured_evidence", "rtl_context",
            "routing_features", "authority", "grounded_descriptor",
            "descriptor_hash",
        }
        if set(payload) != expected:
            raise FailureDescriptorError("failure descriptor fields mismatch")
        if payload.pop("schema_version") != FAILURE_DESCRIPTOR_SCHEMA:
            raise FailureDescriptorError("failure descriptor schema mismatch")
        digest = payload.pop("descriptor_hash")
        if not isinstance(digest, str) or not _HASH_RE.fullmatch(digest):
            raise FailureDescriptorError("descriptor_hash must be sha256-prefixed")
        expected_hash = payload_hash({"schema_version": FAILURE_DESCRIPTOR_SCHEMA, **payload})
        if digest != expected_hash:
            raise FailureDescriptorError("failure descriptor hash mismatch")
        return cls.create(**payload)

    def to_dict(self) -> dict[str, Any]:
        body = {
            "schema_version": FAILURE_DESCRIPTOR_SCHEMA,
            "case_id": self.case_id,
            "failure_family": self.failure_family,
            "first_divergence": deepcopy(self.first_divergence),
            "raw_mismatch": self.raw_mismatch,
            "structured_evidence": self.structured_evidence,
            "rtl_context": deepcopy(self.rtl_context),
            "routing_features": deepcopy(self.routing_features),
            "authority": deepcopy(self.authority),
            "grounded_descriptor": deepcopy(self.grounded_descriptor),
        }
        body["descriptor_hash"] = self.descriptor_hash
        return body
