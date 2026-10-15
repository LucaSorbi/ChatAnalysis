"""
scripts/package_sqlite_legacy_reconciliation_zip.py
---------------------------------------------------
Costruisce il pacchetto di release finale conforme all'audit:
chatAnalysis_sqlite_legacy_final_reconciliation_audit_20261015.zip

Include:
- Repository sorgente completo aggiornato
- Wheel aggiornata (in dist/)
- Test unitari e di integrazione (inclusi i 23 test in test_checkpoint_integrity_scalability.py)
- Log integrali (pytest_and_apptest.log)
- Benchmark quantitativo SQLite vs JSON (benchmark_checkpoint_persistence_results.json)
- Report tecnico completo (CHATANALYSIS_SQLITE_LEGACY_RECONCILIATION_REPORT.md)
- Git diff UTF-8 (git_diff_sqlite_legacy_reconciliation.patch)
- Stato Git e Baseline (GIT_STATUS_AND_BASELINE.md)
- Manifest SHA256 (MANIFEST_SHA256.txt)
- Tre file scientifici congelati verificati (benchmark_frozen_sha256.txt)
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import zipfile

ZIP_NAME = "chatAnalysis_sqlite_legacy_final_reconciliation_audit_20261015.zip"

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
    ".checkpoints",
}

EXCLUDE_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".pyd",
}

INCLUDE_ROOT_FILES = [
    "pyproject.toml",
    "README.md",
    "app.py",
    "run_benchmark.sh",
    "benchmark_frozen_sha256.txt",
    "pytest_and_apptest.log",
    "benchmark_checkpoint_persistence_results.json",
    "git_diff_sqlite_legacy_reconciliation.patch",
    "CHATANALYSIS_SQLITE_LEGACY_RECONCILIATION_REPORT.md",
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
                if file.endswith(".zip"):
                    continue
                rel_path = os.path.relpath(os.path.join(root, file), ".").replace("\\", "/")
                file_list.append(rel_path)

    # Rimuovi duplicati preservando l'ordine
    seen = set()
    unique_list = []
    for f in sorted(file_list):
        norm = f.replace("\\", "/")
        if norm not in seen:
            seen.add(norm)
            unique_list.append(norm)

    return unique_list


def main():
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)

    print(f"Raccolta file per il pacchetto: {ZIP_NAME}...")
    files = collect_files()
    print(f"File totali selezionati per il pacchetto: {len(files)}")

    # 1. Genera MANIFEST_SHA256.txt
    manifest_path = "MANIFEST_SHA256.txt"
    manifest_entries = []
    for f in files:
        digest = sha256_of_file(f)
        manifest_entries.append(f"{digest} *{f}")

    with open(manifest_path, "w", encoding="utf-8") as mf:
        mf.write("\n".join(manifest_entries) + "\n")
    print(f"Manifest SHA256 scritto con successo ({len(manifest_entries)} voci).")

    # Includi il manifest stesso
    all_files_to_pack = files + [manifest_path]

    # 2. Crea archivio ZIP
    zip_path = root / ZIP_NAME
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for f in all_files_to_pack:
            zf.write(f, arcname=f)

    zip_size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"\nArchivio creato con successo: {zip_path.name}")
    print(f"Dimensione archivio: {zip_size_mb:.2f} MB")
    print(f"Numero totale file inclusi: {len(all_files_to_pack)}")

    # 3. Verifica rapida integrita ZIP
    with zipfile.ZipFile(zip_path, "r") as zf:
        bad_file = zf.testzip()
        if bad_file:
            raise RuntimeError(f"Errore di integrità CRC nello ZIP: {bad_file}")
        namelist = zf.namelist()
        print(f"Verifica CRC ZIP: OK ({len(namelist)} file verificati)")


if __name__ == "__main__":
    main()
