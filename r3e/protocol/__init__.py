"""Shared fail-closed protocol primitives for R³E policy evolution."""

from .hashing import (
    atomic_write_json,
    canonical_json,
    hash_file,
    hash_payload,
    read_json,
    utc_now,
)

__all__ = [
    "atomic_write_json",
    "canonical_json",
    "hash_file",
    "hash_payload",
    "read_json",
    "utc_now",
]
