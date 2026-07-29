"""Runner-owned semantic patch signatures for ACP diversity measurement."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Mapping

from r3e.protocol.hashing import hash_payload


SIGNATURE_SCHEMA = "r3e-semantic-patch-signature-v1"
SIGNATURE_RECEIPT_SCHEMA = "r3e-candidate-semantic-signature-v1"
LEGACY_MEASURE_ONLY_PROVIDER_HASH = hash_payload({
    "provider": "measure-only-v1",
})
STRUCTURED_SIGNATURE_PROVIDER_HASH = hash_payload({
    "provider": "runner-owned-structured-semantic-patch-v1",
    "schema_version": SIGNATURE_SCHEMA,
    "source": "parser-validated-candidate-patch-proposal",
})
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_PATCH_FIELDS = {
    "changed_modules",
    "changed_blocks",
    "changed_ast_nodes",
    "changed_signal_roles",
    "operator_classes",
    "patch_scope",
    "normalized_ast_patch",
}


class SemanticSignatureViolation(RuntimeError):
    """Raised when semantic patch evidence is malformed or self-reported."""


def _digest(value: Any, field: str) -> str:
    text = str(value or "")
    if not _HASH_RE.fullmatch(text):
        raise SemanticSignatureViolation(
            f"{field} must be an exact sha256 digest"
        )
    return text


def _normalized_strings(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise SemanticSignatureViolation(f"{field} must be a list")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise SemanticSignatureViolation(
                f"{field} entries must be non-empty strings"
            )
        result.append(item.strip())
    if len(result) != len(set(result)):
        raise SemanticSignatureViolation(f"{field} contains duplicates")
    return tuple(sorted(result))


@dataclass(frozen=True)
class SemanticPatchSignature:
    changed_modules: tuple[str, ...]
    changed_blocks: tuple[str, ...]
    changed_ast_nodes: tuple[str, ...]
    changed_signal_roles: tuple[str, ...]
    operator_classes: tuple[str, ...]
    patch_scope: str
    normalized_ast_patch_hash: str
    signature_hash: str
    schema_version: str = SIGNATURE_SCHEMA

    @classmethod
    def create(
        cls,
        *,
        changed_modules: list[str],
        changed_blocks: list[str],
        changed_ast_nodes: list[str],
        changed_signal_roles: list[str],
        operator_classes: list[str],
        patch_scope: str,
        normalized_ast_patch: Any,
    ) -> "SemanticPatchSignature":
        scope = str(patch_scope or "")
        if not scope:
            raise SemanticSignatureViolation("patch_scope must be non-empty")
        payload = {
            "schema_version": SIGNATURE_SCHEMA,
            "changed_modules": list(
                _normalized_strings(changed_modules, "changed_modules")
            ),
            "changed_blocks": list(
                _normalized_strings(changed_blocks, "changed_blocks")
            ),
            "changed_ast_nodes": list(
                _normalized_strings(changed_ast_nodes, "changed_ast_nodes")
            ),
            "changed_signal_roles": list(
                _normalized_strings(
                    changed_signal_roles, "changed_signal_roles"
                )
            ),
            "operator_classes": list(
                _normalized_strings(operator_classes, "operator_classes")
            ),
            "patch_scope": scope,
            "normalized_ast_patch_hash": hash_payload(
                deepcopy(normalized_ast_patch)
            ),
        }
        return cls(
            changed_modules=tuple(payload["changed_modules"]),
            changed_blocks=tuple(payload["changed_blocks"]),
            changed_ast_nodes=tuple(payload["changed_ast_nodes"]),
            changed_signal_roles=tuple(payload["changed_signal_roles"]),
            operator_classes=tuple(payload["operator_classes"]),
            patch_scope=scope,
            normalized_ast_patch_hash=payload[
                "normalized_ast_patch_hash"
            ],
            signature_hash=hash_payload(payload),
        )

    @classmethod
    def from_dict(
        cls, raw: Mapping[str, Any]
    ) -> "SemanticPatchSignature":
        expected = {
            "schema_version",
            "changed_modules",
            "changed_blocks",
            "changed_ast_nodes",
            "changed_signal_roles",
            "operator_classes",
            "patch_scope",
            "normalized_ast_patch_hash",
            "signature_hash",
        }
        if set(raw) != expected:
            raise SemanticSignatureViolation(
                "semantic patch signature fields mismatch"
            )
        if raw["schema_version"] != SIGNATURE_SCHEMA:
            raise SemanticSignatureViolation(
                "semantic patch signature schema mismatch"
            )
        payload = {
            "schema_version": SIGNATURE_SCHEMA,
            "changed_modules": list(
                _normalized_strings(
                    raw["changed_modules"], "changed_modules"
                )
            ),
            "changed_blocks": list(
                _normalized_strings(raw["changed_blocks"], "changed_blocks")
            ),
            "changed_ast_nodes": list(
                _normalized_strings(
                    raw["changed_ast_nodes"], "changed_ast_nodes"
                )
            ),
            "changed_signal_roles": list(
                _normalized_strings(
                    raw["changed_signal_roles"], "changed_signal_roles"
                )
            ),
            "operator_classes": list(
                _normalized_strings(
                    raw["operator_classes"], "operator_classes"
                )
            ),
            "patch_scope": str(raw["patch_scope"] or ""),
            "normalized_ast_patch_hash": _digest(
                raw["normalized_ast_patch_hash"],
                "normalized_ast_patch_hash",
            ),
        }
        if not payload["patch_scope"]:
            raise SemanticSignatureViolation(
                "patch_scope must be non-empty"
            )
        if raw["signature_hash"] != hash_payload(payload):
            raise SemanticSignatureViolation(
                "semantic patch signature hash mismatch"
            )
        return cls(
            changed_modules=tuple(payload["changed_modules"]),
            changed_blocks=tuple(payload["changed_blocks"]),
            changed_ast_nodes=tuple(payload["changed_ast_nodes"]),
            changed_signal_roles=tuple(
                payload["changed_signal_roles"]
            ),
            operator_classes=tuple(payload["operator_classes"]),
            patch_scope=payload["patch_scope"],
            normalized_ast_patch_hash=payload[
                "normalized_ast_patch_hash"
            ],
            signature_hash=raw["signature_hash"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "changed_modules": list(self.changed_modules),
            "changed_blocks": list(self.changed_blocks),
            "changed_ast_nodes": list(self.changed_ast_nodes),
            "changed_signal_roles": list(self.changed_signal_roles),
            "operator_classes": list(self.operator_classes),
            "patch_scope": self.patch_scope,
            "normalized_ast_patch_hash": self.normalized_ast_patch_hash,
            "signature_hash": self.signature_hash,
        }


class StructuredSemanticSignatureProvider:
    """Parse a structured patch proposal without trusting model/verifier hashes."""

    def __init__(self, *, provider_hash: str = STRUCTURED_SIGNATURE_PROVIDER_HASH):
        if provider_hash not in {
            LEGACY_MEASURE_ONLY_PROVIDER_HASH,
            STRUCTURED_SIGNATURE_PROVIDER_HASH,
        }:
            raise SemanticSignatureViolation(
                "unknown semantic signature provider identity"
            )
        self.provider_hash = provider_hash

    def materialize(
        self,
        *,
        candidate_id: str,
        lens_id: str,
        patch_hash: str,
        patch_payload: Mapping[str, Any],
        expected_patch_scope: str,
    ) -> dict[str, Any]:
        if not isinstance(patch_payload, Mapping):
            raise SemanticSignatureViolation(
                "candidate patch payload must be an object"
            )
        proposal = patch_payload.get("semantic_patch")
        if not isinstance(proposal, Mapping) or set(proposal) != _PATCH_FIELDS:
            raise SemanticSignatureViolation(
                "semantic_patch proposal fields mismatch"
            )
        if proposal["patch_scope"] != expected_patch_scope:
            raise SemanticSignatureViolation(
                "semantic patch scope exceeds active policy"
            )
        signature = SemanticPatchSignature.create(
            changed_modules=proposal["changed_modules"],
            changed_blocks=proposal["changed_blocks"],
            changed_ast_nodes=proposal["changed_ast_nodes"],
            changed_signal_roles=proposal["changed_signal_roles"],
            operator_classes=proposal["operator_classes"],
            patch_scope=proposal["patch_scope"],
            normalized_ast_patch=proposal["normalized_ast_patch"],
        )
        payload = {
            "schema_version": SIGNATURE_RECEIPT_SCHEMA,
            "candidate_id": str(candidate_id),
            "lens_id": str(lens_id),
            "patch_hash": _digest(patch_hash, "patch_hash"),
            "provider_hash": self.provider_hash,
            "signature": signature.to_dict(),
        }
        if not payload["candidate_id"]:
            raise SemanticSignatureViolation(
                "candidate_id must be non-empty"
            )
        if not payload["lens_id"]:
            raise SemanticSignatureViolation("lens_id must be non-empty")
        payload["receipt_hash"] = hash_payload(payload)
        return payload


def verify_semantic_signature_receipt(
    raw: Mapping[str, Any],
    *,
    expected_provider_hash: str,
) -> dict[str, Any]:
    payload = deepcopy(dict(raw))
    expected = {
        "schema_version",
        "candidate_id",
        "lens_id",
        "patch_hash",
        "provider_hash",
        "signature",
        "receipt_hash",
    }
    if set(payload) != expected:
        raise SemanticSignatureViolation(
            "semantic signature receipt fields mismatch"
        )
    if payload["schema_version"] != SIGNATURE_RECEIPT_SCHEMA:
        raise SemanticSignatureViolation(
            "semantic signature receipt schema mismatch"
        )
    if payload["provider_hash"] != expected_provider_hash:
        raise SemanticSignatureViolation(
            "semantic signature provider binding mismatch"
        )
    _digest(payload["patch_hash"], "patch_hash")
    SemanticPatchSignature.from_dict(payload["signature"])
    expected_hash = hash_payload({
        key: value for key, value in payload.items()
        if key != "receipt_hash"
    })
    if payload["receipt_hash"] != expected_hash:
        raise SemanticSignatureViolation(
            "semantic signature receipt hash mismatch"
        )
    return payload


def create_semantic_signature_provider(
    config: Mapping[str, Any],
) -> StructuredSemanticSignatureProvider:
    declared = str(
        config.get("blue_semantic_signature_provider_hash")
        or STRUCTURED_SIGNATURE_PROVIDER_HASH
    )
    return StructuredSemanticSignatureProvider(provider_hash=declared)
