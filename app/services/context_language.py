from __future__ import annotations

from typing import Literal


ContextLanguageBucket = Literal[
    "zh_hans",
    "en",
    "mixed",
    "other",
    "unknown",
]


def classify_context_language(text: str | None) -> ContextLanguageBucket:
    """Return a bounded, non-persisting language bucket for one prompt."""

    try:
        if not isinstance(text, str) or not text.strip():
            return "unknown"
        has_han = any("\u3400" <= character <= "\u9fff" for character in text)
        has_latin = any(
            "a" <= character.casefold() <= "z" for character in text
        )
        if has_han and has_latin:
            return "mixed"
        if has_han:
            return "zh_hans"
        if has_latin:
            return "en"
        if any(character.isalpha() or character.isdigit() for character in text):
            return "other"
        return "unknown"
    except Exception:
        return "unknown"
