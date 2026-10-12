"""
tests/unit/test_packaging_release.py
------------------------------------
Test di packaging REALE per il release gate di ChatAnalysis:
1. Costruisce una wheel reale dal repository (in una directory temporanea).
2. Apre la wheel costruita (zipfile) ed esamina il manifest.
3. Verifica la presenza tassativa del package analysis/ e dei suoi file:
   - analysis/__init__.py
   - analysis/models.py
   - analysis/consistency.py
   - analysis/service.py
4. Verifica la presenza di tutti i package fondamentali:
   - ai/, core/, entity_resolution/, importer/, multimodal/, normalization/,
     search/, ui/, unified/, validation/
5. Estrae la wheel in un ambiente isolato (senza repo root in sys.path)
   e verifica che gli import fondamentali abbiano successo:
   - import analysis
   - import ui.application
   - import ai.lmstudio
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_packaging_wheel_contains_analysis_and_core_packages():
    """Costruisce una wheel reale e verifica i package inclusi e l'import isolato."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        dist_dir = Path(tmp_dir) / "dist"
        dist_dir.mkdir(parents=True, exist_ok=True)

        # 1. Costruzione wheel tramite pip wheel --no-deps
        cmd = [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--wheel-dir",
            str(dist_dir),
            str(REPO_ROOT),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        assert res.returncode == 0, f"pip wheel fallita:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"

        wheels = list(dist_dir.glob("*.whl"))
        assert len(wheels) == 1, f"Attesa 1 wheel, trovate: {wheels}"
        whl_path = wheels[0]

        # 2. Ispezione archivio wheel
        with zipfile.ZipFile(whl_path, "r") as zf:
            namelist = set(zf.namelist())

            # 3. Verifica package analysis/
            required_analysis_files = [
                "analysis/__init__.py",
                "analysis/models.py",
                "analysis/consistency.py",
                "analysis/service.py",
            ]
            for req_file in required_analysis_files:
                assert req_file in namelist, f"File obbligatorio mancante nella wheel: {req_file}"

            # 4. Verifica package fondamentali
            fundamental_packages = [
                "ai/__init__.py",
                "core/__init__.py",
                "entity_resolution/__init__.py",
                "importer/__init__.py",
                "multimodal/__init__.py",
                "normalization/__init__.py",
                "search/__init__.py",
                "ui/__init__.py",
                "unified/__init__.py",
                "validation/__init__.py",
            ]
            for f_pkg in fundamental_packages:
                assert f_pkg in namelist, f"Package fondamentale mancante nella wheel: {f_pkg}"

        # 5. Test di import isolato (senza repo root in PYTHONPATH)
        extract_dir = Path(tmp_dir) / "isolated_site_packages"
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(whl_path, "r") as zf:
            zf.extractall(extract_dir)

        # Esecuzione di uno script Python in sottoprocesso con PYTHONPATH puntato solo a extract_dir
        isolated_script = (
            "import sys\n"
            "import analysis\n"
            "from analysis.models import OperationalAnalysisConfig, DocumentAnalysisState\n"
            "from analysis.service import LocalTopicAnalysisService\n"
            "import ui.application\n"
            "import ai.lmstudio\n"
            "print('ISOLATED_IMPORT_SUCCESS')\n"
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = str(extract_dir)
        # Rimuovi virtualenv path pollution se presente
        proc = subprocess.run(
            [sys.executable, "-c", isolated_script],
            capture_output=True,
            text=True,
            cwd=str(extract_dir),  # Esegui lontano dal repo root
            env=env,
            check=False,
        )
        assert proc.returncode == 0, f"Import isolato fallito:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        assert "ISOLATED_IMPORT_SUCCESS" in proc.stdout
