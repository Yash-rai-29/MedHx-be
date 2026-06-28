"""
PII masking for LLM calls.

Replaces structured PII (phone, Aadhaar, email, PAN, DOB) with stable [TAG_N]
placeholders before any text is sent to an external AI service (Gemini, ElevenLabs).

The original transcript is always stored in Firestore (patient-owned data);
only the masked copy travels to third-party APIs.
"""

import re
from dataclasses import dataclass, field


@dataclass
class PiiMaskResult:
    masked_text: str
    replacement_map: dict[str, str] = field(default_factory=dict)


# Ordered most-specific first to avoid partial-overlap collisions.
# Aadhaar (12-digit) must precede generic phone (10-digit) so we don't
# accidentally match the last 10 digits of an Aadhaar number as a phone.
_PATTERNS: list[tuple[str, str]] = [
    # Aadhaar: 12 digits, optionally space- or hyphen-separated in 4-4-4 groups
    (r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b", "AADHAAR"),
    # Indian mobile: optional +91/0 prefix, 10 digits starting with 6-9
    (r"(?<!\d)(?:\+91[\s\-]?|0)?[6-9]\d{9}(?!\d)", "PHONE"),
    # Email addresses
    (r"\b[\w.+\-]+@[\w\-]+\.[a-zA-Z]{2,7}\b", "EMAIL"),
    # PAN card: ABCDE1234F
    (r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", "PAN"),
    # Date of birth: DD/MM/YYYY or DD-MM-YYYY
    (
        r"\b(?:0?[1-9]|[12]\d|3[01])[/\-](?:0?[1-9]|1[0-2])[/\-](?:19|20)\d{2}\b",
        "DOB",
    ),
]


def mask_pii(text: str, extra_literals: list[str] | None = None) -> PiiMaskResult:
    """
    Replace structured PII with stable [TAG_N] placeholders.

    Args:
        text:           Input text (e.g. a consultation transcript).
        extra_literals: Additional literal strings to mask (e.g. patient full name
                        fetched from profile). Strings shorter than 3 chars are skipped.

    Returns:
        PiiMaskResult with .masked_text (safe to send to LLMs) and
        .replacement_map ({placeholder: original}) for audit or restoration.
    """
    replacement_map: dict[str, str] = {}
    counter = [0]  # mutable int inside a list so lambdas can close over it

    def _sub(m: re.Match, tag: str) -> str:
        key = f"[{tag}_{counter[0]}]"
        counter[0] += 1
        replacement_map[key] = m.group(0)
        return key

    for pattern, tag in _PATTERNS:
        text = re.sub(
            pattern,
            lambda m, t=tag: _sub(m, t),
            text,
            flags=re.IGNORECASE,
        )

    if extra_literals:
        for term in extra_literals:
            term = term.strip()
            if len(term) < 3:
                continue
            text = re.sub(
                re.escape(term),
                lambda m: _sub(m, "NAME"),
                text,
                flags=re.IGNORECASE,
            )

    return PiiMaskResult(masked_text=text, replacement_map=replacement_map)


def restore_pii(masked_text: str, replacement_map: dict[str, str]) -> str:
    """Substitute [TAG_N] placeholders back with their original values."""
    for placeholder, original in replacement_map.items():
        masked_text = masked_text.replace(placeholder, original)
    return masked_text
