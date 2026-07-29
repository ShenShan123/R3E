"""Policy-authorized portfolio templates for ACP-5 RAAM control.

ControlMemory may name one frozen template and repeat its bounded control
values.  It cannot define lenses, prompts, model routes, selectors, providers,
or additional candidate budget.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Mapping

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_file, hash_payload


PORTFOLIO_TEMPLATE_REGISTRY_SCHEMA = (
    "r3e-portfolio-template-registry-v1"
)
PORTFOLIO_TEMPLATE_SCHEMA = "r3e-portfolio-template-v1"
PORTFOLIO_CONTROL_RECEIPT_SCHEMA = "r3e-portfolio-control-receipt-v1"
PORTFOLIO_CONTROL_FIELDS = {
    "candidate_portfolio_template_id",
    "specialist_slot_budget",
    "diversity_retry_budget",
    "portfolio_early_stop",
}
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class PortfolioControlViolation(RuntimeError):
    """Raised when memory attempts to exceed frozen portfolio authority."""


def _digest(value: Any, field: str) -> str:
    text = str(value or "")
    if not _HASH_RE.fullmatch(text):
        raise PortfolioControlViolation(f"{field} must be a sha256 digest")
    return text


def _identifier(value: Any, field: str) -> str:
    text = str(value or "")
    if not _ID_RE.fullmatch(text):
        raise PortfolioControlViolation(f"{field} is not a stable identifier")
    return text


def _non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise PortfolioControlViolation(f"{field} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise PortfolioControlViolation(f"{field} must be an integer") from exc
    if result < 0:
        raise PortfolioControlViolation(f"{field} must be non-negative")
    return result


@dataclass(frozen=True)
class PortfolioTemplate:
    template_id: str
    slots: tuple[str, ...]
    specialist_slot_budget: int
    diversity_retry_budget: int
    portfolio_early_stop: str

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PortfolioTemplate":
        payload = deepcopy(dict(raw))
        if set(payload) != {
            "schema_version",
            "template_id",
            "slots",
            "specialist_slot_budget",
            "diversity_retry_budget",
            "portfolio_early_stop",
            "template_hash",
        }:
            raise PortfolioControlViolation("portfolio template fields mismatch")
        if payload["schema_version"] != PORTFOLIO_TEMPLATE_SCHEMA:
            raise PortfolioControlViolation("portfolio template schema mismatch")
        slots = payload["slots"]
        if (
            not isinstance(slots, list)
            or not slots
            or any(not _ID_RE.fullmatch(str(slot)) for slot in slots)
        ):
            raise PortfolioControlViolation("portfolio template slots are invalid")
        value = cls(
            template_id=_identifier(payload["template_id"], "template_id"),
            slots=tuple(str(slot) for slot in slots),
            specialist_slot_budget=_non_negative_int(
                payload["specialist_slot_budget"], "specialist_slot_budget"
            ),
            diversity_retry_budget=_non_negative_int(
                payload["diversity_retry_budget"], "diversity_retry_budget"
            ),
            portfolio_early_stop=str(payload["portfolio_early_stop"]),
        )
        if value.portfolio_early_stop != "all_candidates":
            raise PortfolioControlViolation(
                "ACP-5 V1 permits only all_candidates early stop"
            )
        if value.diversity_retry_budget != 0:
            raise PortfolioControlViolation(
                "ACP-5 V1 forbids diversity retry provider calls"
            )
        if payload["template_hash"] != value.template_hash:
            raise PortfolioControlViolation("portfolio template hash mismatch")
        return value

    @property
    def template_hash(self) -> str:
        return hash_payload(self._body())

    def _body(self) -> dict[str, Any]:
        return {
            "schema_version": PORTFOLIO_TEMPLATE_SCHEMA,
            "template_id": self.template_id,
            "slots": list(self.slots),
            "specialist_slot_budget": self.specialist_slot_budget,
            "diversity_retry_budget": self.diversity_retry_budget,
            "portfolio_early_stop": self.portfolio_early_stop,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._body()
        payload["template_hash"] = self.template_hash
        return payload


@dataclass(frozen=True)
class PortfolioTemplateRegistry:
    registry_id: str
    candidate_budget: int
    effective_portfolio_hash: str
    lens_registry_hash: str
    allocator_hash: str
    general_lens_id: str
    lens_ids: tuple[str, ...]
    templates: dict[str, PortfolioTemplate]
    source_asset_path: str = ""
    source_asset_hash: str = ""

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, Any],
        *,
        source_asset_path: str = "",
        source_asset_hash: str = "",
    ) -> "PortfolioTemplateRegistry":
        payload = deepcopy(dict(raw))
        if set(payload) != {
            "schema_version",
            "registry_id",
            "candidate_budget",
            "effective_portfolio_hash",
            "lens_registry_hash",
            "allocator_hash",
            "general_lens_id",
            "lens_ids",
            "templates",
            "registry_hash",
        }:
            raise PortfolioControlViolation(
                "portfolio template registry fields mismatch"
            )
        if payload["schema_version"] != PORTFOLIO_TEMPLATE_REGISTRY_SCHEMA:
            raise PortfolioControlViolation(
                "portfolio template registry schema mismatch"
            )
        budget = _non_negative_int(
            payload["candidate_budget"], "candidate_budget"
        )
        if budget < 1:
            raise PortfolioControlViolation("candidate budget must be positive")
        lens_ids = payload["lens_ids"]
        if (
            not isinstance(lens_ids, list)
            or not lens_ids
            or len(set(lens_ids)) != len(lens_ids)
            or any(not _ID_RE.fullmatch(str(value)) for value in lens_ids)
        ):
            raise PortfolioControlViolation("registry lens_ids are invalid")
        raw_templates = payload["templates"]
        if not isinstance(raw_templates, list) or not raw_templates:
            raise PortfolioControlViolation("template registry must not be empty")
        templates: dict[str, PortfolioTemplate] = {}
        general = _identifier(payload["general_lens_id"], "general_lens_id")
        for raw_template in raw_templates:
            if not isinstance(raw_template, Mapping):
                raise PortfolioControlViolation("template must be an object")
            template = PortfolioTemplate.from_dict(raw_template)
            if template.template_id in templates:
                raise PortfolioControlViolation("duplicate portfolio template")
            if len(template.slots) != budget:
                raise PortfolioControlViolation(
                    "template slot count differs from candidate budget"
                )
            if not set(template.slots) <= set(str(value) for value in lens_ids):
                raise PortfolioControlViolation("template uses unknown lens")
            specialist_count = sum(slot != general for slot in template.slots)
            if general not in template.slots:
                raise PortfolioControlViolation(
                    "portfolio template requires a general slot"
                )
            if specialist_count != template.specialist_slot_budget:
                raise PortfolioControlViolation(
                    "template specialist budget does not match slots"
                )
            templates[template.template_id] = template
        value = cls(
            registry_id=_identifier(payload["registry_id"], "registry_id"),
            candidate_budget=budget,
            effective_portfolio_hash=_digest(
                payload["effective_portfolio_hash"],
                "effective_portfolio_hash",
            ),
            lens_registry_hash=_digest(
                payload["lens_registry_hash"], "lens_registry_hash"
            ),
            allocator_hash=_digest(payload["allocator_hash"], "allocator_hash"),
            general_lens_id=general,
            lens_ids=tuple(str(value) for value in lens_ids),
            templates=templates,
            source_asset_path=str(source_asset_path),
            source_asset_hash=str(source_asset_hash),
        )
        if payload["registry_hash"] != value.registry_hash:
            raise PortfolioControlViolation(
                "portfolio template registry hash mismatch"
            )
        return value

    @property
    def registry_hash(self) -> str:
        return hash_payload(self._body())

    def _body(self) -> dict[str, Any]:
        return {
            "schema_version": PORTFOLIO_TEMPLATE_REGISTRY_SCHEMA,
            "registry_id": self.registry_id,
            "candidate_budget": self.candidate_budget,
            "effective_portfolio_hash": self.effective_portfolio_hash,
            "lens_registry_hash": self.lens_registry_hash,
            "allocator_hash": self.allocator_hash,
            "general_lens_id": self.general_lens_id,
            "lens_ids": list(self.lens_ids),
            "templates": [
                self.templates[key].to_dict() for key in sorted(self.templates)
            ],
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._body()
        payload["registry_hash"] = self.registry_hash
        return payload

    def authorize_policy(self, policy: PolicyState) -> None:
        binding = policy.candidate_portfolio_binding or {}
        if (
            policy.schema_version != "r3e-policy-v3"
            or binding.get("effective_portfolio_hash")
            != self.effective_portfolio_hash
            or binding.get("lens_registry_hash") != self.lens_registry_hash
            or binding.get("allocator_hash") != self.allocator_hash
        ):
            raise PortfolioControlViolation(
                "template registry is not authorized by active Policy V3"
            )
        if int(policy.budgets["max_llm_calls_per_case"]) < self.candidate_budget:
            raise PortfolioControlViolation(
                "template registry exceeds policy candidate budget"
            )
        if not self.source_asset_path or not self.source_asset_hash:
            raise PortfolioControlViolation(
                "template registry lacks checked-in asset authority"
            )
        if (
            (policy.frozen_assets or {}).get(self.source_asset_path)
            != self.source_asset_hash
        ):
            raise PortfolioControlViolation(
                "policy does not freeze exact template registry asset"
            )

    def materialize(
        self,
        delta: Mapping[str, Any],
        *,
        policy: PolicyState,
    ) -> dict[str, Any]:
        self.authorize_policy(policy)
        payload = deepcopy(dict(delta))
        if not PORTFOLIO_CONTROL_FIELDS <= set(payload):
            raise PortfolioControlViolation(
                "portfolio control must bind all bounded control fields"
            )
        if set(payload) & PORTFOLIO_CONTROL_FIELDS != set(payload):
            raise PortfolioControlViolation(
                "portfolio materializer accepts only portfolio control fields"
            )
        template_id = _identifier(
            payload["candidate_portfolio_template_id"],
            "candidate_portfolio_template_id",
        )
        template = self.templates.get(template_id)
        if template is None:
            raise PortfolioControlViolation(
                "memory selected a template outside the frozen registry"
            )
        expected = {
            "candidate_portfolio_template_id": template.template_id,
            "specialist_slot_budget": template.specialist_slot_budget,
            "diversity_retry_budget": template.diversity_retry_budget,
            "portfolio_early_stop": template.portfolio_early_stop,
        }
        if payload != expected:
            raise PortfolioControlViolation(
                "memory control differs from frozen template constraints"
            )
        return {
            **expected,
            "portfolio_template_registry_hash": self.registry_hash,
            "portfolio_template_hash": template.template_hash,
            "portfolio_slots": list(template.slots),
            "candidate_budget": self.candidate_budget,
            "candidate_portfolio_hash": self.effective_portfolio_hash,
            "portfolio_allocation_mode": "raam_template",
        }

    def build_receipt(
        self,
        controls: Mapping[str, Any],
        *,
        policy: PolicyState,
        execution_plan_hash: str,
    ) -> dict[str, Any]:
        self.authorize_policy(policy)
        template_id = str(
            controls.get("candidate_portfolio_template_id") or ""
        )
        template = self.templates.get(template_id)
        if template is None:
            raise PortfolioControlViolation("execution plan template is unknown")
        expected = self.materialize(
            {
                field: controls.get(field)
                for field in sorted(PORTFOLIO_CONTROL_FIELDS)
            },
            policy=policy,
        )
        for key, value in expected.items():
            if controls.get(key) != value:
                raise PortfolioControlViolation(
                    "execution plan portfolio controls are not reconstructable"
                )
        receipt = {
            "schema_version": PORTFOLIO_CONTROL_RECEIPT_SCHEMA,
            "effective_policy_hash": policy.effective_policy_hash,
            "execution_plan_hash": _digest(
                execution_plan_hash, "execution_plan_hash"
            ),
            "portfolio_template_registry_hash": self.registry_hash,
            "portfolio_template_id": template.template_id,
            "portfolio_template_hash": template.template_hash,
            "allocated_lens_ids": list(template.slots),
            "candidate_budget": self.candidate_budget,
            "specialist_slot_budget": template.specialist_slot_budget,
            "diversity_retry_budget": template.diversity_retry_budget,
            "portfolio_early_stop": template.portfolio_early_stop,
        }
        receipt["receipt_hash"] = hash_payload(receipt)
        return receipt


def load_portfolio_template_registry(
    path: str | Path,
    *,
    project_root: str | Path,
) -> PortfolioTemplateRegistry:
    root = Path(project_root).resolve()
    source = Path(path).resolve()
    try:
        relative = source.relative_to(root).as_posix()
    except ValueError as exc:
        raise PortfolioControlViolation(
            "template registry asset escapes project root"
        ) from exc
    return PortfolioTemplateRegistry.from_dict(
        json.loads(source.read_text(encoding="utf-8")),
        source_asset_path=relative,
        source_asset_hash=hash_file(source),
    )
