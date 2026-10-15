"""
scripts/package_final_performance_accounting_zip.py
---------------------------------------------------
Costruisce il pacchetto di release finale conforme all'audit:
chatAnalysis_final_performance_accounting_audit_20261013.zip

Include:
- Repository sorgente completo aggiornato
- Wheel aggiornata (in dist/)
- Test unitari e di integrazione (inclusi i nuovi test in test_performance_accounting_audit.py)
- Log integrali (pytest_and_apptest.log)
- Benchmark prestazionali riproducibili (docs/SCALABILITY_MEASUREMENTS_REPORT.md)
- Report tecnico completo (CHATANALYSIS_FINAL_PERFORMANCE_ACCOUNTING_REPORT.md)
- Git diff UTF-8 (git_diff_final_performance_accounting.patch)
- Stato Git e Baseline (GIT_STATUS_AND_BASELINE.md)
- Manifest SHA256 (MANIFEST_SHA256.txt)
- Tre file congelati verificati (benchmark_frozen_sha256.txt)
"""
from __future__ import annotations

import hashlib
import os
import zipfile
from pathlib import Path

ZIP_NAME = "chatAnalysis_final_performance_accounting_audit_20261013.zip"

EXCLUDE_DIRS = {
    ".git",
    ".venv",
    ".pytest_cache",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
    "build",
}

EXCLUDE_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".pyd",
}

EXCLUDE_FILES = {
    ZIP_NAME,
    "chatAnalysis_post_audit_corrective_release_20261013.zip",
    "chatAnalysis_integrated_scalability_progress_http400_audit_20261013.zip",
    "chatAnalysis_realworld_topic_analysis_improvements_20261013.zip",
}

INCLUDE_ROOT_FILES = [
    "pyproject.toml",
    "README.md",
    "app.py",
    "run_benchmark.sh",
    "benchmark_frozen_sha256.txt",
    "pytest_and_apptest.log",
    "git_diff_final_performance_accounting.patch",
    "CHATANALYSIS_FINAL_PERFORMANCE_ACCOUNTING_REPORT.md",
    "GIT_STATUS_AND_BASELINE.md",
]

INCLUDE_DIRS = [
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
    "tests",
    "test_data",
    "docs",
    "scripts",
    "dist",
]


def sha256_of_file(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().upper()


def collect_files() -> list[str]:
    file_list = []

    # Root files
    for rf in INCLUDE_ROOT_FILES:
        if os.path.isfile(rf):
            file_list.append(rf)

    # Directory trees
    for d in INCLUDE_DIRS:
        if not os.path.isdir(d):
            continue
        for root, dirs, files in os.walk(d):
            dirs[:] = [sub for sub in dirs if sub not in EXCLUDE_DIRS]
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in EXCLUDE_EXTENSIONS:
                    continue
                if file in EXCLUDE_FILES:
                    continue
                if ext in {".gguf", ".bin", ".onnx", ".db", ".sqlite"} and "test" not in root:
                    continue
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, ".").replace("\\", "/")
                file_list.append(rel_path)

    return sorted(list(set(file_list)))


def main():
    files = collect_files()
    print(f"Raccolti {len(files)} file per il rilascio {ZIP_NAME}.")

    manifest_lines = []
    for fpath in files:
        h = sha256_of_file(fpath)
        manifest_lines.append(f"{h}  {fpath}\n")

    with open("MANIFEST_SHA256.txt", "w", encoding="utf-8") as mf:
        mf.writelines(manifest_lines)
    print(f"Generato MANIFEST_SHA256.txt con {len(manifest_lines)} voci.")

    files.append("MANIFEST_SHA256.txt")

    print(f"Creazione archivio {ZIP_NAME}...")
    with zipfile.ZipFile(ZIP_NAME, "w", zipfile.ZIP_DEFLATED) as zf:
        for fpath in files:
            zf.write(fpath, arcname=fpath)

    zip_size = os.path.getsize(ZIP_NAME)
    zip_hash = sha256_of_file(ZIP_NAME)
    print(f"\n[SUCCESSO] Creato archivio finale:")
    print(f"  Nome:       {ZIP_NAME}")
    print(f"  Dimensione: {zip_size:,} bytes")
    print(f"  SHA256:     {zip_hash}")


if __name__ == "__main__":
    main()
