"""Append-only, content-addressed archive for verified adversarial episodes."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from r3e.protocol.hashing import atomic_write_json, read_json
from r3e.protocol.ledger import append_ledger, read_ledger, writer_lock

from .schema import VerifiedEpisode


class EpisodeStoreViolation(RuntimeError):
    """Raised when an episode archive is incomplete or mutable."""


class EpisodeStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.objects = self.root / "objects"
        self.index_path = self.root / "episodes.jsonl"

    def _object_path(self, episode: VerifiedEpisode) -> Path:
        safe = episode.episode_hash.replace(":", "_")
        return self.objects / f"{safe}.json"

    def append_episode(self, episode: VerifiedEpisode | dict[str, Any]) -> str:
        value = (
            episode
            if isinstance(episode, VerifiedEpisode)
            else VerifiedEpisode.from_dict(episode)
        )
        lock = self.root / ".episode-store.lock"
        with writer_lock(lock):
            rows = read_ledger(self.index_path)
            existing_id = next(
                (row for row in rows if row.get("episode_id") == value.episode_id),
                None,
            )
            if existing_id:
                if existing_id.get("episode_hash") != value.episode_hash:
                    raise EpisodeStoreViolation("episode id is already bound to another hash")
                return value.episode_hash
            target = self._object_path(value)
            if target.exists():
                stored = VerifiedEpisode.from_dict(read_json(target))
                if stored.episode_id != value.episode_id:
                    raise EpisodeStoreViolation("episode object hash collision")
            else:
                atomic_write_json(target, value.to_dict())
            append_ledger(
                self.index_path,
                {
                    "operation": "append-verified-episode",
                    "episode_id": value.episode_id,
                    "episode_hash": value.episode_hash,
                    "round_id": value.round_id,
                    "challenged_policy_instance_hash": (
                        value.challenged_policy_instance_hash
                    ),
                    "challenged_effective_policy_hash": (
                        value.challenged_effective_policy_hash
                    ),
                    "object_path": str(target.relative_to(self.root)),
                },
            )
        return value.episode_hash

    def get(self, episode_id: str) -> VerifiedEpisode:
        rows = read_ledger(self.index_path)
        matches = [row for row in rows if row.get("episode_id") == episode_id]
        if len(matches) != 1:
            raise EpisodeStoreViolation(f"episode not found or ambiguous: {episode_id}")
        target = self.root / matches[0]["object_path"]
        episode = VerifiedEpisode.from_dict(read_json(target))
        if episode.episode_hash != matches[0]["episode_hash"]:
            raise EpisodeStoreViolation("episode index/object hash mismatch")
        return episode

    def audit(self) -> list[VerifiedEpisode]:
        rows = read_ledger(self.index_path)
        seen: set[str] = set()
        episodes = []
        for row in rows:
            episode_id = str(row.get("episode_id") or "")
            if episode_id in seen:
                raise EpisodeStoreViolation("duplicate episode id in append-only index")
            seen.add(episode_id)
            episodes.append(self.get(episode_id))
        return episodes
