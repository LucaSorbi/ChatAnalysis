"""
test_architecture_guard.py
--------------------------
Test architetturali e controlli statici di regressione (FASE 40, CRITERI DI ACCETTAZIONE).

Garantisce che:
1. ui/presentation.py NON istanzi TopicDetectionAnalyzer.
2. ui/presentation.py NON istanzi TopicDiscoveryAnalyzer.
3. ui/presentation.py NON istanzi direttamente LmStudioClient.
4. Nessun modulo in ui/ contenga stringhe obsolete ("differito alla fase sperimentale finale", "Hardware Block", "Rinvio Benchmark", "AMD A8").
5. Nessuna logica di business di consistency risieda nella view (presentation.py), ma sia delegata a ConsistencyService / analysis.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

UI_DIR = Path(__file__).resolve().parent.parent.parent / "ui"
PRESENTATION_PY = UI_DIR / "presentation.py"


def test_presentation_does_not_instantiate_analyzers_or_clients():
    """Verifica che ui/presentation.py non istanzi direttamente analyzer o client LM Studio."""
    assert PRESENTATION_PY.exists(), f"{PRESENTATION_PY} non trovato."
    source = PRESENTATION_PY.read_text(encoding="utf-8")
    tree = ast.parse(source, filename="presentation.py")

    disallowed_calls = {
        "TopicDetectionAnalyzer",
        "TopicDiscoveryAnalyzer",
        "LmStudioClient",
    }

    found_calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name in disallowed_calls:
                found_calls.append(name)

    assert not found_calls, (
        f"ui/presentation.py viola la separazione architetturale istanziando direttamente: {found_calls}"
    )


def test_no_obsolete_strings_in_ui_modules():
    """Verifica che nessun file Python in ui/ contenga banner o stringhe obsolete."""
    obsolete_patterns = [
        "differito alla fase sperimentale finale",
        "Hardware Block",
        "Rinvio Benchmark",
        "AMD A8",
    ]

    violations: list[tuple[str, str]] = []
    for py_file in UI_DIR.glob("**/*.py"):
        text = py_file.read_text(encoding="utf-8")
        for pattern in obsolete_patterns:
            if pattern.lower() in text.lower():
                violations.append((py_file.name, pattern))

    assert not violations, f"Rilevate stringhe obsolete nei moduli UI: {violations}"


def test_presentation_does_not_implement_consistency_business_logic():
    """Verifica che presentation.py deleghi la consistency al service senza hardcodare decisioni logiche."""
    source = PRESENTATION_PY.read_text(encoding="utf-8")
    # presentation non deve fare controlli grezzi su overlap per decidere se è CONSISTENT
    assert "overlap_evidence_ids" not in source or "execute_discovery_verification" in source
