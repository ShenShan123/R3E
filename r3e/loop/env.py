"""Read provider credentials from an ``.llm`` env file without exposing them.

The file path is never hard-coded. It comes from an explicit argument or from
the ``R3E_LLM_ENV_FILE`` environment variable. Values are parsed into a private
mapping that is passed to the client as its ``environ``. They are never written
into ``os.environ``, logs, ledgers or exceptions; only variable *names* are
ever reported.
"""
from __future__ import annotations

from decimal import Decimal
import hashlib
import json
import os
import re
import shlex
import time
from pathlib import Path
from typing import Any, Mapping

from r3e.providers.openai_compatible import (
    OpenAICompatibleClientConfig,
    OpenAICompatibleJSONClient,
)


ENV_FILE_VARIABLE = "R3E_LLM_ENV_FILE"
_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


class LlmEnvError(RuntimeError):
    """Raised without ever including credential values."""


def resolve_env_file(path: str | os.PathLike[str] | None = None) -> Path:
    raw = path or os.environ.get(ENV_FILE_VARIABLE)
    if not raw:
        raise LlmEnvError(
            f"no .llm env file given (pass a path or set {ENV_FILE_VARIABLE})"
        )
    resolved = Path(raw).expanduser()
    if not resolved.is_file():
        raise LlmEnvError("the .llm env file does not exist")
    return resolved


def load_llm_env(
    path: str | os.PathLike[str] | None = None,
    *,
    names: tuple[str, ...] | None = None,
) -> dict[str, str]:
    """Parse ``NAME=value`` lines; later definitions win, as in a shell."""
    values: dict[str, str] = {}
    for line in resolve_env_file(path).read_text(encoding="utf-8").splitlines():
        match = _LINE.match(line)
        if not match:
            continue
        name, raw_value = match.groups()
        if names is not None and name not in names:
            continue
        try:
            parts = shlex.split(raw_value, comments=True, posix=True)
        except ValueError as exc:
            raise LlmEnvError(f"cannot parse value of {name}") from exc
        values[name] = parts[0] if parts else ""
    return values


THINKING_CHOICES = ("default", "disabled", "low", "high", "max")


def recover_json_object(content: Any, transport: Any = None) -> Any:
    """Strip text or a code fence around one JSON object; never rewrite it.

    JSON mode still occasionally wraps the object (```json ... ```), adds a
    sentence, or closes with a stray extra ``}`` (seen with DeepSeek V4 Flash,
    2026-10-02). Return the first complete JSON object starting at the first
    ``{``, unchanged. Otherwise return the content unchanged (the
    client then rejects it) and keep a copy in ``failure_dir`` for diagnosis.
    """
    if not isinstance(content, str):
        return content
    try:
        json.loads(content)
        return content
    except json.JSONDecodeError:
        pass
    start = content.find("{")
    if start >= 0:
        try:  # the first complete object; trailing text or a stray "}" is dropped
            value, length = json.JSONDecoder().raw_decode(content[start:])
            if isinstance(value, dict):
                if transport is not None:
                    transport.recovered += 1
                return content[start:start + length]
        except json.JSONDecodeError:
            pass
    directory = getattr(transport, "failure_dir", None)
    if directory is not None:
        directory.mkdir(parents=True, exist_ok=True)
        name = hashlib.sha256(content.encode("utf-8", "replace")).hexdigest()[:16]
        (directory / f"unparseable_{name}.txt").write_text(content, encoding="utf-8")
    return content


AUTH_BILLING_STATUS = (401, 402, 403)
CONSECUTIVE_FAILURE_LIMIT = 3


class ProviderHalt(RuntimeError):
    """Collection-wide stop: an auth/billing error, or three failed requests in a row.

    Deliberately NOT an ``OpenAICompatibleProviderViolation``, so no handler that
    tolerates provider failures (Blue infra retries, Red slot failures) absorbs it.
    """

    def __init__(self, reason: str, diagnostics: dict):
        super().__init__(reason)
        self.diagnostics = diagnostics


def _bounded(value: Any, secrets: tuple[str, ...], limit: int = 300) -> str | None:
    if value is None:
        return None
    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    text = re.sub(r"https?://\S+", "[url]", text)
    return " ".join(text.split())[:limit]


