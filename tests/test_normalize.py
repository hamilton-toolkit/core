"""Pin every hash-normalisation rule individually (build-plan 2.2)."""

import re
import unicodedata

from hamilton_core.check import digest, normalize


def test_collapses_internal_whitespace_runs():
    assert normalize("expired    token") == "expired token"


def test_tabs_and_newlines_count_as_whitespace():
    assert normalize("a\tb\nc\r\nd") == "a b c d"


def test_strips_surrounding_whitespace():
    assert normalize("   trimmed \t\n") == "trimmed"


def test_other_unicode_whitespace_is_collapsed():
    # U+00A0 NO-BREAK SPACE, U+2003 EM SPACE
    assert normalize("a  b") == "a b"


def test_unicode_nfc_equivalence():
    composed = "café"          # e-acute as one code point
    decomposed = "café"      # e + combining acute accent
    assert composed != decomposed
    assert normalize(composed) == normalize(decomposed)
    assert normalize(decomposed) == unicodedata.normalize("NFC", decomposed)


def test_case_is_not_folded():
    assert normalize("Expired Token") != normalize("expired token")


def test_trailing_punctuation_is_not_stripped():
    assert normalize("accepted") != normalize("accepted.")
    assert normalize("a -> b") != normalize("a -> b;")


def test_empty_and_whitespace_only_normalise_to_empty():
    assert normalize("") == ""
    assert normalize("   \t\n  ") == ""


def test_digest_is_six_hex_digits_of_sha256():
    assert re.fullmatch(r"[0-9a-f]{6}", digest("anything"))


def test_digest_is_taken_over_normalised_text():
    assert digest("  expired   token ") == digest("expired token")
    assert digest("expired token") != digest("expired token.")
