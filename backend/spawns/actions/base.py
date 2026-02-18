from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from spawns.events import GameEvent


@dataclass
class ActionResult:
    events: list[GameEvent] = field(default_factory=list)
    data: dict = field(default_factory=dict)


class ActionError(Exception):
    """Error raised from an Action to be surfaced as a command error."""

    def __init__(self, message: str, code: str = "error", data: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.data = data or {}
