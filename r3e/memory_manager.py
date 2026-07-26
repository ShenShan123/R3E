"""
memory_manager.py — FluxEDA "Skill-heavy" Memory System
===========================================================
Phase 3 preliminary | R3E
Replaces dumb JSON dumps with structured, queryable skill artifacts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("R3E.memory")


class IntegrityError(Exception):
    """Raised when skill_index.jsonl fails SHA256 integrity check (tamper detected)."""
    pass


class SkillConflictError(Exception):
    """Raised when two skills in a chain have overlapping allowed_edit_scope."""
    pass


# ─────────────────────────────────────────────────────────────────────────────
#  Schema Constants
# ─────────────────────────────────────────────────────────────────────────────

# Similarity thresholds
SIM_EXACT_MATCH_SCORE   = 2.0   # exact signature match
SIM_PARTIAL_MATCH_SCORE = 1.0   # substring / partial overlap
SIM_FUZZY_MIN_THRESHOLD = 0.70 # minimum Jaccard similarity to consider fuzzy match

REQUIRED_SKILL_SCHEMA: dict[str, type | tuple[type, ...]] = {
    "skill_name":          str,
    "precondition":        dict,
    "action_template":     dict,
    "validation":          dict,
    "rollback_condition":  dict,
}

SAMPLE_SKILL_ENTRY: dict[str, Any] = {
    "skill_name": "fix_combinational_depth_on_critical_path",
    "precondition": {
        "error_signature": "WNS < 0",
        "context_pattern": "long logic chain with NAND/INV"
    },
    "action_template": {
        "repair_strategy": "pipeline_insertion_or_logic_refactoring",
        "allowed_edit_scope": ["datapath_module"]
    },
    "validation": {
        "backend_metric": "WNS_improvement > 0.05"
    },
    "rollback_condition": {
        "wns_degradation": True
    }
}


# ─────────────────────────────────────────────────────────────────────────────
#  Dataclass: SkillArtifact
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SkillArtifact:
    """
    Runtime representation of a distilled skill.
    Mirrors the JSON schema but with extra metadata for querying.
    """
    skill_name:           str
    precondition:         dict[str, Any]
    action_template:      dict[str, Any]
    validation:           dict[str, Any]
    rollback_condition:   dict[str, Any]
    case_id:              str | None      = None

    # Metadata (auto-populated)
    skill_hash:           str           = field(init=False)
    created_at:           float         = field(default_factory=time.time)
    success_count:        int           = 0
    failure_count:        int             = 0
    last_used:            float | None    = None

    def __post_init__(self) -> None:
        self.skill_hash = self._compute_hash()

    def _compute_hash(self) -> str:
        """Deterministic hash for deduplication / indexing."""
        canonical = json.dumps(
            [self.skill_name, self.precondition, self.action_template],
            sort_keys=True,
            ensure_ascii=True,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def to_json(self) -> dict[str, Any]:
        """Serialize to schema-compliant JSON dict."""
        d = {
            "skill_name":          self.skill_name,
            "case_id":             self.case_id,
            "precondition":        self.precondition,
            "action_template":     self.action_template,
            "validation":          self.validation,
            "rollback_condition":  self.rollback_condition,
            "_metadata": {
                "skill_hash":     self.skill_hash,
                "created_at":     self.created_at,
                "success_count":  self.success_count,
                "failure_count":  self.failure_count,
                "last_used":      self.last_used,
            }
        }
        return d

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> SkillArtifact:
        """Hydrate from JSON dict (with defensive stripping of metadata)."""
        data = dict(data)  # copy
        # Remove both _metadata and metadata (for backward compatibility)
        data.pop("_metadata", None)
        data.pop("metadata", None)
        return cls(**data)


# ─────────────────────────────────────────────────────────────────────────────
#  Main Manager Class
# ─────────────────────────────────────────────────────────────────────────────

class SkillMemoryManager:
    """
    High-cohesion, low-coupling Skill Memory Layer.

    Responsibilities
    ----------------
    1. distill_skill()    — Convert a raw repair trace into structured skill entry.
    2. query_skill()      — Retrieve applicable skill based on error signature + context.
    3. persist / load    — JSONL storage with atomic writes.

    Storage Format
    --------------
    <memory_root>/
        skill_index.jsonl   (append-only, each line = one SkillArtifact JSON)
        .skill_index_hash.sig (SHA256 hash of skill_index.jsonl + timestamp)
    """

    DEFAULT_MEMORY_ROOT = ".micro_surgeon_memory"
    SKILL_INDEX_FILE    = "skill_index.jsonl"
    HASH_SIG_FILE       = ".skill_index_hash.sig"
    FLUXEDA_VERSION     = "1.0"

    def __init__(
        self,
        memory_root: Path | str | None = None,
        auto_persist: bool = True,
        _skip_integrity_check: bool = False,  # set True ONLY in testing
    ):
        self.root = Path(memory_root or self.DEFAULT_MEMORY_ROOT)
        self.auto_persist = auto_persist
        self._skip_integrity_check = _skip_integrity_check
        self._in_memory_cache: dict[str, SkillArtifact] = {}   # skill_hash → artifact
        self._signature_index: dict[str, list[str]] = {}      # signature_key → list of skill_hashes

        self._ensure_directory()
        self._verify_integrity_on_load()   # ← 完整性校验
        self._load_index()

    # ─── Public API ─────────────────────────────────────────────────────────

    def distill_skill(
        self,
        precondition: dict[str, Any],
        action_template: dict[str, Any],
        validation: dict[str, Any] | None = None,
        rollback_condition: dict[str, Any] | None = None,
        skill_name: str | None = None,
        case_id: str | None = None,
    ) -> SkillArtifact:
        """
        Create (or retrieve) a skill artifact from raw patch data.

        Parameters
        ----------
        precondition : dict
            error_signature, context_pattern, etc.
        action_template : dict
            repair_strategy, allowed_edit_scope, etc.
        validation : dict, optional
            backend_metric thresholds.
        rollback_condition : dict, optional
            wns_degradation, drc_explosion flags.
        skill_name : str, optional
            If omitted, auto-generated from context_pattern + strategy.

        Returns
        -------
        SkillArtifact (hydrated, deduplicated)
        """
        if case_id is None:
            raise ValueError("distill_skill: case_id is required (三库重建强制 case_id 溯源)")
        # ── Defensive defaults ─────────────────────────────────────────────
        validation       = validation or {"backend_metric": "WNS_improvement > 0"}
        rollback_condition = rollback_condition or {"wns_degradation": True}

        # ── Auto-generate name if missing ──────────────────────────────────
        if not skill_name:
            ctx_pat = precondition.get("context_pattern", "unknown_context")
            strat   = action_template.get("repair_strategy", "unknown_strategy")
            skill_name = f"{self._slugify(ctx_pat)}__{self._slugify(strat)}"

        # ── Construct artifact ─────────────────────────────────────────────
        artifact = SkillArtifact(
            skill_name=skill_name,
            precondition=precondition,
            action_template=action_template,
            validation=validation,
            rollback_condition=rollback_condition,
            case_id=case_id,
            success_count=1,  # P1: Initialize with success_count=1 to avoid being filtered by min_success_ratio
            failure_count=0,
        )

        # ── Deduplication check ────────────────────────────────────────────
        if artifact.skill_hash in self._in_memory_cache:
            logger.debug(
                "Skill deduplicated (hash collision) — returning cached version."
            )
            cached = self._in_memory_cache[artifact.skill_hash]
            # Update usage metadata
            cached.last_used = time.time()
            return cached

        # ── Index & persist ────────────────────────────────────────────────
        self._in_memory_cache[artifact.skill_hash] = artifact
        self._index_by_signature(artifact)

        if self.auto_persist:
            self._append_to_jsonl(artifact)

        logger.info(
            "Distilled skill: %s (hash=%s  edit_scope=%s)",
            artifact.skill_name, artifact.skill_hash[:8],
            artifact.action_template.get("allowed_edit_scope", "?"),
        )
        return artifact

    def query_skill(
        self,
        error_signature: str | None = None,
        context_pattern: str | None = None,
        allowed_scope: list[str] | None = None,
        top_k: int = 3,
        min_success_ratio: float = 0.5,
        similarity_threshold: float = SIM_FUZZY_MIN_THRESHOLD,
        return_chain: bool = False,
    ) -> list[tuple[float, SkillArtifact, dict[str, Any]]] | list[list[tuple[float, SkillArtifact, dict[str, Any]]]]:
        """
        Retrieve skills that match the given error signature / context.

        Matching logic (flux-score descending):
            1. Exact error_signature match → +2.0
            2. Substring/overlap match on error_signature → +1.0
            3. Exact context_pattern match → +2.0
            4. Fuzzy similarity (Jaccard/cosine) ≥ 70% → +1.0 (boosted by similarity)
            5. Success ratio > min_success_ratio → +0.5
            6. More recent usage → tiny boost

        With similarity_threshold = 0.70, skills with ≥70% token overlap
        will be considered with a proportionally reduced score boost.

        Parameters
        ----------
        error_signature : str
            e.g. "WNS < -0.1"
        context_pattern : str
            e.g. "long logic chain with NAND/INV"
        allowed_scope : list[str], optional
            Filter skills whose allowed_edit_scope intersects with this.
        top_k : int
            Return at most top_k results.
        min_success_ratio : float
            Minimum success_count / (success+failure) to consider.
        similarity_threshold : float
            Minimum Jaccard/cosine similarity (0.0-1.0) to consider fuzzy match.
        return_chain : bool
            If True, group non-conflicting skills into chains and return
            the top chains instead of flat top_k list.

        Returns
        -------
        List[Tuple[score, artifact, match_info]] — flat list when return_chain=False.
        List[List[Tuple[score, artifact, match_info]]] — list of chains when return_chain=True.
            Each chain is a list of (score, artifact, match_info) tuples that are
            non-conflicting (their allowed_edit_scope does not overlap).
        """
        candidates: list[tuple[float, SkillArtifact, dict[str, Any]]] = []

        for artifact in self._in_memory_cache.values():
            # ── §3.6.2 failed skill 召回排除 ────────────────────────────
            # precondition.is_failed_skill==true 的技能不参与召回(防当修复
            # 配方喂给 LLM)。distill_skill 强制 success_count=1 无法靠
            # min_success_ratio 拦,故在此出口单点 filter。
            if artifact.precondition.get("is_failed_skill"):
                continue
            score = 0.0
            match_info: dict[str, Any] = {
                "sig_match_type": "none",
                "ctx_match_type": "none",
                "jaccard_sim": 0.0,
                "cosine_sim": 0.0,
                "rollback_tolerance": "normal",
            }

            # ── Signature match (exact → substring → fuzzy) ───────────────
            skill_sig = artifact.precondition.get("error_signature", "")
            if error_signature and skill_sig:
                if skill_sig == error_signature:
                    score += SIM_EXACT_MATCH_SCORE
                    match_info["sig_match_type"] = "exact"
                elif error_signature in skill_sig or skill_sig in error_signature:
                    score += SIM_PARTIAL_MATCH_SCORE
                    match_info["sig_match_type"] = "partial"
                else:
                    jaccard = self._jaccard_similarity(error_signature, skill_sig)
                    cosine = self._cosine_similarity_tokens(error_signature, skill_sig)
                    match_info["jaccard_sim"] = round(jaccard, 3)
                    match_info["cosine_sim"] = round(cosine, 3)
                    best_sim = max(jaccard, cosine)
                    if best_sim >= similarity_threshold:
                        fuzzy_boost = SIM_PARTIAL_MATCH_SCORE * best_sim
                        score += fuzzy_boost
                        match_info["sig_match_type"] = f"fuzzy({best_sim:.2f})"
                        match_info["rollback_tolerance"] = "strict"

            # ── Context pattern (exact → token overlap → fuzzy) ──────────────
            skill_ctx = artifact.precondition.get("context_pattern", "")
            if context_pattern and skill_ctx:
                ctx_lower = context_pattern.lower()
                pat_lower = skill_ctx.lower()
                if ctx_lower == pat_lower:
                    score += SIM_EXACT_MATCH_SCORE
                    match_info["ctx_match_type"] = "exact"
                elif self._pattern_match(context_pattern, skill_ctx):
                    score += SIM_PARTIAL_MATCH_SCORE
                    match_info["ctx_match_type"] = "partial"
                else:
                    jaccard = self._jaccard_similarity(context_pattern, skill_ctx)
                    cosine = self._cosine_similarity_tokens(context_pattern, skill_ctx)
                    match_info["ctx_jaccard"] = round(jaccard, 3)
                    match_info["ctx_cosine"] = round(cosine, 3)
                    best_sim = max(jaccard, cosine)
                    if best_sim >= similarity_threshold:
                        fuzzy_boost = SIM_PARTIAL_MATCH_SCORE * best_sim
                        score += fuzzy_boost
                        match_info["ctx_match_type"] = f"fuzzy({best_sim:.2f})"
                        match_info["rollback_tolerance"] = "strict"

            # ── Scope intersection ────────────────────────────────────────
            if allowed_scope:
                skill_scope = artifact.action_template.get("allowed_edit_scope", [])
                if skill_scope and not set(skill_scope).intersection(allowed_scope):
                    continue
                elif skill_scope:
                    score += 0.3

            # ── Success ratio filter ──────────────────────────────────────
            total = artifact.success_count + artifact.failure_count
            if total > 0:
                ratio = artifact.success_count / total
                if ratio < min_success_ratio:
                    # P1: Debug print to show why skill was filtered
                    logger.debug(
                        "Skill filtered by success_ratio: %s (ratio=%.2f < min=%.2f, success=%d, failure=%d)",
                        artifact.skill_name, ratio, min_success_ratio,
                        artifact.success_count, artifact.failure_count
                    )
                    continue
                score += 0.5 * ratio

            # ── Recency boost (decays over 7 days) ────────────────────────
            if artifact.last_used:
                days_ago = (time.time() - artifact.last_used) / 86400
                score += max(0, 0.1 - days_ago * 0.01)

            if score > 0:
                candidates.append((score, artifact, match_info))

        # ── Sort & slice ───────────────────────────────────────────────────
        candidates.sort(key=lambda x: x[0], reverse=True)

        # P1: Debug print to show query results and filtering
        logger.info(
            "query_skill: error_signature='%s', context_pattern='%s'",
            error_signature or "None", context_pattern or "None"
        )
        logger.info(
            "query_skill: found %d candidates (before top_k filter), min_success_ratio=%.2f",
            len(candidates), min_success_ratio
        )
        if candidates:
            logger.info("Top candidates:")
            for i, (score, artifact, match_info) in enumerate(candidates[:5]):
                logger.info(
                    "  [%d] score=%.2f, skill=%s, sig_match=%s, ctx_match=%s, success=%d, failure=%d",
                    i+1, score, artifact.skill_name,
                    match_info.get("sig_match_type", "none"),
                    match_info.get("ctx_match_type", "none"),
                    artifact.success_count, artifact.failure_count
                )

        if return_chain:
            chains = self._build_skill_chains(candidates)
            logger.info(
                "query_skill [chain mode] returned %d chains from %d candidates",
                len(chains), len(candidates)
            )
            return chains

        result = candidates[:top_k]
        logger.info(
            "query_skill returned %d/%d candidates (top_k=%d, sim_threshold=%.2f)",
            len(result), len(candidates), top_k, similarity_threshold
        )
        return result

    def _build_skill_chains(
        self,
        candidates: list[tuple[float, SkillArtifact, dict[str, Any]]],
        max_chain_len: int = 3,
    ) -> list[list[tuple[float, SkillArtifact, dict[str, Any]]]]:
        """
        Group candidates into non-conflicting skill chains.

        Two skills conflict if their allowed_edit_scope files overlap
        (i.e. both try to edit the same file's overlapping region).

        Strategy: Greedy longest-chain cover — sort by score desc, then for each
        skill try to extend every existing chain that it doesn't conflict with.
        Unchained singletons are still emitted as valid 1-element chains.

        Parameters
        ----------
        candidates : sorted list of (score, artifact, match_info) tuples
        max_chain_len : maximum number of skills per chain

        Returns
        -------
        List of chains, each chain is a list of (score, artifact, match_info).
        Ordered by total_chain_score descending.
        """
        if not candidates:
            return []

        # Separate singleton candidates (top-1 always valid as its own chain)
        chains: list[list[tuple[float, SkillArtifact, dict[str, Any]]]] = []

        for score, art, info in candidates:
            if not chains:
                chains.append([(score, art, info)])
                continue

            best_extension: list[tuple[float, SkillArtifact, dict[str, Any]]] | None = None
            best_ext_score = -1.0

            for chain in chains:
                if len(chain) >= max_chain_len:
                    continue
                # Check if this skill conflicts with any skill already in chain
                conflicts = False
                for _, existing_art, _ in chain:
                    if self._scope_overlaps(art, existing_art):
                        conflicts = True
                        break
                if not conflicts:
                    ext_score = sum(c[0] for c in chain) + score
                    if ext_score > best_ext_score:
                        best_ext_score = ext_score
                        best_extension = chain

            if best_extension is not None:
                best_extension.append((score, art, info))
            else:
                # Cannot extend any chain → start a new singleton chain
                chains.append([(score, art, info)])

        # Sort chains by total score descending
        chains.sort(key=lambda c: sum(x[0] for x in c), reverse=True)
        return chains

    @staticmethod
    def _scope_overlaps(a: SkillArtifact, b: SkillArtifact) -> bool:
        """
        Return True if skill A and skill B attempt to modify overlapping
        file regions.

        Overlap rules (in priority order):
        1. If file scopes intersect AND strategies are different → ORTHOGONAL (no conflict)
        2. If file scopes intersect AND same strategy → CONFLICT (same file + same strategy)
        3. If file scopes intersect AND both are "unknown" strategies → CONFLICT
        4. If no file overlap → always ORTHOGONAL

        Strategies in the "known safe" list (pipeline_stage, gate_sizing_up, etc.)
        are orthogonally safe only when they target DIFFERENT files.
        When they share the same file, they always conflict.
        """
        scope_a = set(a.action_template.get("allowed_edit_scope", []))
        scope_b = set(b.action_template.get("allowed_edit_scope", []))
        intersect = scope_a & scope_b
        has_overlap = bool(intersect)

        strat_a = a.action_template.get("repair_strategy", "")
        strat_b = b.action_template.get("repair_strategy", "")

        # ── No file overlap → always safe ─────────────────────────────────
        if not has_overlap:
            return False

        # ── Same file overlap ──────────────────────────────────────────────
        if strat_a == strat_b:
            # Same strategy on same file → always conflict
            return True

        # Different strategies on same file
        # Known-safe strategies (orthogonal by design) → still conflict if same file
        known_safe = {
            "pipeline_stage", "logic_refactoring",
            "gate_sizing_up", "gate_sizing_down",
            "buffer_insertion", "wire_extension",
        }
        if strat_a in known_safe and strat_b in known_safe:
            # Two different known-safe strategies on SAME file → conflict
            # (e.g. gate_sizing_up + buffer_insertion both on cells.v)
            return True

        # Different unknown strategies on same file → conflict
        return True

    def record_outcome(self, skill_hash: str, success: bool) -> None:
        """Update success/failure counters for a skill (feedback loop)."""
        if skill_hash not in self._in_memory_cache:
            logger.warning("Outcome recorded for unknown skill: %s", skill_hash)
            return
        art = self._in_memory_cache[skill_hash]
        if success:
            art.success_count += 1
        else:
            art.failure_count += 1
        art.last_used = time.time()
        logger.debug(
            "Skill %s outcome recorded: success=%s  (total s=%d f=%d)",
            skill_hash[:8], success, art.success_count, art.failure_count,
        )
        if self.auto_persist:
            self._rewrite_jsonl()  # naive: rewrite whole file; for scale use SQLite

    # ─── Persistence Layer ──────────────────────────────────────────────────

    def _ensure_directory(self) -> None:
        """Create memory root directory if missing."""
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            raise RuntimeError(f"Cannot create memory directory {self.root}: {exc}") from exc

    @property
    def _index_path(self) -> Path:
        return self.root / self.SKILL_INDEX_FILE

    @property
    def _hash_sig_path(self) -> Path:
        return self.root / self.HASH_SIG_FILE

    def _compute_file_hash(self) -> str:
        """Compute SHA256 hash of the current skill_index.jsonl file."""
        if not self._index_path.exists():
            return ""  # Empty file = empty hash sentinel
        sha256 = hashlib.sha256()
        with self._index_path.open("rb") as f:
            while chunk := f.read(8192):
                sha256.update(chunk)
        return sha256.hexdigest()

    def _save_hash_signature(self, hash_value: str) -> None:
        """Save hash signature with timestamp to .skill_index_hash.sig."""
        sig_data = {
            "hash": hash_value,
            "timestamp": time.time(),
            "file": self.SKILL_INDEX_FILE,
            "algorithm": "SHA256",
        }
        try:
            with self._hash_sig_path.open("w", encoding="utf-8") as f:
                json.dump(sig_data, f, ensure_ascii=False, indent=None)
                f.write("\n")
        except Exception as exc:
            logger.error("Failed to write hash signature: %s", exc)

    def _load_hash_signature(self) -> dict[str, Any] | None:
        """Load previously saved hash signature. Returns None if not found or corrupt."""
        if not self._hash_sig_path.exists():
            return None
        try:
            with self._hash_sig_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                return data
        except Exception as exc:
            logger.warning("Failed to load hash signature: %s", exc)
            return None

    def _verify_integrity_on_load(self) -> None:
        """
        Verify JSONL file integrity against saved hash.
        Raises IntegrityError on tampering detection (unless _skip_integrity_check=True).
        """
        if self._skip_integrity_check:
            logger.debug("Integrity check skipped (_skip_integrity_check=True)")
            return

        # If index file doesn't exist yet, nothing to verify
        if not self._index_path.exists():
            logger.debug("No index file yet — integrity check bypassed")
            return

        # If no hash file, we can't verify (first boot scenario — save hash after load)
        saved_sig = self._load_hash_signature()
        if saved_sig is None:
            logger.info(
                "No hash signature found at %s — computing baseline hash",
                self._hash_sig_path
            )
            current_hash = self._compute_file_hash()
            self._save_hash_signature(current_hash)
            return

        # Verify hash matches
        saved_hash = saved_sig.get("hash", "")
        current_hash = self._compute_file_hash()

        if saved_hash != current_hash:
            error_msg = (
                f"CRITICAL: Memory integrity check FAILED!\n"
                f"  Expected hash: {saved_hash[:32]}...\n"
                f"  Actual hash:   {current_hash[:32]}...\n"
                f"  File: {self._index_path}\n"
                f"  Last signed at: {saved_sig.get('timestamp', 'unknown')}\n\n"
                f"  Possible causes:\n"
                f"    - External tampering of skill_index.jsonl\n"
                f"    - Concurrent modification by another process\n"
                f"    - Disk corruption or partial write failure\n\n"
                f"  RECOMMENDATION: Restore skill_index.jsonl from backup or "
                f"  delete the file to start fresh (data loss risk)."
            )
            logger.critical(error_msg)
            raise IntegrityError(error_msg)

        logger.info(
            "Memory integrity verified: hash=%s... timestamp=%s",
            current_hash[:16], saved_sig.get('timestamp')
        )

    def _load_index(self) -> None:
        """Load existing skill_index.jsonl into memory cache."""
        if not self._index_path.exists():
            logger.debug("No existing skill index found — starting fresh.")
            return

        try:
            with self._index_path.open("r", encoding="utf-8") as f:
                for lineno, line in enumerate(f, 1):
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    try:
                        data = json.loads(line)
                        art = SkillArtifact.from_json(data)
                        # Hydrate metadata if present
                        meta = data.get("_metadata", {})
                        art.skill_hash      = meta.get("skill_hash", art._compute_hash())
                        art.created_at      = meta.get("created_at", time.time())
                        art.success_count   = meta.get("success_count", 0)
                        art.failure_count   = meta.get("failure_count", 0)
                        art.last_used       = meta.get("last_used", None)

                        self._in_memory_cache[art.skill_hash] = art
                        self._index_by_signature(art)
                    except json.JSONDecodeError as jde:
                        logger.warning("JSON parse error at line %d: %s", lineno, jde)
                    except Exception as exc:
                        logger.warning("Skipping malformed skill at line %d: %s", lineno, exc)
        except Exception as exc:
            logger.error("Failed to load skill index: %s", exc)
            # Defensive: start fresh, don't crash
            self._in_memory_cache.clear()
            self._signature_index.clear()

        logger.info(
            "Loaded %d skills from %s",
            len(self._in_memory_cache), self._index_path,
        )

    def _append_to_jsonl(self, artifact: SkillArtifact) -> None:
        """Atomic append (best-effort) of a single skill to JSONL."""
        tmp_path = self._index_path.with_suffix(".tmp")
        try:
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(artifact.to_json(), f, ensure_ascii=False, indent=None)
                f.write("\n")
            # Append mode: if index exists, copy old content first
            if self._index_path.exists():
                with self._index_path.open("r", encoding="utf-8") as f_src, \
                     tmp_path.open("a", encoding="utf-8") as f_dst:
                    f_dst.write(f_src.read())
            tmp_path.replace(self._index_path)
        except Exception as exc:
            logger.error("Failed to persist skill %s: %s", artifact.skill_hash[:8], exc)
            # Non-fatal: memory cache remains authoritative
            return
        # Update hash signature after successful write
        self._save_hash_signature(self._compute_file_hash())

    def _rewrite_jsonl(self) -> None:
        """Rewrite entire index (used sparingly — e.g., after outcome updates)."""
        tmp_path = self._index_path.with_suffix(".tmp")
        try:
            with tmp_path.open("w", encoding="utf-8") as f:
                for art in self._in_memory_cache.values():
                    json.dump(art.to_json(), f, ensure_ascii=False, indent=None)
                    f.write("\n")
            tmp_path.replace(self._index_path)
            logger.debug("Rewrote skill_index.jsonl (%d entries)", len(self._in_memory_cache))
        except Exception as exc:
            logger.error("Failed to rewrite skill index: %s", exc)
            return
        # Update hash signature after successful write
        self._save_hash_signature(self._compute_file_hash())

    # ─── Internal Indexing ────────────────────────────────────────────────────

    def _index_by_signature(self, artifact: SkillArtifact) -> None:
        """Add artifact to signature reverse index for fast lookup."""
        sig_key = artifact.precondition.get("error_signature", "_unknown")
        if sig_key not in self._signature_index:
            self._signature_index[sig_key] = []
        if artifact.skill_hash not in self._signature_index[sig_key]:
            self._signature_index[sig_key].append(artifact.skill_hash)

    # ─── Helper Utilities ───────────────────────────────────────────────────

    @staticmethod
    def _slugify(text: str) -> str:
        """Convert arbitrary string to filesystem-safe slug."""
        slug = re.sub(r"[^\w\s-]", "", text.lower())
        slug = re.sub(r"[-\s]+", "_", slug).strip("_")[:40]
        return slug

    @staticmethod
    def _pattern_match(query: str, stored_pattern: str) -> bool:
        """
        Lightweight pattern matching:
        - Substring match
        - Token overlap (any shared word)
        """
        q_lower = query.lower()
        p_lower = stored_pattern.lower()
        if q_lower in p_lower or p_lower in q_lower:
            return True
        q_tokens = set(q_lower.split())
        p_tokens = set(p_lower.split())
        return bool(q_tokens & p_tokens)

    @staticmethod
    def _jaccard_similarity(str1: str, str2: str) -> float:
        """
        Compute Jaccard similarity between two strings.
        Tokenizes strings by whitespace and common delimiters.
        Returns 0.0–1.0 score (higher = more similar).
        """
        # Normalize and tokenize
        def tokenize(s: str) -> set[str]:
            s = s.lower().strip()
            # Replace common delimiters with space, then split
            for delim in ['_', '-', '/', ',', '.', '(', ')', '[', ']']:
                s = s.replace(delim, ' ')
            return set(s.split())

        set1 = tokenize(str1)
        set2 = tokenize(str2)

        if not set1 and not set2:
            return 1.0  # both empty = identical
        if not set1 or not set2:
            return 0.0  # one empty = dissimilar

        intersection = set1 & set2
        union = set1 | set2

        return len(intersection) / len(union)

    @staticmethod
    def _cosine_similarity_tokens(str1: str, str2: str) -> float:
        """
        Approximate cosine similarity using token frequency vectors.
        Pure Python implementation, no external libraries.
        """
        from math import sqrt

        def token_counts(s: str) -> dict[str, int]:
            s = s.lower().strip()
            for delim in ['_', '-', '/', ',', '.', '(', ')', '[', ']']:
                s = s.replace(delim, ' ')
            tokens = s.split()
            counts: dict[str, int] = {}
            for tok in tokens:
                counts[tok] = counts.get(tok, 0) + 1
            return counts

        c1 = token_counts(str1)
        c2 = token_counts(str2)

        if not c1 and not c2:
            return 1.0
        if not c1 or not c2:
            return 0.0

        # All unique tokens
        all_tokens = set(c1.keys()) | set(c2.keys())

        # Dot product and magnitudes
        dot = sum(c1.get(tok, 0) * c2.get(tok, 0) for tok in all_tokens)
        mag1 = sqrt(sum(v * v for v in c1.values()))
        mag2 = sqrt(sum(v * v for v in c2.values()))

        if mag1 == 0 or mag2 == 0:
            return 0.0

        return dot / (mag1 * mag2)

    @staticmethod
    def _validate_schema(skill_dict: dict[str, Any]) -> tuple[bool, str]:
        """
        Validate that a dict conforms to REQUIRED_SKILL_SCHEMA.
        Returns (is_valid, error_message).
        """
        for key, expected_type in REQUIRED_SKILL_SCHEMA.items():
            if key not in skill_dict:
                return False, f"Missing required field: {key}"
            if not isinstance(skill_dict[key], expected_type):
                return False, (
                    f"Field {key} has wrong type: "
                    f"expected {expected_type}, got {type(skill_dict[key])}"
                )
        return True, ""

    def export_all(self, dest_path: Path | str) -> None:
        """Export memory cache as pretty-printed JSON (for human review / git)."""
        dest = Path(dest_path)
        export_data = {
            "_meta": {
                "version": self.FLUXEDA_VERSION,
                "exported_at": time.time(),
                "skill_count": len(self._in_memory_cache),
            },
            "skills": [art.to_json() for art in self._in_memory_cache.values()],
        }
        try:
            with dest.open("w", encoding="utf-8") as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False)
            logger.info("Exported %d skills to %s", len(self._in_memory_cache), dest)
        except Exception as exc:
            logger.error("Export failed: %s", exc)
            raise

    def import_json(self, src_path: Path | str, merge: bool = True) -> int:
        """Import skills from JSON (array or object with 'skills' key)."""
        src = Path(src_path)
        if not src.exists():
            logger.error("Import source not found: %s", src)
            return 0

        imported = 0
        try:
            with src.open("r", encoding="utf-8") as f:
                data = json.load(f)

            skills_list: list[dict] = []
            if isinstance(data, list):
                skills_list = data
            elif isinstance(data, dict) and "skills" in data:
                skills_list = data["skills"]
            elif isinstance(data, dict):
                skills_list = [data]

            for entry in skills_list:
                # Strip metadata for import
                entry.pop("_metadata", None)
                valid, err = self._validate_schema(entry)
                if not valid:
                    logger.warning("Skipping invalid skill: %s", err)
                    continue
                self.distill_skill(
                    precondition=entry["precondition"],
                    action_template=entry["action_template"],
                    validation=entry["validation"],
                    rollback_condition=entry["rollback_condition"],
                    skill_name=entry["skill_name"],
                )
                imported += 1

            logger.info("Imported %d skills from %s", imported, src)
            return imported
        except json.JSONDecodeError as jde:
            logger.error("Import JSON parse error: %s", jde)
            return 0
        except Exception as exc:
            logger.error("Import failed: %s", exc)
            return 0

    def get_stats(self) -> dict[str, Any]:
        """Return memory statistics for debugging / monitoring."""
        total = len(self._in_memory_cache)
        if total == 0:
            return {"total_skills": 0}

        total_success = sum(a.success_count for a in self._in_memory_cache.values())
        total_failure = sum(a.failure_count for a in self._in_memory_cache.values())

        return {
            "total_skills": total,
            "total_success": total_success,
            "total_failure": total_failure,
            "success_rate": total_success / (total_success + total_failure) if (total_success + total_failure) > 0 else 0,
            "indexed_signatures": len(self._signature_index),
            "memory_root": str(self.root),
        }
