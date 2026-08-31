"""VIN validation and check-digit arithmetic.

Extracted from the fleet-registry notebook so it is unit-testable. The roster is the
only synthetic asset in the project (§3), so "are these real VINs or plausible-looking
strings?" is the obvious challenge — the check digit is what makes the answer defensible.

ISO 3779: positions 1-3 WMI, 4-8 VDS, 9 check digit, 10 model year, 11 plant,
12-17 serial. `I`, `O` and `Q` are excluded from the alphabet so they cannot be
confused with 1 and 0.
"""

from __future__ import annotations

# Letter -> numeric value. Note I, O, Q are absent by design.
TRANSLITERATION: dict[str, int] = {
    **{str(d): d for d in range(10)},
    "A": 1,
    "B": 2,
    "C": 3,
    "D": 4,
    "E": 5,
    "F": 6,
    "G": 7,
    "H": 8,
    "J": 1,
    "K": 2,
    "L": 3,
    "M": 4,
    "N": 5,
    "P": 7,
    "R": 9,
    "S": 2,
    "T": 3,
    "U": 4,
    "V": 5,
    "W": 6,
    "X": 7,
    "Y": 8,
    "Z": 9,
}

# Positional weights. Position 9 weighs 0 — the check digit cannot influence itself.
WEIGHTS: tuple[int, ...] = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)

VIN_LENGTH = 17
CHECK_DIGIT_INDEX = 8  # 0-based position 9

VALID_CHARS = frozenset(TRANSLITERATION)


class InvalidVin(ValueError):
    """Raised when a string cannot be a VIN at all, as distinct from failing checksum."""


def _validate_shape(vin: str) -> str:
    if len(vin) != VIN_LENGTH:
        raise InvalidVin(f"VIN must be {VIN_LENGTH} characters, got {len(vin)}")
    upper = vin.upper()
    bad = sorted(set(upper) - VALID_CHARS)
    if bad:
        raise InvalidVin(f"VIN contains characters outside the VIN alphabet: {bad}")
    return upper


def check_digit(vin: str) -> str:
    """Return the correct check digit for `vin`, ignoring whatever sits at position 9.

    Returns a single character: '0'-'9' or 'X' (used when the remainder is 10).
    """
    upper = _validate_shape(vin)
    total = sum(TRANSLITERATION[c] * w for c, w in zip(upper, WEIGHTS, strict=True))
    remainder = total % 11
    return "X" if remainder == 10 else str(remainder)


def with_check_digit(vin: str) -> str:
    """Return `vin` with position 9 replaced by the correct check digit.

    This is what makes a generated VIN structurally valid. Appending a serial to a real
    11-character prefix invalidates the original check digit, so it must be recomputed —
    the whole VIN participates in the checksum, not just the prefix.
    """
    upper = _validate_shape(vin)
    return upper[:CHECK_DIGIT_INDEX] + check_digit(upper) + upper[CHECK_DIGIT_INDEX + 1 :]


def is_valid_vin(vin: str) -> bool:
    """True only if shape, alphabet and checksum are all correct."""
    try:
        upper = _validate_shape(vin)
    except InvalidVin:
        return False
    return upper[CHECK_DIGIT_INDEX] == check_digit(upper)


def looks_like_vin_prefix(value: str, length: int = 11) -> bool:
    """True if `value` could be an 11-character VIN prefix.

    Complaint VINs are `CHAR(11)` partials and are owner-entered, so the corpus contains
    values like `!FTEW1EG2GK` and `11C6-RR6FG2`. This is the filter that removes them
    before a prefix is used to build a fleet VIN.
    """
    return len(value) == length and set(value.upper()) <= VALID_CHARS
