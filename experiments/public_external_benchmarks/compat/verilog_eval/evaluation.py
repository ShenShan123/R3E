"""Pass@k primitive from VerilogEval 1.0 without importing its broken runner."""
from __future__ import annotations

import itertools

import numpy as np


def estimate_pass_at_k(num_samples, num_correct, k: int) -> np.ndarray:
    def estimator(n: int, c: int) -> float:
        if n - c < k:
            return 1.0
        return 1.0 - np.prod(1.0 - k / np.arange(n - c + 1, n + 1))

    samples = itertools.repeat(num_samples, len(num_correct)) if isinstance(num_samples, int) else iter(num_samples)
    return np.array([estimator(int(n), int(c)) for n, c in zip(samples, num_correct)])
