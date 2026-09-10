"""
tests/unit/test_smoke.py
------------------------
Smoke test: verifica che l'ambiente di test funzioni
e che i package principali siano importabili.

Questi test devono sempre passare; se falliscono indica
un problema di installazione o di struttura del progetto,
non un bug nel codice applicativo.
"""
import sys

import pytest


@pytest.mark.unit
def test_python_version():
    """Python 3.12+ richiesto."""
    assert sys.version_info >= (3, 12), (
        f"Python 3.12+ richiesto, trovato {sys.version_info}"
    )


@pytest.mark.unit
def test_profiler_package_importable():
    """Il package profiler deve essere importabile."""
    import profiler  # noqa: F401


@pytest.mark.unit
def test_profiler_config_importable():
    """ProfilerConfig deve essere importabile."""
    from profiler.config import ProfilerConfig
    cfg = ProfilerConfig()
    assert cfg.anonymize is True
    assert cfg.include_sample_text is False


@pytest.mark.unit
def test_importer_package_importable():
    """Il package importer deve essere importabile."""
    import importer  # noqa: F401


@pytest.mark.unit
def test_raw_record_importable():
    """RawRecord deve essere importabile dal package importer."""
    from importer import RawRecord
    assert RawRecord is not None


@pytest.mark.unit
def test_base_importer_importable():
    """BaseImporter deve essere importabile dal package importer."""
    from importer import BaseImporter
    assert BaseImporter is not None


@pytest.mark.unit
def test_pytest_is_recent():
    """pytest deve essere almeno versione 8."""
    import pytest as pt
    major = int(pt.__version__.split(".")[0])
    assert major >= 8, f"pytest>=8 richiesto, trovato {pt.__version__}"
