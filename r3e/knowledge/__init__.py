"""Experience-derived repair knowledge for the Blue agent.

Pipeline:
1. Visible feedback and the buggy RTL produce an observable profile
   (bug status and causal relation).
2. Blue's verified repair yields its change, its own explanation and a bug type.
3. Verified episodes become case memory: causal chains (observed failure ->
   fault -> repair -> what did not work -> verification), with no authored
   rules (``cases.py``).
4. The store applies a lifecycle (inactive candidate, paired qualification,
   activation); the matcher retrieves items softly by status, causal relation
   and the inferred bug type, and reports where each agrees or differs.
5. Retrieved cases reach Blue as references through ``KnowledgeInjectingClient``,
   only after Blue's own first attempt (``BlueConfig.memory_from_attempt``).

See ``docs/design/R3E_Core_Contributions_Spec.md`` §3 for the contract.
"""
from .blue_client import (
    KnowledgeInjectingClient,
)
from .cases import CaseMemoryAuthor, causal_chain
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
    "CaseMemoryAuthor",
    "KnowledgeBundle",
    "KnowledgeInjectingClient",
    "KnowledgeItem",
    "KnowledgeMatcher",
    "KnowledgeStore",
    "KnowledgeValidationError",
    "ObservableProfile",
    "RepairEpisode",
    "VisibleFeedback",
    "analyze_rtl",
    "build_bundle",
    "build_profile",
    "build_repair_episode",
    "causal_chain",
    "classify_repair",
    "compare_traces",
    "compile_failure",
    "render_item",
]
