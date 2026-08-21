"""Formal external-provider boundaries.

Provider modules may perform network calls, but they never own verifier,
selection, archive, or promotion authority.
"""

from .openai_compatible import (
    OpenAICompatibleClientConfig,
    OpenAICompatibleEmptyContentViolation,
    OpenAICompatibleJSONClient,
    OpenAICompatibleProviderViolation,
)

__all__ = [
    "OpenAICompatibleClientConfig",
    "OpenAICompatibleEmptyContentViolation",
    "OpenAICompatibleJSONClient",
    "OpenAICompatibleProviderViolation",
]
