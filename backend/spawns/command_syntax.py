"""Small, query-free grammar helpers for player item commands.

Only text arguments pass through these helpers; structured selectors keep their
meaning. An explicit relation remains explicit even when its target is missing,
so an unfinished `get sword from` cannot turn into a pickup from the room.
"""


def selector_words(words: list[str]) -> str:
    if words and words[0].lower() in {"a", "an", "the"}:
        words = words[1:]
    return " ".join(words).strip()


def strip_leading_relation(
    words: list[str], relations: tuple[tuple[str, ...], ...],
) -> tuple[list[str], bool]:
    lowered = [word.lower() for word in words]
    for relation in relations:
        if lowered[:len(relation)] == list(relation):
            return words[len(relation):], True
    return words, False


def item_relation(
    words: list[str], relations: tuple[tuple[str, ...], ...],
) -> tuple[str, str, bool]:
    """Parse explicit relations, retaining the old `item container` shorthand."""
    lowered = [word.lower() for word in words]
    for index in range(len(words)):
        for relation in relations:
            if lowered[index:index + len(relation)] == list(relation):
                return (
                    selector_words(words[:index]),
                    selector_words(words[index + len(relation):]),
                    True,
                )
    if words and lowered[0] in {"a", "an", "the"}:
        words = words[1:]
    return selector_words(words[:1]), selector_words(words[1:]), False
