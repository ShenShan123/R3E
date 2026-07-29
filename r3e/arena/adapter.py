"""Structural interface for model/tool-specific evolution adapters."""
from __future__ import annotations

from typing import Any, Iterable, Protocol

from r3e.policy.schema import PolicyState


class EvolutionAdapter(Protocol):
    """The runner's non-authoritative environment boundary.

    Implementations may call models and EDA tools. They cannot select the
    active policy, mutate manifests, decide promotion, or write the registry.
    """

    # Exact ``r3e-adapter-toolchain-v1`` identity. Every method result must
    # carry the operation-specific ``r3e-evolution-adapter-v1`` provenance
    # envelope validated by ``AdapterConformanceGate``.
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


class BlueCandidateProvider(Protocol):
    """Non-authoritative ACP provider boundary.

    A provider may return a patch proposal and provider usage receipt. It may
    not parse, verify, rank, select, or declare candidate correctness.
    """

    toolchain_fingerprint: dict[str, Any]

    def generate_candidate(
        self,
        *,
        policy: PolicyState,
        current_case_evidence: dict[str, Any],
        slot: dict[str, Any],
        prompt_asset: str,
        prompt_hash: str,
        candidate_id: str,
    ) -> dict[str, Any]: ...
