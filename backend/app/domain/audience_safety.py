from __future__ import annotations


SENSITIVE_TARGETING_TERMS = frozenset({
    "religion", "religious", "muslim", "christian", "hindu", "jewish",
    "sexual orientation", "gay", "lesbian", "bisexual", "transgender",
    "political", "race", "ethnicity", "ethnic", "health condition",
    "diagnosis", "medical history", "clinical", "pregnant", "disability",
})


def contains_sensitive_targeting(*values: str) -> bool:
    normalized = " ".join(values).casefold()
    return any(term in normalized for term in SENSITIVE_TARGETING_TERMS)
