"""
memory_integrity_guard.py — Skill Poisoning Defense (记忆护城河)
===============================================================
Phase 4 | R3E

Responsibilities:
  1. Vet every SkillArtifact before it is written to memory.
  2. Reject entries with:
       - Hollow validation (WNS not actually improved, just "no crash")
       - Cheating shortcuts (constraint relaxation to absurd values)
       - Overly broad / destructive action templates
  3. Log every veto with a clear RED-level audit trail.
  4. Support both inline use (within surgeon_loop_evo) and
     batch integration (via intercept on distill_skill).
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("R3E.guard")


# ─────────────────────────────────────────────────────────────────────────────
#  Vetting Result
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class VetResult:
    """Outcome of a SkillVetting check."""
    approved:          bool
    skill_name:        str
    reason:            str
    veto_code:         str           # empty="" if approved
    wns_delta:         float         # reported WNS improvement
    actual_delta:      float         # measured WNS delta (if available)
    backend_metric:    str           # "WNS >= 0.0" etc.
    detected_patterns: list[str]     # e.g. ["absurd_clock_period", "file_deletion"]
    timestamp:         float         = field(default_factory=time.time)

    @property
    def is_hollow_validation(self) -> bool:
        """True if WNS delta is near-zero (validation is a no-op)."""
        return abs(self.wns_delta) < 1e-4

    @property
    def is_absurd_constraint(self) -> bool:
        """True if any clock period in the action template is set absurdly loose."""
        return "absurd_clock_period" in self.detected_patterns

    def to_dict(self) -> dict:
        return {
            "approved":          self.approved,
            "skill_name":        self.skill_name,
            "reason":            self.reason,
            "veto_code":         self.veto_code,
            "wns_delta":          self.wns_delta,
            "actual_delta":       self.actual_delta,
            "backend_metric":     self.backend_metric,
            "detected_patterns":  self.detected_patterns,
            "timestamp":          self.timestamp,
        }


# ─────────────────────────────────────────────────────────────────────────────
#  Red-Flag Pattern Library
# ─────────────────────────────────────────────────────────────────────────────

# Destructive / obviously malicious action keywords
DESTRUCTIVE_KEYWORDS: list[str] = [
    r"\brm\b", r"\bdel\b", r"\berase\b", r"\btruncate\b",
    r"\bdrop\b", r"\bchmod\s+777\b", r"\bsudo\b",
    r"git\s+reset\s+--hard", r"rm\s+-rf\b",
]

# Absurd constraint values (clock period > 500ns, set_input_delay 999, etc.)
ABSURD_CONSTRAINT_PATTERNS: list[tuple[str, re.Pattern]] = [
    # create_clock -T <value> where value > 500 ns
    (
        "absurd_clock_period",
        re.compile(r"create_clock.*-T\s*(\d+(?:\.\d+)?)", re.IGNORECASE),
    ),
    # set_clock_latency > 100
    (
        "absurd_clock_latency",
        re.compile(r"set_clock_latency\s+-\S+\s+\S+\s+(\d+(?:\.\d+)?)", re.IGNORECASE),
    ),
    # set_input_delay 999 or 1000
    (
        "absurd_input_delay",
        re.compile(r"set_input_delay\s+\S+\s+(\d{3,}(?:\.\d+)?)", re.IGNORECASE),
    ),
    # set_max_delay with a huge value
    (
        "absurd_max_delay",
        re.compile(r"set_max_delay\s+\S+\s+(\d{3,}(?:\.\d+)?)", re.IGNORECASE),
    ),
    # set_disable_timing on all edges
    (
        " blanket_disable_timing",
        re.compile(r"set_disable_timing\s+\S+\s+- Pins\s+\*", re.IGNORECASE),
    ),
    # remove_clock (classic "cheat" to fix timing by removing the clock)
    (
        "remove_clock_cheat",
        re.compile(r"remove_clock\b", re.IGNORECASE),
    ),
]

# Hollow validation: WNS improvement target is 0 or ridiculously small
# Also catch: backend_metric = "No regression" (passes without checking WNS)
HOLLOW_VALIDATION_PATTERNS: list[re.Pattern] = [
    re.compile(r"wns_improvement_target\s*:\s*0(?:\.0*)?\b"),
    re.compile(r"wns_delta\s*:\s*0(?:\.0*)?\b"),
    re.compile(r"\bNo\s+regression\b", re.IGNORECASE),
    re.compile(r"\bno regression\b", re.IGNORECASE),
    re.compile(r"\bno_regression\b", re.IGNORECASE),
    re.compile(r"backend_metric['\"]?\s*:\s*['\"]no regression['\"]", re.IGNORECASE),
]

# Overly broad file-scope wildcards in action_template
BROAD_SCOPE_PATTERNS: list[re.Pattern] = [
    re.compile(r"allowed_edit_scope['\"]?\s*:\s*\[\s*['\"]\*['\"]\s*\]"),
    re.compile(r"allowed_edit_scope['\"]?\s*:\s*\[\s*\]"),   # empty = no restriction
    re.compile(r"full_pass['\"]?\s*:\s*True", re.IGNORECASE),
]


# ─────────────────────────────────────────────────────────────────────────────
#  SkillVetting Core
# ─────────────────────────────────────────────────────────────────────────────

class SkillVetting:
    """
    Intercepts SkillArtifacts before they are written to memory.

    Usage:
        guard = SkillVetting(audit_log=Path("guard_audit.jsonl"))
        result = guard.vet_skill(skill_artifact_or_dict)
        if result.approved:
            memory.distill_skill(...)
        else:
            logger.warning("[VETO] %s  reason=%s", result.skill_name, result.reason)
    """

    def __init__(
        self,
        audit_log: Path | str | None = ".micro_surgeon_memory/guard_audit.jsonl",
        allow_absurd_constraint: bool = False,   # set True ONLY in testing
        allow_destructive:      bool = False,   # set True ONLY in testing
        domain: str | None = None,
    ):
        self.audit_log = Path(audit_log) if audit_log else None
        self._allow_absurd = allow_absurd_constraint
        self._allow_destructive = allow_destructive
        self._domain = domain
        self._veto_count:   int = 0
        self._approve_count: int = 0

        if self.audit_log:
            self.audit_log.parent.mkdir(parents=True, exist_ok=True)

    # ── Public API ──────────────────────────────────────────────────────────

    def vet_skill(
        self,
        skill: dict[str, Any] | Any,
        *,
        measured_wns_delta: float | None = None,
    ) -> VetResult:
        """
        Vet a skill artifact (dict or SkillArtifact) before distillation.

        Checks applied (in order):
          1. Schema completeness (5 required FluxEDA fields)
          2. Destructive action detection
          3. Absurd constraint detection (clock periods, delays)
          4. Hollow validation detection
          5. Overly broad scope detection

        Parameters
        ----------
        skill : dict or SkillArtifact
            The skill to vet.
        measured_wns_delta : float, optional
            Override WNS delta from actual backend measurement.

        Returns
        -------
        VetResult with approved=True/False and full audit details.
        """
        # ── Normalize to dict ─────────────────────────────────────────────
        skill_dict = self._normalize(skill)
        if skill_dict is None:
            return VetResult(
                approved=False,
                skill_name="<unknown>",
                reason="Cannot parse skill — wrong type",
                veto_code="MALFORMED_INPUT",
                wns_delta=0.0,
                actual_delta=0.0,
                backend_metric="",
                detected_patterns=["malformed_input"],
            )

        skill_name   = skill_dict.get("skill_name", "<unnamed>")
        wns_delta    = measured_wns_delta if measured_wns_delta is not None else \
                       skill_dict.get("_measured_wns_delta", 0.0)
        backend_metric = self._get_backend_metric(skill_dict)
        detected: list[str] = []

        # ── Check 1: Schema completeness ──────────────────────────────────
        schema_ok, schema_msg = self._check_schema(skill_dict)
        if not schema_ok:
            return self._veto(
                skill_name=skill_name,
                reason=f"Schema violation: {schema_msg}",
                veto_code="SCHEMA_VIOLATION",
                wns_delta=wns_delta,
                backend_metric=backend_metric,
                detected=["schema_violation"],
            )

        # ── Check 2: Destructive actions ─────────────────────────────────
        if not self._allow_destructive:
            for pattern in DESTRUCTIVE_KEYWORDS:
                if self._search_destructively(skill_dict, pattern):
                    detected.append("destructive_action")
                    return self._veto(
                        skill_name=skill_name,
                        reason=f"DESTRUCTIVE ACTION DETECTED — Red-flag keyword '{pattern}' found in skill",
                        veto_code="DESTRUCTIVE_ACTION",
                        wns_delta=wns_delta,
                        backend_metric=backend_metric,
                        detected=detected,
                    )

        # ── Check 3: Absurd constraints (clock/constraint cheating) ─────
        if not self._allow_absurd:
            for flag_name, pattern in ABSURD_CONSTRAINT_PATTERNS:
                if self._search_constraints(skill_dict, pattern):
                    detected.append(flag_name)
                    return self._veto(
                        skill_name=skill_name,
                        reason=f"ABSURD CONSTRAINT DETECTED — {flag_name}: "
                               f"constraint value violates physics/production semantics",
                        veto_code=flag_name.upper(),
                        wns_delta=wns_delta,
                        backend_metric=backend_metric,
                        detected=detected,
                    )

        # ── Check 4: Hollow validation ────────────────────────────────────
        action_str = json.dumps(skill_dict.get("action_template", {}), ensure_ascii=False)
        val_str    = json.dumps(skill_dict.get("validation", {}),     ensure_ascii=False)
        combined   = action_str + val_str

        # Direct check: if backend_metric is exactly "No regression" (case-insensitive),
        # this is hollow — it passes trivially without checking actual WNS improvement
        bm_lower = backend_metric.lower().strip()
        if bm_lower in ("no regression", "no_regression", "no-error", "pass"):
            detected.append("hollow_validation")
            return self._veto(
                skill_name=skill_name,
                reason=f"HOLLOW VALIDATION DETECTED — backend_metric='{backend_metric}' "
                       "passes trivially without verifying actual WNS improvement",
                veto_code="HOLLOW_VALIDATION",
                wns_delta=wns_delta,
                backend_metric=backend_metric,
                detected=detected,
            )

        for hpat in HOLLOW_VALIDATION_PATTERNS:
            if hpat.search(combined):
                detected.append("hollow_validation")
                return self._veto(
                    skill_name=skill_name,
                    reason="HOLLOW VALIDATION DETECTED — validation metric is trivially "
                           "satisfied (wns_improvement_target=0 or 'no regression')",
                    veto_code="HOLLOW_VALIDATION",
                    wns_delta=wns_delta,
                    backend_metric=backend_metric,
                    detected=detected,
                )

        # ── Check 5: Overly broad scope — HARD VETO ─────────────────────────
        at = skill_dict.get("action_template", {})
        scope: list = at.get("allowed_edit_scope", [])

        # 条件 A: 显式通配符 ['*']
        # 条件 B: 空列表 [] (无任何限制)
        # 条件 C: 包含系统路径（如 /usr/bin, /etc, /tmp 等危险路径）
        veto_broad = False
        veto_reason = ""

        if scope == ["*"]:
            veto_broad = True
            veto_reason = "allowed_edit_scope=['*'] — no-file restriction"
        elif scope == []:
            veto_broad = True
            veto_reason = "allowed_edit_scope=[] — empty scope implies unbounded edits"
        else:
            # 检查 scope 条目中是否包含系统路径
            SYSTEM_PATH_PREFIXES = ["/usr/bin", "/usr/local/bin", "/etc", "/tmp",
                                    "/var", "/home", "/root", "/opt", "/sys", "/proc",
                                    "/dev", "/boot"]
            for entry in scope:
                if isinstance(entry, str):
                    ep = entry.strip().rstrip("/")
                    if any(ep.startswith(p) for p in SYSTEM_PATH_PREFIXES):
                        veto_broad = True
                        veto_reason = (
                            f"allowed_edit_scope contains system path '{entry}' "
                            f"— illegal modification target"
                        )
                        break
                    # 也拦截任何包含 '*' 的路径（除 ['*'] 外的情况）
                    if "*" in entry:
                        veto_broad = True
                        veto_reason = (
                            f"allowed_edit_scope contains wildcard path '{entry}' "
                            f"— unsafe recursive scope"
                        )
                        break

        # 也用正则检测字符串化的 scope
        if not veto_broad:
            for spat in BROAD_SCOPE_PATTERNS:
                if spat.search(action_str):
                    veto_broad = True
                    veto_reason = (
                        f"BROAD_SCOPE pattern matched: "
                        f"'{spat.pattern[:40]}...' — scope is unbounded"
                    )
                    break

        if veto_broad:
            detected.append("broad_scope_veto")
            return self._veto(
                skill_name=skill_name,
                reason=f"BROAD SCOPE VETO — {veto_reason}",
                veto_code="BROAD_SCOPE",
                wns_delta=wns_delta,
                backend_metric=backend_metric,
                detected=detected,
            )

        # ── Check 6: Constraint relaxation as sole repair strategy ──────
        if self._is_cheat_constraint_relaxation(skill_dict):
            detected.append("constraint_relaxation_cheat")
            return self._veto(
                skill_name=skill_name,
                reason="CHEAT DETECTED: repair_strategy='constraint_relaxation' or "
                       "equivalent — this fixes timing by hiding the problem, not solving it",
                veto_code="CHEAT_CONSTRAINT_RELAXATION",
                wns_delta=wns_delta,
                backend_metric=backend_metric,
                detected=detected,
            )

        # ── APPROVED ─────────────────────────────────────────────────────
        result = VetResult(
            approved=True,
            skill_name=skill_name,
            reason="All vetting checks passed",
            veto_code="",
            wns_delta=wns_delta,
            actual_delta=wns_delta,
            backend_metric=backend_metric,
            detected_patterns=detected,
        )
        self._approve_count += 1
        self._log(result)
        logger.info(
            "\u2705 [GUARD APPROVED]  skill=%-50s  wns_delta=%+.4f  patterns=%s",
            skill_name[:50], wns_delta, detected or ["clean"]
        )
        return result

    def vet_distill(
        self,
        memory_mgr,   # SkillMemoryManager
        **distill_kwargs,
    ) -> tuple[bool, VetResult, Any]:
        """
        Convenience wrapper: vet + distill in one call.

        Returns (distill_happened: bool, vet_result: VetResult, artifact_or_None).

        Usage:
            happened, result, art = guard.vet_distill(memory, skill_name="...", ...)
            if happened:
                print(f"Distilled: {art.skill_name}")
            else:
                print(f"VETOED: {result.veto_code} — {result.reason}")
        """
        # Build the raw skill dict from kwargs for vetting
        raw = {
            "skill_name":        distill_kwargs.get("skill_name", "<unnamed>"),
            "precondition":      distill_kwargs.get("precondition", {}),
            "action_template":   distill_kwargs.get("action_template", {}),
            "validation":        distill_kwargs.get("validation", {}),
            "rollback_condition": distill_kwargs.get("rollback_condition", {}),
        }

        vet = self.vet_skill(raw, measured_wns_delta=distill_kwargs.get("_measured_wns_delta"))

        if not vet.approved:
            self._veto_count += 1
            logger.warning(
                "\u274c [GUARD VETO] skill=%s  veto=%s  reason=%s",
                vet.skill_name, vet.veto_code, vet.reason
            )
            self._log(vet)
            return False, vet, None

        # Distill through the manager
        artifact = memory_mgr.distill_skill(**distill_kwargs)
        return True, vet, artifact

    def get_stats(self) -> dict[str, Any]:
        """Return veto/approval statistics."""
        total = self._approve_count + self._veto_count
        return {
            "total_vetted":         total,
            "approved":             self._approve_count,
            "vetoed":              self._veto_count,
            "veto_rate":           self._veto_count / total if total else 0.0,
            "audit_log":           str(self.audit_log) if self.audit_log else None,
        }

    # ── Private: Normalization ─────────────────────────────────────────────

    @staticmethod
    def _normalize(skill) -> Optional[dict[str, Any]]:
        """Coerce various skill representations to a plain dict."""
        if isinstance(skill, dict):
            return skill
        if hasattr(skill, "to_json"):
            return skill.to_json()
        if hasattr(skill, "__dict__"):
            return vars(skill)
        return None

    # ── Private: Schema Check ─────────────────────────────────────────────

    REQUIRED = ["skill_name", "precondition", "action_template", "validation", "rollback_condition"]

    def _check_schema(self, s: dict[str, Any]) -> tuple[bool, str]:
        for field in self.REQUIRED:
            if field not in s:
                return False, f"Missing required field: '{field}'"
            val = s[field]
            if field == "skill_name" and not isinstance(val, str):
                return False, f"skill_name must be str, got {type(val).__name__}"
            if field in ("precondition", "action_template", "validation", "rollback_condition"):
                if not isinstance(val, dict):
                    return False, f"'{field}' must be dict, got {type(val).__name__}"
        # ---- 跨域字段隔离 (v2 §3.1: 双向校验, 校验失败返回 False 走 veto) ----
        # 仅 domain in ("frontend","backend") 生效; None / "loopback" 跳过
        v = s.get("validation", {}) or {}
        a = s.get("action_template", {}) or {}
        _BACKEND_TIMING_IN_VALIDATION = {
            "backend_metric", "final_wns", "final_tns", "final_area", "setup_violations",
        }
        _BACKEND_TIMING_IN_ACTION = {"delta_wns", "delta_tns", "delta_area"}
        _FRONTEND_SEMANTIC_IN_VALIDATION = {
            "frontend_metric", "equiv_result", "equiv_config",
        }
        # TODO(backend action_template 跨域校验): Stage B 现有 action_template 全是
        #   跨域通用字段(repair_strategy/key_actions/source_model/allowed_edit_scope),
        #   无 RTL 语义专属字段名可列黑名单。待真实 backend skill 落库、确认其字段形态
        #   后再补对称禁止集。当前置空以免误杀合法 backend skill。
        _FRONTEND_SEMANTIC_IN_ACTION: set = set()
        if self._domain == "frontend":
            if "frontend_metric" not in v:
                return False, "frontend skill missing required frontend_metric in validation"
            bad_v = _BACKEND_TIMING_IN_VALIDATION & set(v.keys())
            bad_a = _BACKEND_TIMING_IN_ACTION & set(a.keys())
            if bad_v:
                return False, f"frontend skill must not contain backend/timing fields in validation: {sorted(bad_v)} (cross-domain pollution)"
            if bad_a:
                return False, f"frontend skill must not contain timing deltas in action_template: {sorted(bad_a)} (cross-domain pollution)"
        elif self._domain == "backend":
            if "backend_metric" not in v:
                return False, "backend skill missing required backend_metric in validation"
            bad_v = _FRONTEND_SEMANTIC_IN_VALIDATION & set(v.keys())
            bad_a = _FRONTEND_SEMANTIC_IN_ACTION & set(a.keys())  # 暂空, 见 TODO
            if bad_v:
                return False, f"backend skill must not contain frontend/semantic fields in validation: {sorted(bad_v)} (cross-domain pollution)"
            if bad_a:
                return False, f"backend skill must not contain RTL-semantic fields in action_template: {sorted(bad_a)} (cross-domain pollution)"
        # domain in (None, "loopback", 其它): 不做域校验
        return True, ""

    # ── Private: Detection Patterns ────────────────────────────────────────

    @staticmethod
    def _get_backend_metric(s: dict[str, Any]) -> str:
        val = s.get("validation", {})
        return val.get("backend_metric", "")

    @staticmethod
    def _search_destructively(s: dict[str, Any], pattern: str) -> bool:
        """Search all string fields for a destructive regex pattern."""
        haystack = json.dumps(s, ensure_ascii=False)
        try:
            return bool(re.search(pattern, haystack, re.IGNORECASE))
        except re.error:
            return pattern.lower() in haystack.lower()

    @staticmethod
    def _search_constraints(s: dict[str, Any], pattern: re.Pattern) -> bool:
        """
        Search for constraint patterns in action_template / allowed_edit_scope.
        Looks inside the raw skill dict for TCL/SDC constraint snippets.
        """
        # Check the raw action_template dict
        at = s.get("action_template", {})
        at_str = json.dumps(at, ensure_ascii=False)

        if pattern.search(at_str):
            return True

        # Also check allowed_edit_scope strings for loose timing values
        scope = at.get("allowed_edit_scope", [])
        for entry in scope:
            if isinstance(entry, str) and pattern.search(entry):
                return True

        return False

    @staticmethod
    def _is_cheat_constraint_relaxation(s: dict[str, Any]) -> bool:
        """
        Detect 'constraint_relaxation' or 'relax_clock_period' as a repair strategy.
        These are cheat strategies that hide the timing problem rather than fix it.
        """
        at = s.get("action_template", {})
        strat = at.get("repair_strategy", "").lower()
        bad_strategies = [
            "constraint_relaxation", "relax_constraint", "loosen_constraint",
            "relax_clock_period", "set_clock_period_higher",
            "increase_clock_period", "widen_timing",
        ]
        return strat in bad_strategies

    # ── Private: Logging ─────────────────────────────────────────────────

    def _veto(
        self,
        skill_name: str,
        reason: str,
        veto_code: str,
        wns_delta: float,
        backend_metric: str,
        detected: list[str],
    ) -> VetResult:
        self._veto_count += 1
        result = VetResult(
            approved=False,
            skill_name=skill_name,
            reason=reason,
            veto_code=veto_code,
            wns_delta=wns_delta,
            actual_delta=wns_delta,
            backend_metric=backend_metric,
            detected_patterns=detected,
        )
        self._log(result)
        # Log at WARNING level for rejected skills
        logger.warning(
            "\u274c [GUARD VETO]  skill=%-50s  veto=%-30s  wns=%+.4f  reason=%s",
            skill_name[:50], veto_code, wns_delta, reason
        )
        return result

    def _log(self, result: VetResult) -> None:
        if not self.audit_log:
            return
        try:
            with self.audit_log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.error("Failed to write guard audit log: %s", exc)

    # ── Offline audit reader ─────────────────────────────────────────────

    @classmethod
    def load_audit(cls, path: Path | str) -> list[VetResult]:
        """Load all VetResult entries from a guard_audit.jsonl file."""
        path = Path(path)
        if not path.exists():
            return []
        results = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    results.append(VetResult(**{k: v for k, v in d.items()
                                                if k in VetResult.__dataclass_fields__}))
                except Exception:
                    pass
        return results
