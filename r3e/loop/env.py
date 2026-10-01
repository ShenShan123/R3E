"""Read provider credentials from an ``.llm`` env file without exposing them.

The file path is never hard-coded. It comes from an explicit argument or from
the ``R3E_LLM_ENV_FILE`` environment variable. Values are parsed into a private
mapping that is passed to the client as its ``environ``. They are never written
into ``os.environ``, logs, ledgers or exceptions; only variable *names* are
ever reported.
"""
from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Mapping

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


def build_client(
    env: Mapping[str, str],
    *,
    prefix: str = "DEEPSEEK",
    model_override: str | None = None,
    maximum_output_tokens: int = 8192,
    timeout_seconds: float = 180.0,
    temperature: float = 0.2,
    require_seed: bool = False,
    transport=None,
) -> OpenAICompatibleJSONClient:
    """OpenAI-compatible client whose credentials come only from ``env``."""
    key_name, url_name, model_name = (
        f"{prefix}_API_KEY", f"{prefix}_BASE_URL", f"{prefix}_MODEL",
    )
    model = model_override or env.get(model_name)
    if not model:
        raise LlmEnvError(f"{model_name} is not set in the .llm env file")
    if transport is None:
        missing = [n for n in (key_name, url_name) if not env.get(n)]
        if missing:
            raise LlmEnvError(f"missing in the .llm env file: {missing}")
    config = OpenAICompatibleClientConfig.from_dict({
        "schema_version": "r3e-openai-compatible-client-config-v1",
        "provider_id": prefix.lower(),
        "provider_version": "1",
        "endpoint_id": f"{prefix.lower()}-env-endpoint",
        "model_id": model,
        "model_version": "env-declared",
        "api_key_env": key_name,
        "base_url_env": url_name,
        "timeout_seconds": timeout_seconds,
        "maximum_output_tokens": maximum_output_tokens,
        "temperature": temperature,
        "require_seed": require_seed,
    })
    return OpenAICompatibleJSONClient(config, transport=transport, environ=dict(env))
