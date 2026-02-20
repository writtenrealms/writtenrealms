from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable


class MatchExpressionError(ValueError):
    pass


TOKEN_LITERAL = "literal"
TOKEN_OR = "or"
TOKEN_AND = "and"
TOKEN_NOT = "not"
TOKEN_LPAREN = "lparen"
TOKEN_RPAREN = "rparen"

_WORD_RE = re.compile(r"[a-z0-9']+")


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str


@dataclass(frozen=True)
class _LiteralNode:
    value: str


@dataclass(frozen=True)
class _UnaryNode:
    op: str
    child: "_Node"


@dataclass(frozen=True)
class _BinaryNode:
    op: str
    left: "_Node"
    right: "_Node"


_Node = _LiteralNode | _UnaryNode | _BinaryNode


def normalize_phrase_text(value: str | None) -> str:
    return " ".join(_WORD_RE.findall(str(value or "").lower()))


def normalize_exact_text(value: str | None) -> str:
    return " ".join(str(value or "").strip().lower().split())


def phrase_term_match(candidate: str | None, term: str | None) -> bool:
    normalized_candidate = normalize_phrase_text(candidate)
    normalized_term = normalize_phrase_text(term)
    if not normalized_candidate or not normalized_term:
        return False
    return f" {normalized_term} " in f" {normalized_candidate} "


def exact_term_match(candidate: str | None, term: str | None) -> bool:
    normalized_candidate = normalize_exact_text(candidate)
    normalized_term = normalize_exact_text(term)
    if not normalized_candidate or not normalized_term:
        return False
    return normalized_candidate == normalized_term


def _is_word_char(ch: str) -> bool:
    return bool(ch) and (ch.isalnum() or ch == "_")


def _matches_word_operator(text: str, index: int, operator: str) -> bool:
    end_index = index + len(operator)
    if text[index:end_index].lower() != operator:
        return False

    prev_char = text[index - 1] if index > 0 else ""
    next_char = text[end_index] if end_index < len(text) else ""
    return (not _is_word_char(prev_char)) and (not _is_word_char(next_char))


def _read_quoted_literal(text: str, start_index: int) -> tuple[str, int]:
    quote_char = text[start_index]
    index = start_index + 1
    chars: list[str] = []

    while index < len(text):
        ch = text[index]
        if ch == "\\":
            if index + 1 >= len(text):
                raise MatchExpressionError("Invalid escape sequence in quoted literal.")
            chars.append(text[index + 1])
            index += 2
            continue
        if ch == quote_char:
            return "".join(chars), index + 1
        chars.append(ch)
        index += 1

    raise MatchExpressionError("Unterminated quoted literal.")


def _tokenize(expression: str) -> list[_Token]:
    text = str(expression or "")
    tokens: list[_Token] = []
    index = 0

    while index < len(text):
        ch = text[index]

        if ch.isspace():
            index += 1
            continue

        if ch == "(":
            tokens.append(_Token(kind=TOKEN_LPAREN, value=ch))
            index += 1
            continue

        if ch == ")":
            tokens.append(_Token(kind=TOKEN_RPAREN, value=ch))
            index += 1
            continue

        if ch == "|":
            tokens.append(_Token(kind=TOKEN_OR, value=ch))
            index += 1
            continue

        if ch == "+":
            tokens.append(_Token(kind=TOKEN_AND, value=ch))
            index += 1
            continue

        if ch == "!":
            tokens.append(_Token(kind=TOKEN_NOT, value=ch))
            index += 1
            continue

        if _matches_word_operator(text, index, "or"):
            tokens.append(_Token(kind=TOKEN_OR, value=text[index:index + 2]))
            index += 2
            continue

        if _matches_word_operator(text, index, "and"):
            tokens.append(_Token(kind=TOKEN_AND, value=text[index:index + 3]))
            index += 3
            continue

        if _matches_word_operator(text, index, "not"):
            tokens.append(_Token(kind=TOKEN_NOT, value=text[index:index + 3]))
            index += 3
            continue

        if ch in ('"', "'"):
            quoted, index = _read_quoted_literal(text, index)
            literal = quoted.strip()
            if literal:
                tokens.append(_Token(kind=TOKEN_LITERAL, value=literal))
            continue

        literal_chars: list[str] = []
        while index < len(text):
            ch = text[index]
            if ch in "()|+!":
                break
            if ch in ('"', "'"):
                quoted, index = _read_quoted_literal(text, index)
                literal_chars.append(quoted)
                continue
            if (
                _matches_word_operator(text, index, "or")
                or _matches_word_operator(text, index, "and")
                or _matches_word_operator(text, index, "not")
            ):
                break
            literal_chars.append(ch)
            index += 1

        literal = "".join(literal_chars).strip()
        if literal:
            tokens.append(_Token(kind=TOKEN_LITERAL, value=literal))
            continue

        raise MatchExpressionError("Unexpected token in expression.")

    return tokens


