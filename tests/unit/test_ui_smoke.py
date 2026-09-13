from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = str(Path(__file__).resolve().parents[2] / "app.py")


@pytest.mark.unit
class TestUiSmoke:

    def test_app_entrypoint_importable(self):
        """Verifica che app.py sia importabile direttamente senza crash."""
        import app
        assert app is not None

    def test_app_headless_initial_run(self):
        """Verifica l'esecuzione headless iniziale dell'applicazione Streamlit."""
        at = AppTest.from_file(APP_PATH, default_timeout=15)
        at.run()
        assert not at.exception
        assert len(at.sidebar.radio) >= 1
        assert at.sidebar.radio[0].value == "1. Panoramica"

    def test_app_headless_navigation(self):
        """Verifica la navigazione tra le sezioni principali dell'app."""
        at = AppTest.from_file(APP_PATH, default_timeout=15)
        at.run()
        assert not at.exception

        pages = [
            "1. Panoramica",
            "2. Importazione",
            "3. Esplora conversazione",
            "4. Ricerca",
            "5. Analisi topic",
            "6. Sistema / Stato",
        ]

        for p in pages:
            at.sidebar.radio[0].set_value(p)
            at.run()
            assert not at.exception, f"Eccezione rilevata durante la navigazione su {p}"
