"""A minimal event stream used by the UI and CLI traces."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class EventStream:
    events: list[dict[str, Any]] = field(default_factory=list)

    def emit(self, stage: str, status: str, **payload: Any) -> dict[str, Any]:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stage": stage,
            "status": status,
            **payload,
        }
        self.events.append(event)
        return event
