"""Immutable storage and policy-bound loading for ActiveMemoryBank objects."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import atomic_write_json, read_json
from r3e.protocol.ledger import append_ledger, read_ledger, writer_lock

from .memory_store import MemoryStore
from .schema import ActiveMemoryBank


class ActiveBankStoreViolation(RuntimeError):
    """Raised when a bank is not fully bound to a policy and memory library."""


class ActiveBankStore:
    def __init__(self, root: str | Path, *, memory_store: MemoryStore):
        self.root = Path(root)
        self.objects = self.root / "objects"
        self.index_path = self.root / "active_banks.jsonl"
        self.memory_store = memory_store

    def add_candidate(self, bank: ActiveMemoryBank | dict[str, Any]) -> str:
        value = (
            bank
            if isinstance(bank, ActiveMemoryBank)
            else ActiveMemoryBank.from_dict(bank)
        )
        for memory_id, binding in value.memories.items():
            memory = self.memory_store.get_version(
                memory_id, int(binding["memory_version"])
            )
            if memory.memory_hash != binding["memory_hash"]:
                raise ActiveBankStoreViolation("bank memory hash mismatch")
            if self.memory_store.current_status(
                memory_id, int(binding["memory_version"])
            ) != "active_dormant":
                raise ActiveBankStoreViolation("bank contains unauthorized memory")
        with writer_lock(self.root / ".active-bank-store.lock"):
            rows = read_ledger(self.index_path)
            existing = next(
                (
                    row for row in rows
                    if row.get("bank_id") == value.bank_id
                    and int(row.get("bank_version") or 0) == value.bank_version
                ),
                None,
            )
            if existing:
                if (
                    existing["bank_hash"] != value.bank_hash
                    or existing["effective_policy_hash"]
                    != value.effective_policy_hash
                ):
                    raise ActiveBankStoreViolation("bank version is already bound")
                return value.bank_hash
            conflicting_binding = next(
                (
                    row for row in rows
                    if row.get("bank_hash") == value.bank_hash
                    and row.get("effective_policy_hash")
                    != value.effective_policy_hash
                ),
                None,
            )
            if conflicting_binding:
                raise ActiveBankStoreViolation(
                    "bank identity is already bound to another effective policy"
                )
            target = self.objects / f"{value.bank_hash.replace(':', '_')}.json"
            if target.exists():
                stored = ActiveMemoryBank.from_dict(read_json(target))
                if stored.effective_policy_hash != value.effective_policy_hash:
                    raise ActiveBankStoreViolation(
                        "bank object has another effective policy binding"
                    )
            else:
                atomic_write_json(target, value.to_dict())
            append_ledger(
                self.index_path,
                {
                    "operation": "add-active-memory-bank-candidate",
                    "bank_id": value.bank_id,
                    "bank_version": value.bank_version,
                    "bank_hash": value.bank_hash,
                    "effective_policy_hash": value.effective_policy_hash,
                    "object_path": str(target.relative_to(self.root)),
                },
            )
        return value.bank_hash

    def get_by_hash(self, bank_hash: str) -> ActiveMemoryBank:
        matches = [
            row for row in read_ledger(self.index_path)
            if row.get("bank_hash") == bank_hash
        ]
        if len(matches) != 1:
            raise ActiveBankStoreViolation("active memory bank not found or ambiguous")
        bank = ActiveMemoryBank.from_dict(
            read_json(self.root / matches[0]["object_path"])
        )
        if bank.bank_hash != bank_hash:
            raise ActiveBankStoreViolation("bank index/object hash mismatch")
        return bank

    def load_for_policy(self, policy: PolicyState) -> ActiveMemoryBank:
        binding = policy.memory_binding or {}
        bank_hash = str(binding.get("active_memory_bank_hash") or "")
        if not bank_hash:
            raise ActiveBankStoreViolation("policy has no active memory bank binding")
        bank = self.get_by_hash(bank_hash)
        if bank.effective_policy_hash != policy.policy_hash:
            raise ActiveBankStoreViolation("bank effective policy hash mismatch")
        if bank.policy_binding != binding:
            raise ActiveBankStoreViolation("policy component hashes differ from bank")
        for memory_id, memory_binding in bank.memories.items():
            if self.memory_store.current_status(
                memory_id, int(memory_binding["memory_version"])
            ) != "active_dormant":
                raise ActiveBankStoreViolation("bank memory execution right was revoked")
        return bank

    def audit(self) -> int:
        rows = read_ledger(self.index_path)
        for row in rows:
            self.get_by_hash(row["bank_hash"])
        return len(rows)
