"""Deliver retrieved memory to Blue's model requests.

``KnowledgeInjectingClient`` wraps the JSON client Blue's provider uses
(``r3e/loop/blue_provider.py``). While a bundle is active, it adds the
bundle's prompt payload to every Blue request under ``reference_cases``; other
requests (Red's) pass through untouched.

Integrity: the inner client hashes the exact messages it sends
(``request_hash``), and the provider binds that hash into each attempt's
``command_hash``. ``deliveries`` records, per call, which bundle was active
and whether it was injected.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from typing import Any, Iterator, Mapping

from r3e.protocol.hashing import canonical_json

from .schema import KnowledgeBundle, KnowledgeValidationError


REQUEST_KEY = "reference_cases"
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