def provider_error_diagnostics(exc: BaseException, elapsed: float, secrets: tuple[str, ...]) -> dict:
    """A failed request, described without headers, request, prompt or credentials.

    Uses the SDK's parsed error fields only (never ``str(exc)``, which embeds the
    response body). Billing of a failed request is unknown, not zero.
    """
    body = getattr(exc, "body", None)
    error = body.get("error", body) if isinstance(body, dict) else {}
    error = error if isinstance(error, dict) else {}
    status = getattr(exc, "status_code", None)
    return {"cause": type(exc).__name__, "http_status": status if type(status) is int else None,
            "error_code": _bounded(error.get("code") or error.get("type"), secrets, 64),
            "error_message": _bounded(error.get("message"), secrets),
            "request_id": _bounded(getattr(exc, "request_id", None), secrets, 128),
            "elapsed_seconds": round(elapsed, 2), "billing": "unknown"}


def deepseek_balance(env: Mapping[str, str], required: Decimal | None = None, *,
                     key_name: str = "DEEPSEEK_API_KEY", url_name: str = "DEEPSEEK_BASE_URL",
                     currency: str = "CNY", timeout_seconds: float = 30.0) -> dict:
    """Free account query (GET /user/balance), no model call.

    With ``required``, raise unless the account is available and holds at least
    that much in ``currency``. An approved budget is not a funded balance.
    """
    import urllib.request

    base = env[url_name].rstrip("/").removesuffix("/v1")
    req = urllib.request.Request(base + "/user/balance",
                                 headers={"Authorization": "Bearer " + env[key_name]})
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as r:
            body = json.loads(r.read())
    except Exception as exc:
        raise LlmEnvError("balance query failed: " + type(exc).__name__
                          + (f" (HTTP {exc.code})" if hasattr(exc, "code") else "")) from None
    infos = [i for i in body.get("balance_infos", []) if i.get("currency") == currency]
    out = {"is_available": body.get("is_available") is True, "currency": currency,
           "total_balance": str(Decimal(infos[0]["total_balance"])) if infos else None}
    if required is not None and (not out["is_available"] or out["total_balance"] is None
                                 or Decimal(out["total_balance"]) < required):
        raise LlmEnvError(f"account cannot fund the approved envelope: {out}, required {required} {currency}")
    return out


