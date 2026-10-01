"""Deliver a knowledge bundle to the real Blue provider without changing it.

``KnowledgeInjectingClient`` wraps the OpenAI-compatible JSON client that
``OpenAICompatibleCandidateProvider`` already uses. While a bundle is active,
the wrapper adds that bundle's prompt payload to every Blue candidate request,
under ``retrieved_repair_knowledge``.

Integrity follows from the existing bindings:
- the inner client hashes the exact messages it sends (``request_hash``);
- the Blue provider binds that hash into each candidate receipt's
  ``command_hash``.

``verify_delivery_receipt`` recomputes those command hashes, so an audit can
prove which knowledge Blue saw for each candidate. Frozen Blue files (the
executor, provider and audit) are untouched; the global candidate budget and
the provider call count are unchanged.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from typing import Any, Iterator, Mapping

from r3e.protocol.hashing import canonical_json, hash_payload

from .schema import KnowledgeBundle, KnowledgeValidationError


REQUEST_KEY = "retrieved_repair_knowledge"
DELIVERY_SCHEMA_VERSION = "r3e-knowledge-delivery-receipt-v1"
_BLUE_REQUEST_MARKERS = ("current_buggy_rtl", "lens_instruction")


class KnowledgeInjectingClient:
    def __init__(self, inner: Any):
        if not callable(getattr(inner, "complete_json", None)):
            raise KnowledgeValidationError("inner client lacks complete_json")
        self.inner = inner
        self.config = inner.config
        self._bundle: KnowledgeBundle | None = None
        self.deliveries: list[dict[str, Any]] = []

    def readiness(self) -> Mapping[str, Any]:
        return self.inner.readiness()

    @contextmanager
    def active(self, bundle: KnowledgeBundle) -> Iterator[None]:
        if not isinstance(bundle, KnowledgeBundle):
            raise KnowledgeValidationError("active knowledge must be a KnowledgeBundle")
        if self._bundle is not None:
            raise KnowledgeValidationError("a knowledge bundle is already active")
        self._bundle = bundle
        try:
            yield
        finally:
            self._bundle = None

    def complete_json(
        self,
        *,
        messages: list[dict[str, str]],
        seed: int,
        maximum_output_tokens: int | None = None,
    ) -> dict[str, Any]:
        bundle = self._bundle
        injected = False
        if bundle is not None and not bundle.is_empty and messages:
            try:
                user = json.loads(messages[-1]["content"])
            except (TypeError, ValueError):
                user = None
            if isinstance(user, dict) and all(k in user for k in _BLUE_REQUEST_MARKERS):
                if REQUEST_KEY in user:
                    raise KnowledgeValidationError("request already carries knowledge")
                user[REQUEST_KEY] = bundle.prompt_payload()
                messages = [
                    *messages[:-1],
                    {"role": messages[-1]["role"], "content": canonical_json(user)},
                ]
                injected = True
        kwargs: dict[str, Any] = {"messages": messages, "seed": seed}
        if maximum_output_tokens is not None:
            kwargs["maximum_output_tokens"] = maximum_output_tokens
        response = self.inner.complete_json(**kwargs)
        if bundle is not None:
            self.deliveries.append({
                "bundle_hash": bundle.bundle_hash,
                "mode": bundle.mode,
                "injected": injected,
                "request_hash": str(response.get("request_hash", "")),
            })
        return response


def execute_with_knowledge(
    executor: Any,
    client: KnowledgeInjectingClient,
    bundle: KnowledgeBundle,
    **execute_kwargs: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run one Blue case with ``bundle`` active; return (evaluation, receipt)."""
    start = len(client.deliveries)
    with client.active(bundle):
        evaluation = executor.execute(**execute_kwargs)
    receipt = build_delivery_receipt(bundle, evaluation, client.deliveries[start:])
    return evaluation, receipt


def build_delivery_receipt(
    bundle: KnowledgeBundle,
    evaluation: Mapping[str, Any],
    deliveries: list[Mapping[str, Any]],
) -> dict[str, Any]:
    receipt = {
        "schema_version": DELIVERY_SCHEMA_VERSION,
        "case_id": str(evaluation.get("case_id", "")),
        "bundle": bundle.to_dict(),
        "bundle_hash": bundle.bundle_hash,
        "prompt_payload_hash": bundle.prompt_payload_hash,
        "portfolio_execution_hash": evaluation["portfolio_execution_hash"],
        "deliveries": [dict(row) for row in deliveries],
    }
    receipt["receipt_hash"] = hash_payload(receipt)
    return receipt


def verify_delivery_receipt(
    receipt: Mapping[str, Any], evaluation: Mapping[str, Any]
) -> dict[str, Any]:
    """Check that the receipt matches the evaluation's provider receipts."""
    body = {k: v for k, v in receipt.items() if k != "receipt_hash"}
    if receipt.get("receipt_hash") != hash_payload(body):
        raise KnowledgeValidationError("delivery receipt hash mismatch")
    if receipt.get("portfolio_execution_hash") != evaluation.get("portfolio_execution_hash"):
        raise KnowledgeValidationError("delivery receipt is bound to another evaluation")
    provider_receipts = evaluation.get("candidate_provider_receipts") or []
    deliveries = receipt.get("deliveries") or []
    if len(deliveries) != len(provider_receipts):
        raise KnowledgeValidationError("delivery count differs from provider calls")
    for delivery, provider in zip(deliveries, provider_receipts):
        if delivery["bundle_hash"] != receipt["bundle_hash"]:
            raise KnowledgeValidationError("delivery bound to another bundle")
        expected_command = hash_payload({
            "provider_request_hash": delivery["request_hash"],
            "prompt_hash": provider["prompt_hash"],
            "current_case_artifact_hash": provider["current_case_artifact_hash"],
        })
        if provider.get("command_hash") != expected_command:
            raise KnowledgeValidationError("provider command hash does not cover delivered request")
    empty = not receipt["bundle"]["items"]
    if any(d["injected"] == empty for d in deliveries):
        raise KnowledgeValidationError("injection flag contradicts bundle content")
    return dict(receipt)
