"""One-call, strict-JSON OpenAI-compatible transport.

Credentials are referenced by environment-variable name and are never copied
into receipts or configuration artifacts.  The transport performs no retry:
one authorized adapter call corresponds to at most one provider request.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import os
from typing import Any, Callable, Mapping
from urllib import error as urllib_error
from urllib import request as urllib_request

from r3e.protocol.hashing import hash_payload


CLIENT_CONFIG_SCHEMA = "r3e-openai-compatible-client-config-v1"
RESPONSE_DIAGNOSTIC_SCHEMA = (
    "r3e-openai-compatible-response-diagnostic-v1"
)
_OPTIONAL_RESPONSE_FIELDS = {"finish_reason", "refusal"}
_SAFE_FINISH_REASONS = {
    "stop",
    "length",
    "tool_calls",
    "function_call",
    "content_filter",
    "null",
}


class OpenAICompatibleProviderViolation(RuntimeError):
    """Raised when provider configuration or output is not auditable."""

    def __init__(
        self,
        message: str,
        *,
        diagnostics: Mapping[str, Any] | None = None,
    ):
        super().__init__(message)
        self.diagnostics = sanitize_provider_diagnostics(diagnostics)


class OpenAICompatibleEmptyContentViolation(
    OpenAICompatibleProviderViolation
):
    """Raised when a provider returns no assistant content."""


def sanitize_provider_diagnostics(
    raw: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return the fixed, privacy-safe response diagnostic subset.

    Diagnostics are deliberately limited to categorical envelope metadata and
    token counters.  Provider content, prompts, request URLs, credentials and
    arbitrary provider fields never cross this boundary.
    """
    if not isinstance(raw, Mapping):
        return {}
    allowed = {
        "schema_version",
        "content_state",
        "finish_reason",
        "refusal_present",
        "provider_request_id_present",
        "input_tokens",
        "output_tokens",
    }
    payload = {key: raw[key] for key in allowed if key in raw}
    required = {
        "schema_version",
        "content_state",
        "finish_reason",
        "refusal_present",
        "provider_request_id_present",
        "input_tokens",
        "output_tokens",
    }
    if set(payload) != required:
        return {}
    if payload.get("schema_version") != RESPONSE_DIAGNOSTIC_SCHEMA:
        return {}
    if payload.get("content_state") not in {
        None,
        "missing_content",
        "null_content",
        "blank_content",
        "invalid_content_type",
        "nonempty_content",
    }:
        return {}
    if payload.get("finish_reason") not in {
        None,
        "missing",
        *_SAFE_FINISH_REASONS,
        "other",
    }:
        return {}
    for key in ("refusal_present", "provider_request_id_present"):
        if key in payload and not isinstance(payload[key], bool):
            return {}
    for key in ("input_tokens", "output_tokens"):
        value = payload.get(key)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
        ):
            return {}
    return payload


@dataclass(frozen=True)
class OpenAICompatibleClientConfig:
    provider_id: str
    provider_version: str
    endpoint_id: str
    model_id: str
    model_version: str
    api_key_env: str
    base_url_env: str
    timeout_seconds: float
    maximum_output_tokens: int
    temperature: float = 0.0
    require_seed: bool = True

    @classmethod
    def from_dict(
        cls, raw: Mapping[str, Any]
    ) -> "OpenAICompatibleClientConfig":
        payload = deepcopy(dict(raw))
        required = {
            "schema_version",
            "provider_id",
            "provider_version",
            "endpoint_id",
            "model_id",
            "model_version",
            "api_key_env",
            "base_url_env",
            "timeout_seconds",
            "maximum_output_tokens",
            "temperature",
            "require_seed",
        }
        if set(payload) != required:
            raise OpenAICompatibleProviderViolation(
                "OpenAI-compatible client config fields mismatch"
            )
        if payload.pop("schema_version") != CLIENT_CONFIG_SCHEMA:
            raise OpenAICompatibleProviderViolation(
                "OpenAI-compatible client config schema mismatch"
            )
        for field in (
            "provider_id",
            "provider_version",
            "endpoint_id",
            "model_id",
            "model_version",
            "api_key_env",
            "base_url_env",
        ):
            if not isinstance(payload.get(field), str) or not payload[field]:
                raise OpenAICompatibleProviderViolation(
                    f"{field} must be a non-empty string"
                )
        if "KEY" not in payload["api_key_env"].upper():
            raise OpenAICompatibleProviderViolation(
                "api_key_env must name a credential environment variable"
            )
        if float(payload["timeout_seconds"]) <= 0:
            raise OpenAICompatibleProviderViolation(
                "timeout_seconds must be positive"
            )
        if (
            isinstance(payload["maximum_output_tokens"], bool)
            or int(payload["maximum_output_tokens"]) <= 0
        ):
            raise OpenAICompatibleProviderViolation(
                "maximum_output_tokens must be positive"
            )
        temperature = float(payload["temperature"])
        if temperature < 0 or temperature > 2:
            raise OpenAICompatibleProviderViolation(
                "temperature must be in [0, 2]"
            )
        if not isinstance(payload["require_seed"], bool):
            raise OpenAICompatibleProviderViolation(
                "require_seed must be boolean"
            )
        return cls(
            **{
                **payload,
                "timeout_seconds": float(payload["timeout_seconds"]),
                "maximum_output_tokens": int(
                    payload["maximum_output_tokens"]
                ),
                "temperature": temperature,
            }
        )

    @property
    def public_identity(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "endpoint_id": self.endpoint_id,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "timeout_seconds": self.timeout_seconds,
            "maximum_output_tokens": self.maximum_output_tokens,
            "temperature": self.temperature,
            "require_seed": self.require_seed,
        }

    @property
    def config_hash(self) -> str:
        return hash_payload(self.public_identity)


