"""Request-size counting for DeepSeek models with a fixed tokenizer (offline).

The counter is DeepSeek's published tokenizer (``DeepSeek-V3`` ``tokenizer.json``,
pinned by SHA-256), plus a constant request overhead. Calibration (2026-10-06):
16 Blue requests from the Part B run were rebuilt offline byte for byte, and on
all 16 the provider-reported prompt tokens equalled the tokenizer count of the
messages + 52 (the chat template). This is consistency on those requests, not a
guarantee for every request shape or a future server-side tokenizer: record the
provider's counts next to this one and check them. Bytes / 4 was off by up to
23% on the same requests, so it is never used.

The tokenizer file lives outside the repository (``R3E_TOKENIZER`` or
``../R3E-local-artifacts/tokenizers/``); a wrong hash is refused.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Mapping, Sequence

TOKENIZER_NAME = "deepseek-ai/DeepSeek-V3 tokenizer.json"
TOKENIZER_SHA256 = "621ac2e32d0dba658404412318818aaa8ce8cda492e59830109d8da6b517fb41"
REQUEST_OVERHEAD = 52  # provider prompt tokens - tokenizer count, constant over the calibration set


def default_tokenizer_path() -> Path:
    env = os.environ.get("R3E_TOKENIZER")
    if env:
        return Path(env)
    root = Path(__file__).resolve().parents[2]
    return root.parent / "R3E-local-artifacts" / "tokenizers" / "deepseek-ai_DeepSeek-V3.tokenizer.json"


class TokenCounter:
    def __init__(self, path: Path | None = None):
        from tokenizers import Tokenizer  # optional dependency, needed only for repository tasks

        path = Path(path or default_tokenizer_path())
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != TOKENIZER_SHA256:
            raise ValueError(f"tokenizer {path} does not match the pinned hash")
        self._tok = Tokenizer.from_file(str(path))
        self.identity = {"tokenizer": TOKENIZER_NAME, "sha256": TOKENIZER_SHA256,
                         "request_overhead": REQUEST_OVERHEAD}

    def count(self, text: str) -> int:
        return len(self._tok.encode(text, add_special_tokens=False).ids)

    def request(self, messages: Sequence[Mapping[str, str]]) -> int:
        return sum(self.count(m["content"]) for m in messages) + REQUEST_OVERHEAD
