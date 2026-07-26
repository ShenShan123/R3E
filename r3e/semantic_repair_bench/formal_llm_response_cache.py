"""Hash-addressed common-response cache for paired formal policy arms."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from typing import Callable

from semantic_repair_bench.formal_protocol import atomic_write_json, hash_payload, utc_now


def cached_call_llm(prompt: str, call: Callable[[str], object], cache_root: str | Path) -> object:
    """Return one model response per frozen prompt/config key across paired arms.

    Secrets and endpoint credentials are deliberately excluded from artifacts.
    The cache controls stochastic calls for identical parent-policy prompts; a
    strategy-modified prompt naturally receives a different key.
    """
    config = {
        "prompt": prompt,
        "model": os.environ.get("LLM_MODEL") or os.environ.get("DEEPSEEK_MODEL"),
        "temperature": os.environ.get("LLM_TEMPERATURE"),
        "max_tokens": os.environ.get("LLM_MAX_TOKENS"),
        "seed": os.environ.get("LLM_SEED"),
    }
    key = hash_payload(config)
    root = Path(cache_root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{key}.json"
    lock_path = root / f"{key}.lock"
    with lock_path.open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if path.is_file():
            row = json.loads(path.read_text())
            if row.get("request_hash") != key or row.get("request") != config:
                raise RuntimeError("LLM common-response cache collision")
            if row.get("response_hash") != hash_payload(row.get("response")):
                raise RuntimeError("LLM common-response cache response hash mismatch")
            return row["response"]
        response = call(prompt)
        atomic_write_json(path, {
            "schema": "r3e-formal-common-response-cache-v1",
            "request": config,
            "request_hash": key,
            "response": response,
            "response_hash": hash_payload(response),
            "created_at": utc_now(),
            "credentials_recorded": False,
        })
        return response