class OpenAICompatibleJSONClient:
    """Make one strict-JSON request and return content plus usage."""

    def __init__(
        self,
        config: OpenAICompatibleClientConfig,
        *,
        transport: Callable[..., Mapping[str, Any]] | None = None,
        environ: Mapping[str, str] | None = None,
    ):
        self.config = config
        self._transport = transport
        self._environ = environ if environ is not None else os.environ

    def readiness(self) -> dict[str, Any]:
        credential_present = bool(
            self._environ.get(self.config.api_key_env)
        )
        endpoint_present = bool(
            self._environ.get(self.config.base_url_env)
        )
        return {
            "schema_version": "r3e-real-provider-readiness-v1",
            "provider_config_hash": self.config.config_hash,
            "credential_env": self.config.api_key_env,
            "base_url_env": self.config.base_url_env,
            "credential_present": credential_present,
            "base_url_present": endpoint_present,
            "ready": credential_present and endpoint_present,
        }

    @staticmethod
    def _response_diagnostics(raw: Mapping[str, Any]) -> dict[str, Any]:
        if "content" not in raw:
            content_state = "missing_content"
        elif raw.get("content") is None:
            content_state = "null_content"
        elif not isinstance(raw.get("content"), str):
            content_state = "invalid_content_type"
        elif not raw["content"].strip():
            content_state = "blank_content"
        else:
            content_state = "nonempty_content"
        reason = raw.get("finish_reason")
        if reason is None:
            finish_reason = "missing"
        elif isinstance(reason, str) and reason in _SAFE_FINISH_REASONS:
            finish_reason = reason
        else:
            finish_reason = "other"
        return {
            "schema_version": RESPONSE_DIAGNOSTIC_SCHEMA,
            "content_state": content_state,
            "finish_reason": finish_reason,
            "refusal_present": bool(raw.get("refusal")),
            "provider_request_id_present": bool(
                str(raw.get("provider_request_id") or "")
            ),
            "input_tokens": (
                raw.get("input_tokens")
                if isinstance(raw.get("input_tokens"), int)
                and not isinstance(raw.get("input_tokens"), bool)
                and raw.get("input_tokens") >= 0
                else None
            ),
            "output_tokens": (
                raw.get("output_tokens")
                if isinstance(raw.get("output_tokens"), int)
                and not isinstance(raw.get("output_tokens"), bool)
                and raw.get("output_tokens") >= 0
                else None
            ),
        }

    @staticmethod
    def _strict_object(
        text: Any,
        *,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        safe_diagnostics = sanitize_provider_diagnostics(diagnostics)
        if not isinstance(text, str) or not text.strip():
            raise OpenAICompatibleEmptyContentViolation(
                "provider returned empty content",
                diagnostics=safe_diagnostics,
            )
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise OpenAICompatibleProviderViolation(
                "provider content is not strict JSON",
                diagnostics=safe_diagnostics,
            ) from exc
        if not isinstance(value, dict):
            raise OpenAICompatibleProviderViolation(
                "provider JSON output must be an object",
                diagnostics=safe_diagnostics,
            )
        return value

    @staticmethod
    def _safe_error_label(exc: Exception) -> str:
        """Expose only exception classes and numeric HTTP statuses."""
        labels = []
        current: BaseException | None = exc
        seen: set[int] = set()
        while current is not None and len(labels) < 4:
            if id(current) in seen:
                break
            seen.add(id(current))
            label = type(current).__name__
            status = getattr(current, "status_code", None)
            if (
                isinstance(status, int)
                and not isinstance(status, bool)
                and 100 <= status <= 599
            ):
                label += f":status={status}"
            labels.append(label)
            current = current.__cause__ or current.__context__
        return "->".join(labels)

    def complete_json(
        self,
        *,
        messages: list[dict[str, str]],
        seed: int,
        maximum_output_tokens: int | None = None,
    ) -> dict[str, Any]:
        if (
            not messages
            or any(set(row) != {"role", "content"} for row in messages)
        ):
            raise OpenAICompatibleProviderViolation(
                "provider messages schema mismatch"
            )
        request_output_tokens = self.config.maximum_output_tokens
        if maximum_output_tokens is not None:
            if (
                isinstance(maximum_output_tokens, bool)
                or not isinstance(maximum_output_tokens, int)
                or maximum_output_tokens <= 0
                or maximum_output_tokens > self.config.maximum_output_tokens
            ):
                raise OpenAICompatibleProviderViolation(
                    "requested output budget exceeds client authority"
                )
            request_output_tokens = maximum_output_tokens
        request = {
            "model": self.config.model_id,
            "messages": deepcopy(messages),
            "temperature": self.config.temperature,
            "max_tokens": request_output_tokens,
            "response_format": {"type": "json_object"},
        }
        if self.config.require_seed:
            request["seed"] = int(seed)
        request_hash = hash_payload({
            "provider_config_hash": self.config.config_hash,
            "request": request,
        })
        if self._transport is not None:
            raw = dict(self._transport(**deepcopy(request)))
        else:
            readiness = self.readiness()
            if not readiness["ready"]:
                raise OpenAICompatibleProviderViolation(
                    "real provider environment is not ready"
                )
            try:
                from openai import OpenAI
            except ImportError:
                raw = self._stdlib_complete(request)
            else:
                client = OpenAI(
                    api_key=self._environ[self.config.api_key_env],
                    base_url=self._environ[self.config.base_url_env],
                    timeout=self.config.timeout_seconds,
                    max_retries=0,
                )
                try:
                    response = client.chat.completions.create(**request)
                    raw = {
                        "content": response.choices[0].message.content,
                        "finish_reason": getattr(
                            response.choices[0], "finish_reason", None
                        ),
                        "refusal": getattr(
                            response.choices[0].message, "refusal", None
                        ),
                        "input_tokens": int(
                            getattr(
                                response.usage, "prompt_tokens", 0
                            )
                            or 0
                        ),
                        "output_tokens": int(
                            getattr(
                                response.usage, "completion_tokens", 0
                            )
                            or 0
                        ),
                        "provider_request_id": str(
                            getattr(response, "id", "") or ""
                        ),
                    }
                except Exception as exc:
                    raise OpenAICompatibleProviderViolation(
                        "real provider request failed: "
                        + self._safe_error_label(exc)
                    ) from exc
            try:
                raw = dict(raw)
            except Exception as exc:
                raise OpenAICompatibleProviderViolation(
                    "real provider request failed"
                ) from exc
        required = {
            "content",
            "input_tokens",
            "output_tokens",
            "provider_request_id",
        }
        if (
            not required <= set(raw)
            or not set(raw) <= required | _OPTIONAL_RESPONSE_FIELDS
        ):
            raise OpenAICompatibleProviderViolation(
                "provider transport result fields mismatch",
                diagnostics=self._response_diagnostics(raw),
            )
        for field in ("input_tokens", "output_tokens"):
            value = raw[field]
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise OpenAICompatibleProviderViolation(
                    f"provider {field} must be non-negative",
                    diagnostics=self._response_diagnostics(raw),
                )
        result = self._strict_object(
            raw["content"],
            diagnostics=self._response_diagnostics(raw),
        )
        return {
            "result": result,
            "raw_response_hash": hash_payload({
                "content": raw["content"],
                "provider_request_id": raw["provider_request_id"],
            }),
            "input_tokens": raw["input_tokens"],
            "output_tokens": raw["output_tokens"],
            "request_hash": request_hash,
            "response_diagnostics": self._response_diagnostics(raw),
        }

    def _stdlib_complete(
        self,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Execute one no-retry OpenAI-compatible request with stdlib HTTP."""
        base = self._environ[self.config.base_url_env].rstrip("/")
        endpoint = (
            base
            if base.endswith("/chat/completions")
            else f"{base}/chat/completions"
        )
        body = json.dumps(
            request,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        http_request = urllib_request.Request(
            endpoint,
            data=body,
            headers={
                "Authorization": (
                    "Bearer "
                    + self._environ[self.config.api_key_env]
                ),
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib_request.urlopen(
                http_request,
                timeout=self.config.timeout_seconds,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            urllib_error.HTTPError,
            urllib_error.URLError,
        ) as exc:
            raise OpenAICompatibleProviderViolation(
                "real provider request failed"
            ) from exc
        try:
            usage = payload.get("usage") or {}
            choice = payload["choices"][0]
            message = choice["message"]
            return {
                "content": message.get("content"),
                "finish_reason": choice.get("finish_reason"),
                "refusal": message.get("refusal"),
                "input_tokens": int(
                    usage.get("prompt_tokens") or 0
                ),
                "output_tokens": int(
                    usage.get("completion_tokens") or 0
                ),
                "provider_request_id": str(payload.get("id") or ""),
            }
        except (
            AttributeError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
        ) as exc:
            raise OpenAICompatibleProviderViolation(
                "real provider response envelope is invalid"
            ) from exc
