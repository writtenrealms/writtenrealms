from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


ABILITY_INTENT_STATUS_QUEUED = "queued"
ABILITY_INTENT_STATUS_CASTING = "casting"
ABILITY_INTENT_STATUS_CHANNELING = "channeling"

# Channels do not have an authoring/runtime pipeline yet, but reserving their
# committed status here gives casts and future channels one cancellation
# contract. Queued intents have not started and are deliberately excluded.
INTERRUPTIBLE_ABILITY_INTENT_STATUSES = frozenset(
    {
        ABILITY_INTENT_STATUS_CASTING,
        ABILITY_INTENT_STATUS_CHANNELING,
    }
)


@dataclass(frozen=True)
class InterruptibleAbilityIntent:
    slug: str
    status: str

    @property
    def phase(self) -> str:
        if self.status == ABILITY_INTENT_STATUS_CHANNELING:
            return "channel"
        return "cast"


def ability_intent_is_committed(pending: Any) -> bool:
    if not isinstance(pending, Mapping):
        return False
    status = str(pending.get("status") or "").strip().lower()
    return status in INTERRUPTIBLE_ABILITY_INTENT_STATUSES


def interruptible_ability_intent(
    pending: Any,
) -> InterruptibleAbilityIntent | None:
    if not ability_intent_is_committed(pending):
        return None
    return InterruptibleAbilityIntent(
        slug=str(pending.get("ability") or "").strip().lower(),
        status=str(pending.get("status") or "").strip().lower(),
    )