class _Parser:
    def __init__(self, tokens: list[_Token]):
        self._tokens = tokens
        self._index = 0

    def parse(self) -> _Node:
        if not self._tokens:
            raise MatchExpressionError("Expression is empty.")

        node = self._parse_or()
        if self._peek() is not None:
            token = self._peek()
            raise MatchExpressionError(f"Unexpected token '{token.value}'.")
        return node

    def _parse_or(self) -> _Node:
        node = self._parse_and()
        while self._peek_kind(TOKEN_OR):
            self._consume(TOKEN_OR)
            rhs = self._parse_and()
            node = _BinaryNode(op=TOKEN_OR, left=node, right=rhs)
        return node

    def _parse_and(self) -> _Node:
        node = self._parse_unary()
        while self._peek_kind(TOKEN_AND):
            self._consume(TOKEN_AND)
            rhs = self._parse_unary()
            node = _BinaryNode(op=TOKEN_AND, left=node, right=rhs)
        return node

    def _parse_unary(self) -> _Node:
        if self._peek_kind(TOKEN_NOT):
            self._consume(TOKEN_NOT)
            return _UnaryNode(op=TOKEN_NOT, child=self._parse_unary())
        return self._parse_primary()

    def _parse_primary(self) -> _Node:
        token = self._peek()
        if token is None:
            raise MatchExpressionError("Expression ended unexpectedly.")

        if token.kind == TOKEN_LPAREN:
            self._consume(TOKEN_LPAREN)
            node = self._parse_or()
            if not self._peek_kind(TOKEN_RPAREN):
                raise MatchExpressionError("Missing closing ')'.")
            self._consume(TOKEN_RPAREN)
            return node

        if token.kind == TOKEN_LITERAL:
            return _LiteralNode(value=self._consume(TOKEN_LITERAL).value)

        raise MatchExpressionError(f"Expected a literal or '(', got '{token.value}'.")

    def _peek(self) -> _Token | None:
        if self._index >= len(self._tokens):
            return None
        return self._tokens[self._index]

    def _peek_kind(self, kind: str) -> bool:
        token = self._peek()
        return bool(token and token.kind == kind)

    def _consume(self, kind: str) -> _Token:
        token = self._peek()
        if token is None or token.kind != kind:
            raise MatchExpressionError("Unexpected expression structure.")
        self._index += 1
        return token


@lru_cache(maxsize=2048)
def _parse_cached(expression: str) -> _Node:
    tokens = _tokenize(expression)
    parser = _Parser(tokens)
    return parser.parse()


def validate_match_expression(expression: str | None) -> None:
    text = str(expression or "").strip()
    if not text:
        return
    _parse_cached(text)


def _evaluate(node: _Node, *, term_matcher: Callable[[str], bool]) -> bool:
    if isinstance(node, _LiteralNode):
        return bool(term_matcher(node.value))

    if isinstance(node, _UnaryNode):
        if node.op == TOKEN_NOT:
            return not _evaluate(node.child, term_matcher=term_matcher)
        raise MatchExpressionError(f"Unsupported unary operator '{node.op}'.")

    if node.op == TOKEN_AND:
        return _evaluate(node.left, term_matcher=term_matcher) and _evaluate(
            node.right,
            term_matcher=term_matcher,
        )
    if node.op == TOKEN_OR:
        return _evaluate(node.left, term_matcher=term_matcher) or _evaluate(
            node.right,
            term_matcher=term_matcher,
        )

    raise MatchExpressionError(f"Unsupported binary operator '{node.op}'.")


def evaluate_match_expression(
    expression: str | None,
    *,
    term_matcher: Callable[[str], bool],
    empty_expression: bool = False,
) -> bool:
    text = str(expression or "").strip()
    if not text:
        return empty_expression

    node = _parse_cached(text)
    return _evaluate(node, term_matcher=term_matcher)


def first_match_term(expression: str | None) -> str | None:
    text = str(expression or "")
    if not text.strip():
        return None

    tokens = _tokenize(text)
    for token in tokens:
        if token.kind == TOKEN_LITERAL and token.value.strip():
            return " ".join(token.value.strip().split())
    return None
