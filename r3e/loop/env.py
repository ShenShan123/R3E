"""Read provider credentials from an ``.llm`` env file without exposing them.

The file path is never hard-coded. It comes from an explicit argument or from
the ``R3E_LLM_ENV_FILE`` environment variable. Values are parsed into a private
mapping that is passed to the client as its ``environ``. They are never written
into ``os.environ``, logs, ledgers or exceptions; only variable *names* are
ever reported.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
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
                 reasoning_log: Path | None = None):
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

    def __call__(self, **request):
        from openai import OpenAI  # imported lazily; same SDK the client uses

        from r3e.providers.openai_compatible import OpenAICompatibleProviderViolation

        extra: dict = {}
        if self.thinking == "disabled":
            extra["extra_body"] = {"thinking": {"type": "disabled"}}
        else:
            extra["reasoning_effort"] = self.thinking
        client = OpenAI(api_key=self._env[self._key_name], base_url=self._env[self._url_name],
                        timeout=self.timeout_seconds, max_retries=0)
        try:
            response = client.chat.completions.create(**request, **extra)
        except Exception as exc:
            raise OpenAICompatibleProviderViolation(
                "real provider request failed: " + type(exc).__name__) from exc
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
                                          failure_dir=failure_dir, reasoning_log=reasoning_log)
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
