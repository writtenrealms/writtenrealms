from django.db import transaction

from spawns.models import Player


COMMAND_HISTORY_LIMIT = 20


def normalize_command_history_entry(text: str) -> str:
    return str(text or "").strip()


def get_player_command_history(player: Player) -> list[str]:
    history = player.command_history
    if not isinstance(history, list):
        return []

    entries: list[str] = []
    for entry in history:
        normalized = normalize_command_history_entry(entry)
        if normalized:
            entries.append(normalized)
        if len(entries) >= COMMAND_HISTORY_LIMIT:
            break
    return entries


def record_player_command_history(player_id: int, text: str) -> None:
    command = normalize_command_history_entry(text)
    if not command:
        return

    with transaction.atomic():
        player = Player.objects.select_for_update().get(pk=player_id)
        history = get_player_command_history(player)
        player.command_history = [command, *history][:COMMAND_HISTORY_LIMIT]
        player.save(update_fields=["command_history"])


def resolve_player_command_history(player: Player, index: int) -> str | None:
    if index < 1:
        return None
    history = get_player_command_history(player)
    if index > len(history):
        return None
    return history[index - 1]
