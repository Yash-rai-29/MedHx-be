"""
Phone Number Utilities
======================
Minimal, modular utility for normalizing (country_code, phone_number).
"""

import re


def normalize_phone(country_code: str | None = None, phone_number: str | None = None) -> tuple[str | None, str | None]:
    """
    Normalizes phone input into a clean 2-tuple: (country_code, phone_number).
    - Ensures country_code starts with '+' and phone_number contains only digits.
    - If only phone_number is provided, parses international '+' prefix or defaults country_code to '+91'.
    """
    if not country_code and not phone_number:
        return None, None

    cc = str(country_code).strip() if country_code else ""
    pn = str(phone_number).strip() if phone_number else ""

    # Both country code and phone number provided separately
    if cc and pn and not pn.startswith("+"):
        return f"+{cc.lstrip('+')}", re.sub(r"\D", "", pn)

    # Combined single string passed in phone_number or country_code
    raw = pn or cc
    if raw.startswith("+"):
        m = re.match(r"^(\+91|\+1|\+44|\+971|\+966|\+65|\+61|\+81|\+49|\+33|\+\d{1,3})(\d+)$", raw)
        if m:
            return m.group(1), m.group(2)

    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None, None

    if len(digits) == 12 and digits.startswith("91"):
        return "+91", digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        return "+91", digits[1:]

    return "+91", digits

