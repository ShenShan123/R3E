"""Adaptive Candidate Portfolio protocol foundation."""

from .candidate_executor import CandidatePortfolioExecutor
from .allocator import (
    OfflineAdaptiveAllocator,
    OfflineAllocatorState,
    build_adaptive_portfolio,
    build_offline_allocator_state,
    load_offline_allocator_state,
)
from .fake import DeterministicFakeCandidateProvider, DeterministicFakeCandidateVerifier
from .openai_provider import OpenAICompatibleCandidateProvider
from .oracle_gate_verifier import (
    PublicManifestOracleVerifier,
    public_case_from_manifest,
)
from .lens_registry import load_lens_registry
from .router import DescriptorRouter, load_descriptor_router
from .semantic_signature import (
    ParserBackedSemanticSignatureProvider,
    SemanticPatchSignature,
    StructuredSemanticSignatureProvider,
)
from .rtl_ast_materializer import materialize_rtl_ast_semantic_patch
from .schema import (
    AllocationPlan,
    CandidatePortfolio,
    LensDefinition,
    LensRegistry,
    build_candidate_portfolio_binding,
    build_implicit_homogeneous_portfolio,
)

__all__ = [
    "AllocationPlan",
    "CandidatePortfolio",
    "CandidatePortfolioExecutor",
    "OfflineAdaptiveAllocator",
    "OfflineAllocatorState",
    "OpenAICompatibleCandidateProvider",
    "ParserBackedSemanticSignatureProvider",
    "PublicManifestOracleVerifier",
    "DeterministicFakeCandidateProvider",
    "DeterministicFakeCandidateVerifier",
    "DescriptorRouter",
    "LensDefinition",
    "LensRegistry",
    "SemanticPatchSignature",
    "StructuredSemanticSignatureProvider",
    "build_candidate_portfolio_binding",
    "build_adaptive_portfolio",
    "build_implicit_homogeneous_portfolio",
    "build_offline_allocator_state",
    "load_lens_registry",
    "load_descriptor_router",
    "load_offline_allocator_state",
    "materialize_rtl_ast_semantic_patch",
    "public_case_from_manifest",
]
