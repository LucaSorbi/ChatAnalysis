"""
scripts/build_final_gate_validation_bundle.py
---------------------------------------------
Generatore autoritativo e verificabile del bundle di release gate:
chatAnalysis_final_gate_validation_20261010.zip

Esegue e cattura i log raw reali di:
- Python compileall
- Test mirati dei tre blocker (Blocker 1, 2, 3)
- Full pytest suite
- Streamlit AppTest suites
- Wheel build e ispezione bytecode (.pyc count == 0)
- Isolated wheel import in sandbox temporanea
- Git status e git diff sui componenti congelati
- Creazione del bundle ZIP con README.md, app.py, .streamlit/, sorgenti, test, manifest SHA-256
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUNDLE_ZIP_NAME = "chatAnalysis_final_gate_validation_20261010.zip"
OUTPUT_ZIP_PATH = PROJECT_ROOT / BUNDLE_ZIP_NAME


def compute_sha256(file_path: Path) -> str:
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def run_and_log(cmd: list[str], log_file: Path, cwd: Path = PROJECT_ROOT) -> int:
    """Esegue un comando di sistema e salva l'output grezzo con metadati nel file di log."""
    header = f"=== COMMAND EXECUTION LOG ===\nCommand: {' '.join(cmd)}\nWorking Dir: {cwd}\nPython: {sys.version}\nPlatform: {sys.platform}\n=============================\n\n"
    print(f"  [EXEC] {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600)
        output = header + proc.stdout + "\n" + proc.stderr + f"\n\nExit Code: {proc.returncode}\n"
        log_file.write_text(output, encoding="utf-8")
        return proc.returncode
    except Exception as exc:
        output = header + f"EXCEPTION OCCURRED: {exc}\nExit Code: -1\n"
        log_file.write_text(output, encoding="utf-8")
        return -1


def main() -> int:
    print(f"======================================================================")
    print(f"COSTRUZIONE BUNDLE AUTORITATIVO: {BUNDLE_ZIP_NAME}")
    print(f"======================================================================")

    # Pulizia preventiva di __pycache__ e file .pyc nel workspace
    for pycache in PROJECT_ROOT.rglob("__pycache__"):
        if ".venv" not in pycache.parts and ".git" not in pycache.parts:
            shutil.rmtree(pycache, ignore_errors=True)

    with tempfile.TemporaryDirectory(prefix="chatanalysis_final_gate_") as tmp_dir_str:
        bundle_root = Path(tmp_dir_str) / "chatAnalysis_final_gate_validation_20261010"
        bundle_root.mkdir(parents=True, exist_ok=True)
        reports_dir = bundle_root / "audit_reports"
        reports_dir.mkdir(parents=True, exist_ok=True)

        # 1. Copia file radice fondamentali (inclusi README.md, app.py, configurazioni)
        root_files = [
            "README.md",
            "app.py",
            "pyproject.toml",
            "MANIFEST.in",
            ".gitattributes",
            "run_benchmark.sh",
            "FINAL_GATE_VALIDATION_REPORT.md",
            "large_file_benchmark_results.json",
        ]
        for rf in root_files:
            src = PROJECT_ROOT / rf
            if src.exists():
                shutil.copy2(src, bundle_root / rf)
                print(f"  + File Radice: {rf}")
            else:
                print(f"  ! ATTENZIONE File Radice mancante: {rf}")

        # 1b. Copia cartella .streamlit
        streamlit_dir = PROJECT_ROOT / ".streamlit"
        if streamlit_dir.exists():
            shutil.copytree(
                streamlit_dir,
                bundle_root / ".streamlit",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            print("  + Configurazione Streamlit: .streamlit/")

        # 2. Copia tutti i package sorgente
        source_dirs = [
            "ai",
            "analysis",
            "core",
            "entity_resolution",
            "importer",
            "multimodal",
            "normalization",
            "profiler",
            "search",
            "ui",
            "unified",
            "validation",
            "scripts",
        ]
        for sd in source_dirs:
            src_dir = PROJECT_ROOT / sd
            if src_dir.exists():
                dst_dir = bundle_root / sd
                shutil.copytree(
                    src_dir,
                    dst_dir,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "*.tmp"),
                )
                print(f"  + Package Sorgente: {sd}/")

        # 3. Copia test e test_data
        for td in ("tests", "test_data"):
            src_dir = PROJECT_ROOT / td
            if src_dir.exists():
                shutil.copytree(
                    src_dir,
                    bundle_root / td,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".pytest_cache"),
                )
                print(f"  + Suite Test: {td}/")

        # 4. Esecuzione e archiviazione LOG RAW REALI
        python_exe = sys.executable

        print("\n--- [RELEASE GATE EXECUTION & LOG CAPTURE] ---")
        # 4.1 Python compileall
        run_and_log([python_exe, "-m", "compileall", "-q", "."], reports_dir / "raw_python_compileall_log.txt")

        # 4.2 Git Status & Diffs
        run_and_log(["git", "status", "--short"], reports_dir / "raw_git_status_log.txt")
        run_and_log(["git", "diff", "--stat", "HEAD"], reports_dir / "raw_git_diff_modified_log.txt")
        run_and_log(
            ["git", "diff", "HEAD", "--", "ai/experiment.py", "ai/benchmark.py", "benchmark/"],
            reports_dir / "raw_git_diff_frozen_benchmark_log.txt",
        )
        run_and_log(["git", "ls-files", "--stage", "run_benchmark.sh"], reports_dir / "raw_run_benchmark_sh_git_stage.txt")

        # 4.3 Test mirati dei tre blocker
        run_and_log(
            [
                python_exe, "-m", "pytest", "-v",
                "tests/unit/test_lmstudio_model_loading.py",
                "tests/unit/test_media_registry_session_isolation.py",
                "tests/unit/test_discovery_provenance_service.py",
            ],
            reports_dir / "raw_targeted_blockers_pytest_log.txt",
        )

        # 4.4 AppTest suite
        run_and_log(
            [
                python_exe, "-m", "pytest", "-v",
                "tests/unit/test_apptest_flows.py",
                "tests/unit/test_ui_smoke.py",
                "tests/unit/test_ui_manual_topic_detection.py",
                "tests/unit/test_large_file_upload.py",
            ],
            reports_dir / "raw_streamlit_apptest_log.txt",
        )

        # 4.5 Wheel build
        dist_dir = PROJECT_ROOT / "dist"
        dist_dir.mkdir(parents=True, exist_ok=True)
        for old_whl in dist_dir.glob("*.whl"):
            try:
                old_whl.unlink()
            except Exception:
                pass
        run_and_log([python_exe, "-m", "pip", "wheel", "--no-deps", "-w", str(dist_dir), "."], reports_dir / "raw_wheel_build_log.txt")

        # 4.6 Wheel inspection & Isolated Wheel Import
        whl_files = list(dist_dir.glob("*.whl"))
        whl_inspection_lines = [f"Found wheel: {whl_files[0].name if whl_files else 'NONE'}\n"]
        if whl_files:
            target_whl = whl_files[0]
            with zipfile.ZipFile(target_whl, "r") as zf:
                namelist = zf.namelist()
                pyc_count = sum(1 for name in namelist if name.endswith(".pyc") or "__pycache__" in name)
                whl_inspection_lines.append(f"Total files in wheel: {len(namelist)}")
                whl_inspection_lines.append(f"Bytecode .pyc count: {pyc_count} (EXACT REQUIREMENT: 0)")
                whl_inspection_lines.append("\nTop-level packages:")
                top_pkgs = sorted(list({p.split("/")[0] for p in namelist if "/" in p}))
                whl_inspection_lines.append(f"{top_pkgs}")
            (reports_dir / "raw_wheel_inspection_log.txt").write_text("\n".join(whl_inspection_lines), encoding="utf-8")

            # Isolated wheel import test in separate temp directory
            with tempfile.TemporaryDirectory(prefix="isolated_whl_test_") as iso_dir:
                iso_path = Path(iso_dir)
                test_script = iso_path / "test_import.py"
                test_script.write_text(
                    "import sys\n"
                    "import whatsapp_forensic_importer\n"
                    "import ai, analysis, core, entity_resolution, importer, multimodal, normalization, profiler, search, ui, unified, validation\n"
                    "print('ALL 12 PACKAGES IMPORTED SUCCESSFULLY')\n",
                    encoding="utf-8",
                )
                # Unpack wheel into site_packages inside iso_dir
                site_pkg = iso_path / "site-packages"
                with zipfile.ZipFile(target_whl, "r") as zf:
                    zf.extractall(site_pkg)
                env = os.environ.copy()
                env["PYTHONPATH"] = str(site_pkg)
                iso_proc = subprocess.run([python_exe, str(test_script)], cwd=iso_path, env=env, capture_output=True, text=True)
                (reports_dir / "raw_isolated_wheel_import_log.txt").write_text(
                    f"Command: {python_exe} {test_script}\nExit Code: {iso_proc.returncode}\nStdout: {iso_proc.stdout}\nStderr: {iso_proc.stderr}\n",
                    encoding="utf-8",
                )

        # 4.7 Full pytest suite execution
        run_and_log([python_exe, "-m", "pytest", "-q"], reports_dir / "raw_full_pytest_log.txt")

        # 5. Generazione SHA-256 MANIFEST
        print("\n--- [GENERAZIONE MANIFEST CRITTOGRAFICO SHA-256] ---")
        all_files: list[Path] = []
        for root, _, files in os.walk(bundle_root):
            for f in sorted(files):
                all_files.append(Path(root) / f)

        manifest_lines: list[str] = []
        for fp in sorted(all_files):
            rel = fp.relative_to(bundle_root).as_posix()
            sha = compute_sha256(fp)
            manifest_lines.append(f"{sha}  {rel}")

        manifest_file = bundle_root / "SHA256_MANIFEST.txt"
        manifest_file.write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
        print(f"  + Manifest crittografico generato: {len(manifest_lines)} file registrati")

        # 6. Compressione finale in ZIP
        print(f"\n--- [COMPRESSIONE BUNDLE ZIP: {BUNDLE_ZIP_NAME}] ---")
        if OUTPUT_ZIP_PATH.exists():
            OUTPUT_ZIP_PATH.unlink()

        with zipfile.ZipFile(OUTPUT_ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for root, _, files in os.walk(bundle_root):
                for f in sorted(files):
                    abs_path = Path(root) / f
                    arcname = abs_path.relative_to(bundle_root.parent).as_posix()
                    zf.write(abs_path, arcname=arcname)

        zip_sha256 = compute_sha256(OUTPUT_ZIP_PATH)
        zip_size_mb = OUTPUT_ZIP_PATH.stat().st_size / (1024 * 1024)
        print(f"Bundle ZIP creato con successo: {OUTPUT_ZIP_PATH}")
        print(f"Dimensione: {zip_size_mb:.2f} MB")
        print(f"SHA-256: {zip_sha256}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
