"""Configurazione condivisa della suite (`python -m pytest`).

I moduli che usano `streamlit.testing` (AppTest) richiedono l'extra opzionale ``ui``
(``pip install -e ".[ui,dev]"``). Se Streamlit non è installato, questi moduli vengono
saltati con un avviso invece di far fallire la raccolta dell'intera suite.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

# Import a livello di modulo (colonna 0) di streamlit, oppure di un altro modulo di test che a sua volta lo richiede.
_STREAMLIT_IMPORT = re.compile(r"^(?:from|import)\s+streamlit\b", re.MULTILINE)
_TEST_IMPORT = re.compile(r"^from\s+tests\.(?:unit|integration)\.(test_\w+)\s+import", re.MULTILINE)
_SKIPPED_FOR_STREAMLIT: list[str] = []


def _needs_streamlit(path: Path, seen: frozenset[str] = frozenset()) -> bool:
    if path.name in seen:
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    if _STREAMLIT_IMPORT.search(text):
        return True
    for module in _TEST_IMPORT.findall(text):
        for sibling in path.parent.parent.glob(f"*/{module}.py"):
            if _needs_streamlit(sibling, seen | {path.name}):
                return True
    return False


def _streamlit_testing_available() -> bool:
    try:
        return importlib.util.find_spec("streamlit.testing") is not None
    except (ImportError, ValueError):
        return False


def pytest_ignore_collect(collection_path: Path, config):  # noqa: ANN001
    if collection_path.suffix != ".py" or not collection_path.name.startswith("test_"):
        return None
    if _streamlit_testing_available():
        return None
    if _needs_streamlit(collection_path):
        _SKIPPED_FOR_STREAMLIT.append(collection_path.name)
        return True
    return None


def pytest_report_header(config):  # noqa: ANN001
    if _streamlit_testing_available():
        return None
    return "ATTENZIONE: Streamlit non installato; i test AppTest/UI vengono saltati (pip install -e \".[ui,dev]\")."


def pytest_terminal_summary(terminalreporter, exitstatus, config):  # noqa: ANN001
    if _SKIPPED_FOR_STREAMLIT:
        terminalreporter.write_sep("-", "moduli saltati: Streamlit non installato")
        for name in sorted(set(_SKIPPED_FOR_STREAMLIT)):
            terminalreporter.write_line(f"  {name}")
