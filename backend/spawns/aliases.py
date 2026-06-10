from dataclasses import dataclass, field

from core.utils import split_cmd
from spawns.models import Alias, Player


ALIAS_EXPANSION_LIMIT = 10
RESERVED_ALIAS_MATCHES = {"alias", "unalias"}


@dataclass
class AliasExpansionResult:
    text: str
    expanded: bool = False
    error: str | None = None
    code: str | None = None
    chain: list[str] = field(default_factory=list)


def normalize_alias_match(match: str) -> str:
    return " ".join(str(match or "").strip().lower().split())


def validate_alias_match(match: str) -> str | None:
    normalized = normalize_alias_match(match)
    if not normalized:
        return "Alias name is required."
    if normalized in RESERVED_ALIAS_MATCHES:
        return f"{normalized} is reserved and cannot be used as an alias."
    if normalized.startswith("!"):
        return "Aliases cannot start with !."
    if any(char.isspace() for char in normalized) or ";" in normalized or "=" in normalized:
        return "Alias names must be a single token."
    return None


def serialize_player_aliases(player: Player) -> dict[str, dict]:
    aliases: dict[str, dict] = {}
    for alias in player.aliases.order_by("match", "id"):
        aliases[alias.match] = serialize_alias(alias)
    return aliases


def serialize_alias(alias: Alias) -> dict:
    return {
        "id": alias.id,
        "match": alias.match,
        "replacement": alias.replacement,
    }


def get_player_alias_map(player: Player) -> dict[str, Alias]:
    aliases: dict[str, Alias] = {}
    for alias in player.aliases.order_by("id"):
        match = normalize_alias_match(alias.match)
        if match:
            aliases[match] = alias
    return aliases


def upsert_player_alias(player: Player, match: str, replacement: str) -> Alias:
    normalized = normalize_alias_match(match)
    cleaned_replacement = str(replacement or "").strip()
    existing = list(
        Alias.objects.filter(player=player, match__iexact=normalized).order_by("id")
    )
    if existing:
        alias = existing[0]
        alias.match = normalized
        alias.replacement = cleaned_replacement
        alias.save(update_fields=["match", "replacement", "modified_ts"])
        duplicate_ids = [duplicate.id for duplicate in existing[1:]]
        if duplicate_ids:
            Alias.objects.filter(pk__in=duplicate_ids).delete()
        return alias
    return Alias.objects.create(
        player=player,
        match=normalized,
        replacement=cleaned_replacement,
    )


def delete_player_alias(player: Player, match: str) -> int:
    normalized = normalize_alias_match(match)
    deleted, _ = Alias.objects.filter(player=player, match__iexact=normalized).delete()
    return deleted


def expand_player_aliases(player: Player, text: str) -> AliasExpansionResult:
    aliases = get_player_alias_map(player)
    return _expand_text(str(text or "").strip(), aliases, stack=[])


def _expand_text(
    text: str,
    aliases: dict[str, Alias],
    *,
    stack: list[str],
) -> AliasExpansionResult:
    segments = [segment.strip() for segment in split_cmd(text) if segment.strip()]
    if not segments:
        return AliasExpansionResult(text="")

    expanded_segments: list[str] = []
    expanded_any = False
    for segment in segments:
        result = _expand_segment(segment, aliases, stack=stack)
        if result.error:
            return result
        expanded_segments.append(result.text)
        expanded_any = expanded_any or result.expanded

    return AliasExpansionResult(
        text=" ; ".join(expanded_segments),
        expanded=expanded_any,
    )


def _expand_segment(
    segment: str,
    aliases: dict[str, Alias],
    *,
    stack: list[str],
) -> AliasExpansionResult:
    first_token, suffix = _split_first_token(segment)
    if not first_token:
        return AliasExpansionResult(text=segment)

    match = normalize_alias_match(first_token)
    alias = aliases.get(match)
    if not alias:
        return AliasExpansionResult(text=segment)

    if match in stack:
        chain = [*stack, match]
        return AliasExpansionResult(
            text=segment,
            error=f"Alias loop: {' -> '.join(chain)}",
            code="alias_loop",
            chain=chain,
        )
    if len(stack) >= ALIAS_EXPANSION_LIMIT:
        chain = [*stack, match]
        return AliasExpansionResult(
            text=segment,
            error=f"Alias expansion is too deep: {' -> '.join(chain)}",
            code="alias_expansion_limit",
            chain=chain,
        )

    replacement = alias.replacement.strip()
    if suffix:
        replacement = f"{replacement} {suffix}" if replacement else suffix
    result = _expand_text(replacement, aliases, stack=[*stack, match])
    if result.error:
        return result
    result.expanded = True
    return result


def _split_first_token(text: str) -> tuple[str, str]:
    stripped = str(text or "").strip()
    if not stripped:
        return "", ""
    parts = stripped.split(maxsplit=1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1].strip()
