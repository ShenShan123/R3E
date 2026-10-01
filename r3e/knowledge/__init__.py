"""Experience-derived repair knowledge for the Blue agent.

Pipeline:
1. Visible feedback and the buggy RTL produce an observable profile
   (bug status and causal relation).
2. Blue's verified repair yields a bug type and an anonymized example.
3. Verified episodes are authored into knowledge cards.
4. The store applies a lifecycle; the matcher selects items softly, using
   status, causal relation and the inferred bug type.
5. The selected cards go into Blue's prompt through ``KnowledgeInjectingClient``,
   which needs no change to the frozen Blue executor or provider.

See ``docs/design/R3E_Core_Contributions_Spec.md`` §3 for the contract.
"""
from .author import DeterministicKnowledgeAuthor, LlmKnowledgeAuthor
from .blue_client import (
    KnowledgeInjectingClient,
    build_delivery_receipt,
    execute_with_knowledge,
    verify_delivery_receipt,
)
from .delivery import build_bundle, render_item
from .diff_classifier import classify_repair
from .episodes import build_repair_episode
from .feedback import compare_traces, compile_failure
from .matcher import KnowledgeMatcher
from .profile import build_profile
from .schema import (
    KnowledgeBundle,
    KnowledgeItem,
    KnowledgeValidationError,
    ObservableProfile,
    RepairEpisode,
    VisibleFeedback,
)
from .store import KnowledgeStore
from .structure import analyze_rtl
from .type_inference import BugTypeInference

__all__ = [
    "BugTypeInference",
    "DeterministicKnowledgeAuthor",
    "KnowledgeBundle",
    "KnowledgeInjectingClient",
    "KnowledgeItem",
    "KnowledgeMatcher",
    "KnowledgeStore",
    "KnowledgeValidationError",
    "LlmKnowledgeAuthor",
    "ObservableProfile",
    "RepairEpisode",
    "VisibleFeedback",
    "analyze_rtl",
    "build_bundle",
    "build_delivery_receipt",
    "build_profile",
    "build_repair_episode",
    "classify_repair",
    "compare_traces",
    "compile_failure",
    "execute_with_knowledge",
    "render_item",
    "verify_delivery_receipt",
]
