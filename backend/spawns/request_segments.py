"""Stable bounded paths for idempotent subcommands within one request."""

from __future__ import annotations

import hashlib
import re


REQUEST_SEGMENT_ROOT = "r"
REQUEST_SEGMENT_MAX_LENGTH = 128

_PATH_RE = re.compile(r"^r(?:\.[0-9]+)*$")
_HASHED_PATH_RE = re.compile(r"^h\.[0-9a-f]{32}(?:\.[0-9]+)*$")


def normalize_request_segment(value) -> str:
    """Return a safe receipt path, accepting old numeric callers at the edge."""
    if value is None or value == "" or value == 0 or value == "0":
        return REQUEST_SEGMENT_ROOT

    text = str(value).strip().lower()
    if text.isdigit():
        text = f"{REQUEST_SEGMENT_ROOT}.{int(text)}"
    if len(text) <= REQUEST_SEGMENT_MAX_LENGTH and (
        _PATH_RE.fullmatch(text) or _HASHED_PATH_RE.fullmatch(text)
    ):
        return text
    return REQUEST_SEGMENT_ROOT


def append_request_segment(value, child_index: int) -> str:
    """Append a deterministic child index without exceeding the DB bound."""
    parent = normalize_request_segment(value)
    child = max(0, int(child_index))
    candidate = f"{parent}.{child}"
    if len(candidate) <= REQUEST_SEGMENT_MAX_LENGTH:
        return candidate

    digest = hashlib.blake2s(candidate.encode("utf-8"), digest_size=16).hexdigest()
    return f"h.{digest}"
