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


class OpenAICompatibleProviderViolation(RuntimeError):
    """Raised when provider configuration or output is not auditable."""


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
    def _strict_object(text: str) -> dict[str, Any]:
        if not isinstance(text, str) or not text.strip():
            raise OpenAICompatibleProviderViolation(
                "provider returned empty content"
            )
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise OpenAICompatibleProviderViolation(
                "provider content is not strict JSON"
            ) from exc
        if not isinstance(value, dict):
            raise OpenAICompatibleProviderViolation(
                "provider JSON output must be an object"
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
    ) -> dict[str, Any]:
        if (
            not messages
            or any(set(row) != {"role", "content"} for row in messages)
        ):
            raise OpenAICompatibleProviderViolation(
                "provider messages schema mismatch"
            )
        request = {
            "model": self.config.model_id,
            "messages": deepcopy(messages),
            "temperature": self.config.temperature,
            "max_tokens": self.config.maximum_output_tokens,
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
        if set(raw) != required:
            raise OpenAICompatibleProviderViolation(
                "provider transport result fields mismatch"
            )
        for field in ("input_tokens", "output_tokens"):
            value = raw[field]
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise OpenAICompatibleProviderViolation(
                    f"provider {field} must be non-negative"
                )
        result = self._strict_object(str(raw["content"]))
        return {
            "result": result,
            "raw_response_hash": hash_payload({
                "content": raw["content"],
                "provider_request_id": raw["provider_request_id"],
            }),
            "input_tokens": raw["input_tokens"],
            "output_tokens": raw["output_tokens"],
            "request_hash": request_hash,
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
            return {
                "content": payload["choices"][0]["message"]["content"],
                "input_tokens": int(
                    usage.get("prompt_tokens") or 0
                ),
                "output_tokens": int(
                    usage.get("completion_tokens") or 0
                ),
                "provider_request_id": str(payload.get("id") or ""),
            }
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise OpenAICompatibleProviderViolation(
                "real provider response envelope is invalid"
            ) from exc