class ThinkingTransport:
    """Real transport that adds DeepSeek thinking controls to each request.

    DeepSeek V4 thinks by default at ``high`` effort (api-docs.deepseek.com,
    "thinking mode"). ``disabled`` sends ``thinking: {"type": "disabled"}``,
    and ``low``/``high``/``max`` send ``reasoning_effort``. The frozen client
    still builds the request and computes its hash. The thinking setting is
    bound through the client config's ``model_version``. Credentials stay in
    ``env``.
    """

    def __init__(self, env: Mapping[str, str], *, key_name: str, url_name: str,
                 thinking: str, timeout_seconds: float, failure_dir: Path | None = None,
                 reasoning_log: Path | None = None, error_log: Path | None = None):
        if thinking not in THINKING_CHOICES or thinking == "default":
            raise ValueError("ThinkingTransport needs disabled/low/high/max")
        self._env = dict(env)
        self._key_name, self._url_name = key_name, url_name
        self.thinking = thinking
        self.timeout_seconds = timeout_seconds
        # where unparseable model content is kept for diagnosis (model output
        # only; requests and credentials are never written)
        self.failure_dir = Path(failure_dir) if failure_dir else None
        self.recovered = 0
        # diagnosis only: each call's reasoning text (returned by the provider
        # separately from the answer) is appended here; it is never shown to
        # any agent and never enters memory
        self.reasoning_log = Path(reasoning_log) if reasoning_log else None
        # sanitized failed-request records (see provider_error_diagnostics)
        self.error_log = Path(error_log) if error_log else None
        self.consecutive_failures = 0
        self.halted: dict | None = None

    def __call__(self, **request):
        from openai import OpenAI  # imported lazily; same SDK the client uses

        from r3e.providers.openai_compatible import OpenAICompatibleProviderViolation

        extra: dict = {}
        if self.thinking == "disabled":
            extra["extra_body"] = {"thinking": {"type": "disabled"}}
        else:
            extra["reasoning_effort"] = self.thinking
        if self.halted is not None:
            raise ProviderHalt("provider halted earlier; no further request", self.halted)
        client = OpenAI(api_key=self._env[self._key_name], base_url=self._env[self._url_name],
                        timeout=self.timeout_seconds, max_retries=0)
        started = time.monotonic()
        try:
            response = client.chat.completions.create(**request, **extra)
        except Exception as exc:
            diag = provider_error_diagnostics(exc, time.monotonic() - started,
                                              (self._env[self._key_name], self._env[self._url_name]))
            self.consecutive_failures += 1
            diag["consecutive_failures"] = self.consecutive_failures
            if diag["http_status"] in AUTH_BILLING_STATUS:
                diag["halt"] = "auth_or_billing"
            elif self.consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
                diag["halt"] = "consecutive_transport_failures"
            if self.error_log is not None:
                self.error_log.parent.mkdir(parents=True, exist_ok=True)
                with self.error_log.open("a", encoding="utf-8") as log:
                    log.write(json.dumps(diag) + "\n")
            if "halt" in diag:
                self.halted = diag
                raise ProviderHalt(diag["halt"], diag) from None
            failure = OpenAICompatibleProviderViolation(
                "real provider request failed: " + diag["cause"]
                + (f" (HTTP {diag['http_status']})" if diag["http_status"] else ""))
            failure.diagnostics = diag
            raise failure from None
        self.consecutive_failures = 0  # any received response, length-limited included
        choice = response.choices[0]
        if self.reasoning_log is not None:
            reasoning = getattr(choice.message, "reasoning_content", None) or ""
            self.reasoning_log.parent.mkdir(parents=True, exist_ok=True)
            with self.reasoning_log.open("a", encoding="utf-8") as log:
                log.write(json.dumps({
                    "provider_request_id": str(getattr(response, "id", "") or ""),
                    "finish_reason": getattr(choice, "finish_reason", None),
                    "output_tokens": int(getattr(response.usage, "completion_tokens", 0) or 0),
                    "answer_chars": len(choice.message.content or ""),
                    "reasoning": reasoning}) + "\n")
        content = recover_json_object(choice.message.content, self)
        return {
            "content": content,
            "finish_reason": getattr(choice, "finish_reason", None),
            "refusal": getattr(choice.message, "refusal", None),
            "input_tokens": int(getattr(response.usage, "prompt_tokens", 0) or 0),
            "output_tokens": int(getattr(response.usage, "completion_tokens", 0) or 0),
            "provider_request_id": str(getattr(response, "id", "") or ""),
        }


def build_client(
    env: Mapping[str, str],
    *,
    prefix: str = "DEEPSEEK",
    model_override: str | None = None,
    maximum_output_tokens: int = 8192,
    timeout_seconds: float = 180.0,
    temperature: float = 0.2,
    require_seed: bool = False,
    thinking: str = "default",
    transport=None,
    failure_dir: Path | None = None,
    reasoning_log: Path | None = None,
    error_log: Path | None = None,
) -> OpenAICompatibleJSONClient:
    """OpenAI-compatible client whose credentials come only from ``env``."""
    key_name, url_name, model_name = (
        f"{prefix}_API_KEY", f"{prefix}_BASE_URL", f"{prefix}_MODEL",
    )
    model = model_override or env.get(model_name)
    if not model:
        raise LlmEnvError(f"{model_name} is not set in the .llm env file")
    if thinking not in THINKING_CHOICES:
        raise LlmEnvError(f"thinking must be one of {THINKING_CHOICES}")
    if transport is None:
        missing = [n for n in (key_name, url_name) if not env.get(n)]
        if missing:
            raise LlmEnvError(f"missing in the .llm env file: {missing}")
        if thinking != "default":
            transport = ThinkingTransport(env, key_name=key_name, url_name=url_name,
                                          thinking=thinking, timeout_seconds=timeout_seconds,
                                          failure_dir=failure_dir, reasoning_log=reasoning_log,
                                          error_log=error_log)
    config = OpenAICompatibleClientConfig.from_dict({
        "schema_version": "r3e-openai-compatible-client-config-v1",
        "provider_id": prefix.lower(),
        "provider_version": "1",
        "endpoint_id": f"{prefix.lower()}-env-endpoint",
        "model_id": model,
        "model_version": f"env-declared;thinking={thinking}",
        "api_key_env": key_name,
        "base_url_env": url_name,
        "timeout_seconds": timeout_seconds,
        "maximum_output_tokens": maximum_output_tokens,
        "temperature": temperature,
        "require_seed": require_seed,
    })
    return OpenAICompatibleJSONClient(config, transport=transport, environ=dict(env))
