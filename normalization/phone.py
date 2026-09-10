"""
normalization/phone.py
----------------------
Single source of truth per la canonicalizzazione sintattica dei numeri di telefono.

Principi:
- Deterministica, pura e senza effetti collaterali.
- Rimuove esclusivamente separatori consentiti (+, cifre, spazi, trattini, parentesi).
- Non inventa country code (richiede prefisso '+' esplicito).
- Non dichiara validità o conformità E.164 (canonicalizzazione puramente sintattica).
- Restituisce None per valori non telefonici, non supportati o privi di prefisso.
"""
from __future__ import annotations

from typing import Any

_ALLOWED_CHARS = frozenset("+0123456789 -()")
_MIN_DIGITS = 5


def canonicalize_phone_syntax(value: Any) -> str | None:
    """
    Normalizza deterministicamente la sintassi di una stringa telefonica internazionale.

    Regole:
    1. L'input deve essere una stringa (o convertibile a stringa) non vuota.
    2. Deve iniziare con il carattere '+'.
    3. Può contenere esclusivamente cifre decimali e separatori consentiti: ' ', '-', '(', ')'.
    4. Deve contenere almeno 5 cifre decimali dopo la rimozione dei separatori.
    5. Restituisce '+' seguito dalla sola sequenza di cifre.
    6. Se una qualsiasi condizione non è soddisfatta, restituisce None.
    """
    if value is None:
        return None

    if not isinstance(value, str):
        # Rifiuta tipi non testuali (interi, booleani, float, dict, ecc.)
        return None

    s = value.strip()
    if not s or not s.startswith("+") or s.count("+") != 1:
        return None

    # Verifica che tutti i caratteri appartengano all'alfabeto consentito
    if not set(s).issubset(_ALLOWED_CHARS):
        return None

    # Estrai esclusivamente le cifre decimali
    digits = "".join(c for c in s if c.isdigit())
    if len(digits) < _MIN_DIGITS:
        return None

    return "+" + digits
