"""
tests/unit/test_phone_canonicalization.py
-----------------------------------------
Test unitari per la funzione di canonicalizzazione telefonica single source of truth:
canonicalize_phone_syntax()
"""
from __future__ import annotations

import pytest

from normalization.phone import canonicalize_phone_syntax


@pytest.mark.unit
class TestPhoneCanonicalizationSyntax:

    def test_valid_international_number_with_spaces(self):
        assert canonicalize_phone_syntax("+39 000 0000001") == "+390000000001"

    def test_valid_international_number_with_dashes(self):
        assert canonicalize_phone_syntax("+1-555-123-4567") == "+15551234567"

    def test_valid_international_number_with_parentheses(self):
        assert canonicalize_phone_syntax("+1 (555) 1234567") == "+15551234567"

    def test_valid_already_clean_number(self):
        assert canonicalize_phone_syntax("+390000000001") == "+390000000001"

    def test_rejects_missing_plus_prefix(self):
        """Non inventa country code: numeri privi di '+' sono scartati."""
        assert canonicalize_phone_syntax("390000000001") is None
        assert canonicalize_phone_syntax("00390000000001") is None

    def test_rejects_letters_and_symbols(self):
        assert canonicalize_phone_syntax("+39 000 ABC0001") is None
        assert canonicalize_phone_syntax("+39#000000001") is None
        assert canonicalize_phone_syntax("+39/000000001") is None

    def test_rejects_too_short_numbers(self):
        """Meno di 5 cifre decimali -> non supportato/non telefonico."""
        assert canonicalize_phone_syntax("+1") is None
        assert canonicalize_phone_syntax("+39") is None
        assert canonicalize_phone_syntax("+1234") is None
        assert canonicalize_phone_syntax("+12345") == "+12345"

    def test_rejects_non_string_types(self):
        assert canonicalize_phone_syntax(None) is None
        assert canonicalize_phone_syntax(1234567890) is None
        assert canonicalize_phone_syntax(True) is None
        assert canonicalize_phone_syntax(["+390000000001"]) is None
        assert canonicalize_phone_syntax({"phone": "+390000000001"}) is None

    def test_rejects_empty_or_whitespace_strings(self):
        assert canonicalize_phone_syntax("") is None
        assert canonicalize_phone_syntax("   ") is None
        assert canonicalize_phone_syntax("+") is None
        assert canonicalize_phone_syntax("+   ") is None

    def test_rejects_multiple_plus_signs(self):
        """Deve esistere esattamente un '+' iniziale; nessun altro '+' ammesso."""
        assert canonicalize_phone_syntax("++390000000001") is None
        assert canonicalize_phone_syntax("+39+000000001") is None
        assert canonicalize_phone_syntax("+39 000+0000001") is None
        assert canonicalize_phone_syntax("+39abc123") is None
