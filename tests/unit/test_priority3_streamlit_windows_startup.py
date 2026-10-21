"""
test_priority3_streamlit_windows_startup.py
-------------------------------------------
Test per la Priorità 3:
1. Verifica dell'entry point effettivo 'app.py' su Windows.
2. Verifica importazione DEMO, navigazione completa fra le 6 sezioni.
3. Presenza e render delle schede Discovery e Detection.
4. Smoke test con subprocess reale di Streamlit in modalità headless con verifica health check HTTP.
5. Verifica selezione interprete Python in PowerShell ('python' e 'py -3').
"""
from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import pytest

from streamlit.testing.v1 import AppTest
from ui.models import DatasetMode

APP_PATH = str(Path(__file__).resolve().parent.parent.parent / "app.py")


def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestPriority3StreamlitWindowsStartup:
    """Verifiche formali dell'avvio interfaccia Streamlit su Windows."""

    def test_app_entrypoint_exists_and_is_app_py(self) -> None:
        """Verifica che l'entrypoint sia 'app.py' nel root del repository e non 'ui/application.py'."""
        root = Path(__file__).resolve().parent.parent.parent
        app_py = root / "app.py"
        assert app_py.is_file(), f"Entry point app.py non trovato in {root}"
        
        # ui/application.py è modulo puro, non deve essere l'entrypoint di streamlit run
        ui_app = root / "ui" / "application.py"
        assert ui_app.is_file()
        content = ui_app.read_text(encoding="utf-8")
        assert "import streamlit" not in content, "ui/application.py non deve dipendere da streamlit (logica pura)"

    def test_apptest_demo_load_and_navigation(self) -> None:
        """Verifica caricamento dataset DEMO e navigazione su tutte le 6 sezioni con presenza schede Discovery/Detection."""
        at = AppTest.from_file(APP_PATH, default_timeout=25)
        at.run()
        assert not at.exception, f"Errore all'avvio: {at.exception}"

        # 1. Naviga su 2. Importazione
        at.sidebar.radio[0].set_value("2. Importazione")
        at.run()
        assert not at.exception

        # Clicca 'Carica Dataset Dimostrativo'
        demo_btn = [b for b in at.button if "Dimostrativo" in b.label or "DEMO" in b.label]
        assert len(demo_btn) >= 1, f"Pulsante caricamento DEMO non trovato fra i pulsanti: {[b.label for b in at.button]}"
        demo_btn[0].click()
        at.run()
        assert not at.exception

        # Verifica dataset caricato in session_state
        assert at.session_state["dataset_loaded"] is True
        assert at.session_state["dataset_mode"] == DatasetMode.DEMO

        # 2. Naviga su 5. Analisi topic
        at.sidebar.radio[0].set_value("5. Analisi topic")
        at.run()
        assert not at.exception

        # Verifica presenza delle schede / tab Discovery e Detection
        tab_labels = [t.label for t in at.tabs]
        assert any("Discovery" in lbl for lbl in tab_labels), f"Scheda Discovery non trovata in tabs: {tab_labels}"
        assert any("Detection" in lbl for lbl in tab_labels), f"Scheda Detection non trovata in tabs: {tab_labels}"

        # 3. Naviga su tutte le altre sezioni per garantire assenza di crash
        for page in ["1. Panoramica", "3. Esplora conversazione", "4. Ricerca", "6. Sistema / Stato"]:
            at.sidebar.radio[0].set_value(page)
            at.run()
            assert not at.exception, f"Errore nella navigazione verso {page}: {at.exception}"

    def test_streamlit_real_subprocess_smoke(self) -> None:
        """Avvia un vero processo Streamlit headless su porta locale libera e interroga l'endpoint di health check."""
        port = _get_free_port()
        cmd = [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            APP_PATH,
            "--server.port",
            str(port),
            "--server.headless",
            "true",
            "--browser.gatherUsageStats",
            "false",
        ]

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["STREAMLIT_SERVER_HEADLESS"] = "true"

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        health_url = f"http://127.0.0.1:{port}/_stcore/health"
        root_url = f"http://127.0.0.1:{port}/"

        healthy = False
        start_time = time.time()
        try:
            # Attendi fino a 20 secondi che il server risponda
            while time.time() - start_time < 20:
                if proc.poll() is not None:
                    stdout, stderr = proc.communicate()
                    pytest.fail(f"Streamlit terminato prematuramente con codice {proc.returncode}.\nSTDOUT: {stdout}\nSTDERR: {stderr}")

                try:
                    with urllib.request.urlopen(health_url, timeout=1.5) as resp:
                        if resp.status == 200:
                            healthy = True
                            break
                except Exception:
                    time.sleep(0.5)

            assert healthy, f"Streamlit non ha risposto 200 OK su {health_url} entro il timeout."

            # Verifica anche la pagina principale
            with urllib.request.urlopen(root_url, timeout=3.0) as resp:
                assert resp.status == 200
                html = resp.read().decode("utf-8", errors="ignore")
                assert "<!DOCTYPE html>" in html or "streamlit" in html.lower() or "chatanalysis" in html.lower()

        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    def test_powershell_python_command_array_invocation(self) -> None:
        """Verifica che l'invocazione di comandi con argomenti array in PowerShell (es. 'py -3') funzioni senza errori sintattici."""
        # Testiamo che l'interprete risponda correttamente tramite comando PowerShell nativo
        ps_script = """
        $PythonExe = $env:PYTHON_TEST_EXE
        $PythonArgs = @("-c", "import sys; print(f'PY_OK_{sys.version_info.major}')")
        & $PythonExe @PythonArgs
        """
        env = os.environ.copy()
        env["PYTHON_TEST_EXE"] = sys.executable

        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            env=env,
        )
        assert res.returncode == 0, f"PowerShell array invocation fallita: {res.stderr}"
        assert f"PY_OK_{sys.version_info.major}" in res.stdout
