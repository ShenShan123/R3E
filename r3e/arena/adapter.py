"""Structural interface for model/tool-specific evolution adapters."""
from __future__ import annotations

from typing import Any, Iterable, Protocol

from r3e.policy.schema import PolicyState


class EvolutionAdapter(Protocol):
    """The runner's non-authoritative environment boundary.

    Implementations may call models and EDA tools. They cannot select the
    active policy, mutate manifests, decide promotion, or write the registry.
    """

    toolchain_fingerprint: dict[str, Any]

    def generate_red(
        self,
        parent: PolicyState,
        config: dict[str, Any],
        red_search_context: dict[str, Any],
    ) -> Iterable[dict[str, Any]]: ...

    def prepare_validity(self, poison: dict[str, Any]) -> dict[str, Any]: ...

    def evaluate_blue(
        self, policy: PolicyState, poison: dict[str, Any], seed: int
    ) -> dict[str, Any]: ...

    def probe_learnability(
        self, policy: PolicyState, poison: dict[str, Any]
    ) -> dict[str, Any]: ...

    def screen_child(
        self,
        parent: PolicyState,
        child: PolicyState,
        adaptation_manifest: dict[str, Any],
    ) -> dict[str, Any]: ...

    def replay(
        self, policy: PolicyState, case: dict[str, Any], seed: int
    ) -> dict[str, Any]: ...
