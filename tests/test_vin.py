"""Unit tests for VIN check-digit arithmetic.

The fleet roster is the only synthetic asset in the project, so these tests are what
back the claim that its VINs are structurally real rather than plausible-looking.
"""

import pytest

from fleetguard.vin import (
    TRANSLITERATION,
    InvalidVin,
    check_digit,
    is_valid_vin,
    looks_like_vin_prefix,
    with_check_digit,
)

# Real-world VINs whose published check digit must be reproduced exactly.
# 1M8GDM9A_KP042788 is the canonical ISO 3779 worked example; its check digit is 'X',
# which exercises the remainder-10 branch that a naive str(remainder) would get wrong.
KNOWN_GOOD = [
    ("1M8GDM9AXKP042788", "X"),
    ("1HGCM82633A004352", "3"),
    ("JH4TB2H26CC000000", "6"),
    ("5YJSA1DG9DFP14705", "9"),
    ("11111111111111111", "1"),
]


@pytest.mark.parametrize(("vin", "expected"), KNOWN_GOOD)
def test_check_digit_matches_published_value(vin, expected):
    assert check_digit(vin) == expected


@pytest.mark.parametrize(("vin", "_expected"), KNOWN_GOOD)
def test_known_good_vins_validate(vin, _expected):
    assert is_valid_vin(vin)


@pytest.mark.parametrize(("vin", "_expected"), KNOWN_GOOD)
def test_with_check_digit_is_idempotent_on_valid_vins(vin, _expected):
    assert with_check_digit(vin) == vin.upper()


def test_x_check_digit_is_produced_not_stringified_ten():
    """Remainder 10 must render as 'X'. str(10) would silently make an 18-char VIN."""
    out = with_check_digit("1M8GDM9A0KP042788")
    assert out[8] == "X"
    assert len(out) == 17


def test_corrupting_any_position_breaks_the_checksum():
    """Every position except 9 is covered — but only for changes that alter the *value*.

    Position 9 weighs 0, so the check digit cannot police itself.
    """
    vin = "1HGCM82633A004352"
    for i in range(17):
        if i == 8:
            continue
        original_value = TRANSLITERATION[vin[i]]
        # Pick a replacement with a genuinely different transliterated value — see
        # test_transliteration_is_many_to_one for why "any different character" is
        # not sufficient.
        replacement = next(c for c in "0123456789" if TRANSLITERATION[c] != original_value)
        mutated = vin[:i] + replacement + vin[i + 1 :]
        assert not is_valid_vin(mutated), f"position {i + 1} was not covered by the checksum"


def test_transliteration_is_many_to_one_so_some_substitutions_are_invisible():
    """A real property of VIN checksums, discovered by this suite.

    `A`, `J` and `1` all transliterate to 1, so swapping between them leaves the
    checksum untouched. The check digit validates the *numeric* VIN, not the literal
    characters — which means a valid checksum is not proof of an untampered VIN.
    """
    vin = "1HGCM82633A004352"
    assert TRANSLITERATION["A"] == TRANSLITERATION["1"] == TRANSLITERATION["J"] == 1

    swapped = vin.replace("A", "1")
    assert swapped != vin
    assert is_valid_vin(swapped), "equal-valued substitution should stay valid"
    assert check_digit(swapped) == check_digit(vin)

    collisions = {}
    for char, value in TRANSLITERATION.items():
        collisions.setdefault(value, []).append(char)
    assert any(len(chars) > 1 for chars in collisions.values())


def test_appending_a_serial_invalidates_the_original_check_digit():
    """This is precisely why the roster recomputes it.

    A real 11-character prefix plus a serial is *not* a valid VIN until position 9 is
    recalculated over all 17 characters.
    """
    prefix = "1FTEW1C40KF"
    naive = prefix + "000001"
    assert not is_valid_vin(naive)
    assert is_valid_vin(with_check_digit(naive))


def test_different_serials_yield_different_check_digits():
    """Guards against a check digit that ignores positions 12-17."""
    prefix = "1FTEW1C40KF"
    digits = {with_check_digit(prefix + f"{n:06d}")[8] for n in range(1, 40)}
    assert len(digits) > 1


class TestRejectsNonVins:
    """The complaint corpus is owner-entered; these are values actually observed in it."""

    @pytest.mark.parametrize(
        "value",
        [
            "!FTEW1EG2GK000001",  # invalid leading character, seen in FLAT_CMPL
            "11C6-RR6FG2000001",  # hyphen, seen in FLAT_CMPL
            "1FTEW1C40KI000001",  # I is not in the VIN alphabet
            "1FTEW1C40KO000001",  # O is not in the VIN alphabet
            "1FTEW1C40KQ000001",  # Q is not in the VIN alphabet
        ],
    )
    def test_invalid_alphabet_raises(self, value):
        with pytest.raises(InvalidVin):
            check_digit(value)

    @pytest.mark.parametrize("value", ["", "1FTEW1C40KF", "1FTEW1C40KF0000012"])
    def test_wrong_length_raises(self, value):
        with pytest.raises(InvalidVin):
            check_digit(value)

    @pytest.mark.parametrize("value", ["!FTEW1EG2GK", "11C6-RR6FG2", "1FTEW1C40K"])
    def test_prefix_filter_rejects_dirty_complaint_vins(self, value):
        assert not looks_like_vin_prefix(value)

    def test_prefix_filter_accepts_a_clean_prefix(self):
        assert looks_like_vin_prefix("1FTEW1C40KF")


def test_is_valid_vin_returns_false_rather_than_raising():
    """Callers screening a dirty corpus should not need try/except around every row."""
    assert is_valid_vin("!!!") is False
