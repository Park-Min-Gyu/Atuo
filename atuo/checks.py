"""Deterministic checks that do not depend on any model.

These guard against the verifier model itself making things up: every issue
it raises has to be anchored to text that really exists.
"""

from __future__ import annotations

import re

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def squash(text: str) -> str:
    """Remove all whitespace so PDF line breaks do not defeat matching."""
    return re.sub(r"\s+", "", text)


def contains(haystack: str, needle: str) -> bool:
    needle = squash(needle)
    return bool(needle) and needle in squash(haystack)


def numbers(text: str) -> set[str]:
    """Numbers of two or more digits, with thousands separators removed."""
    found = set()
    for match in _NUMBER.findall(text):
        value = match.replace(",", "").rstrip(".")
        if len(value.replace(".", "")) >= 2:
            found.add(value)
    return found


def unsupported_numbers(source: str, summary: str) -> list[str]:
    """Numbers that appear in the summary but nowhere in the source."""
    source_digits = squash(source).replace(",", "")
    return sorted(n for n in numbers(summary) if n not in source_digits)
