"""
scripts/create_windows_readiness_archive.py
-------------------------------------------
Genera l'archivio zip di audit per la migrazione e stabilizzazione su Windows:
chatAnalysis_windows_topic_pipeline_readiness_audit_20261020.zip

Requisito vincolante:
Lo ZIP deve essere creato DIRETTAMENTE FUORI dalla cartella Tesi e FUORI dal repository Git.
Non deve essere creato temporaneamente dentro il repository per poi essere spostato.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import zipfile

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TARGET_ARCHIVE_DIR = PROJECT_ROOT.parent
ARCHIVE_NAME = "chatAnalysis_windows_topic_pipeline_readiness_audit_20261020.zip"
TARGET_ARCHIVE_PATH = TARGET_ARCHIVE_DIR / ARCHIVE_NAME


def run_command_capture(cmd: list[str], cwd: Path | None = None) -> tuple[int, str]:
    """Esegue un comando e cattura output ed exit code."""
    proc = subprocess.run(
        cmd,
        cwd=str(cwd or PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    combined = f"COMMAND: {' '.join(cmd)}\nEXIT_CODE: {proc.returncode}\n\nSTDOUT:\n{proc.stdout}\n\nSTDERR:\n{proc.stderr}\n"
    return proc.returncode, combined


def main() -> int:
    print(f"Generazione archivio di audit Windows & Topic Pipeline:")
    print(f"Destinazione: {TARGET_ARCHIVE_PATH}")

    # Verifica vincolo di localizzazione archivio
    if str(PROJECT_ROOT) in str(TARGET_ARCHIVE_PATH):
        raise RuntimeError(f"VIOLAZIONE: Il target {TARGET_ARCHIVE_PATH} si trova dentro il repository!")

    # 1. Raccolta log reali di test per certificazione
    print("\n[1/4] Raccolta log di esecuzione test...")
    test_logs: dict[str, str] = {}

    print("  - Esecuzione pytest Topic Stabilization (Windows)...")
    code_stab, out_stab = run_command_capture([sys.executable, "-m", "pytest", "tests/unit/test_topic_discovery_detection_stabilization.py", "-v"])
    test_logs["test_logs/stabilization_suite_windows_pytest.log"] = out_stab

    print("  - Esecuzione pytest Snapshot Ownership F1 (Windows)...")
    code_f1, out_f1 = run_command_capture([
        sys.executable, "-m", "pytest", "tests/unit/test_checkpoint_preflight_resume.py",
        "-k", "test_122 or test_136 or test_137 or test_138 or test_139 or test_140 or test_141 or test_144", "-v"
    ])
    test_logs["test_logs/snapshot_ownership_f1_windows_pytest.log"] = out_f1

    print("  - Esecuzione pytest UI Manual Detection (Windows)...")
    code_ui, out_ui = run_command_capture([sys.executable, "-m", "pytest", "tests/unit/test_ui_manual_topic_detection.py", "-v"])
    test_logs["test_logs/ui_manual_detection_windows_pytest.log"] = out_ui

    print("  - Esecuzione Smoke Test Operativo (Mock Mode)...")
    code_smoke, out_smoke = run_command_capture([sys.executable, "scripts/smoke_operational_ai.py", "--mock"])
    test_logs["test_logs/smoke_operational_ai_mock.log"] = out_smoke

    print("  - Esecuzione compileall...")
    code_comp, out_comp = run_command_capture([sys.executable, "-m", "compileall", "-q", "core", "importer", "validation", "normalization", "entity_resolution", "unified", "multimodal", "ai", "analysis", "search", "ui", "profiler", "scripts", "tests"])
    test_logs["test_logs/compileall_check.log"] = out_comp

    # Tentativo WSL Linux per log cross-platform se disponibile
    try:
        print("  - Esecuzione pytest Topic Stabilization (Linux WSL)...")
        code_wsl, out_wsl = run_command_capture(["wsl", "python3", "-m", "pytest", "tests/unit/test_topic_discovery_detection_stabilization.py", "-v"])
        test_logs["test_logs/stabilization_suite_linux_wsl_pytest.log"] = out_wsl
    except Exception as e:
        test_logs["test_logs/stabilization_suite_linux_wsl_pytest.log"] = f"WSL execution skipped or unavailable: {e}\n"

    # 2. Selezione file da includere
    print("\n[2/4] Raccolta file sorgenti, test, script e documentazione...")
    included_files: dict[str, Path] = {}

    # File radice
    for rf in [
        "pyproject.toml",
        "README.md",
        "WINDOWS_TOPIC_PIPELINE_RUNBOOK.md",
        "CHATANALYSIS_WINDOWS_TOPIC_PIPELINE_REPORT.md",
    ]:
        p = PROJECT_ROOT / rf
        if p.is_file():
            included_files[rf] = p

    # Package sorgente
    source_dirs = [
        "core", "importer", "validation", "normalization",
        "entity_resolution", "unified", "multimodal", "ai",
        "analysis", "search", "ui", "profiler",
    ]
    for sdir in source_dirs:
        dir_p = PROJECT_ROOT / sdir
        if dir_p.is_dir():
            for root, _, files in os.walk(dir_p):
                for f in files:
                    if f.endswith((".py", ".json", ".md")):
                        full_p = Path(root) / f
                        if "__pycache__" not in str(full_p):
                            rel_p = full_p.relative_to(PROJECT_ROOT).as_posix()
                            included_files[rel_p] = full_p

    # Script
    scripts_dir = PROJECT_ROOT / "scripts"
    for sf in scripts_dir.iterdir():
        if sf.is_file() and sf.suffix in (".py", ".ps1", ".md", ".sh"):
            if "__pycache__" not in str(sf):
                rel_p = sf.relative_to(PROJECT_ROOT).as_posix()
                included_files[rel_p] = sf

    # Test
    tests_dir = PROJECT_ROOT / "tests"
    for root, _, files in os.walk(tests_dir):
        for f in files:
            if f.endswith((".py", ".md")):
                full_p = Path(root) / f
                if "__pycache__" not in str(full_p):
                    rel_p = full_p.relative_to(PROJECT_ROOT).as_posix()
                    included_files[rel_p] = full_p

    # Fixture sintetiche
    synth_dir = PROJECT_ROOT / "test_data" / "synthetic_smoke"
    if synth_dir.is_dir():
        for sf in synth_dir.iterdir():
            if sf.is_file():
                rel_p = sf.relative_to(PROJECT_ROOT).as_posix()
                included_files[rel_p] = sf

    print(f"  Totale file fisici da includere: {len(included_files)}")
    print(f"  Totale log sintetici da includere: {len(test_logs)}")

    # 3. Creazione archivio ZIP direttamente fuori dal repository
    print(f"\n[3/4] Creazione archivio ZIP: {TARGET_ARCHIVE_PATH}...")
    manifest_lines: list[str] = []

    with zipfile.ZipFile(TARGET_ARCHIVE_PATH, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Aggiungi file fisici
        for arcname, file_path in sorted(included_files.items()):
            content_bytes = file_path.read_bytes()
            sha256 = hashlib.sha256(content_bytes).hexdigest()
            manifest_lines.append(f"{sha256}  {arcname}")
            zf.writestr(arcname, content_bytes)

        # Aggiungi file di log
        for arcname, log_text in sorted(test_logs.items()):
            log_bytes = log_text.encode("utf-8")
            sha256 = hashlib.sha256(log_bytes).hexdigest()
            manifest_lines.append(f"{sha256}  {arcname}")
            zf.writestr(arcname, log_bytes)

        # Aggiungi manifest SHA-256
        manifest_content = "\n".join(manifest_lines) + "\n"
        zf.writestr("MANIFEST_SHA256.txt", manifest_content.encode("utf-8"))

    archive_size_mb = TARGET_ARCHIVE_PATH.stat().st_size / (1024 * 1024)
    print(f"  [OK] Archivio creato: {TARGET_ARCHIVE_PATH} ({archive_size_mb:.2f} MB)")

    # 4. Validazione dell'archivio generato
    print("\n[4/4] Validazione integrità archivio generato...")
    with zipfile.ZipFile(TARGET_ARCHIVE_PATH, "r") as check_zf:
        namelist = check_zf.namelist()
        print(f"  File presenti nell'archivio: {len(namelist)}")
        assert "MANIFEST_SHA256.txt" in namelist
        assert "WINDOWS_TOPIC_PIPELINE_RUNBOOK.md" in namelist
        assert "CHATANALYSIS_WINDOWS_TOPIC_PIPELINE_REPORT.md" in namelist
        assert "scripts/windows_setup_and_verify.ps1" in namelist
        assert "scripts/run_streamlit_windows.ps1" in namelist
        assert "tests/unit/test_topic_discovery_detection_stabilization.py" in namelist
        assert "tests/unit/test_checkpoint_preflight_resume.py" in namelist

        # Verifica manifest
        manifest_raw = check_zf.read("MANIFEST_SHA256.txt").decode("utf-8")
        lines = [line.strip() for line in manifest_raw.strip().splitlines() if line.strip()]
        for line in lines:
            sha, fname = line.split("  ", 1)
            file_data = check_zf.read(fname)
            actual_sha = hashlib.sha256(file_data).hexdigest()
            if actual_sha != sha:
                raise ValueError(f"Discrepanza SHA256 per {fname}: atteso {sha}, calcolato {actual_sha}")

        print(f"  [OK] Manifest verificato: {len(lines)} file controllati con SHA-256 esatti.")

    print("\n=================================================================")
    print("CONSEGNA GENERATA CON SUCCESSO!")
    print(f"File: {TARGET_ARCHIVE_PATH}")
    print(f"Dimensione: {archive_size_mb:.2f} MB")
    print("=================================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
