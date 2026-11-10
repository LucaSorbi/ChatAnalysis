"""
scripts/create_sanitized_final_zip.py
-------------------------------------
Script per creare il rilascio finale ZIP pulito e sanitizzato fuori dal repository:
C:\\Users\\Luca\\Desktop\\CHATANALYSIS_SPRINT_TESI_06NOV2026.zip

Regole inderogabili di consegna:
1. ZIP situato sul Desktop (fuori dalla cartella del repository).
2. Generazione del manifest SHA-256 e CRC32.
3. Allowlist rigorosa di file e cartelle del codice sorgente.
4. Esclusione tassativa di:
   - test_data/reference_chat_document.zip (chat reale privata)
   - Database SQLite (*.db, *-wal, *-shm)
   - Cartelle checkpoint e raw completions audit (output/)
   - Modelli GGUF, venv, .git, file temporanei e cache pycache
5. Ispezione e verifica automatica del contenuto del pacchetto prima del completamento.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
import sys
import zipfile
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DESKTOP_DIR = Path("C:/Users/Luca/Desktop")
ZIP_NAME = "CHATANALYSIS_SPRINT_TESI_06NOV2026.zip"
OUTPUT_ZIP_PATH = DESKTOP_DIR / ZIP_NAME
CHECKSUM_FILE_PATH = DESKTOP_DIR / f"{ZIP_NAME}.sha256"

# Directory e file da includere (allowlist)
ALLOWED_TOP_DIRS = [
    "ai",
    "analysis",
    "core",
    "docs",
    "entity_resolution",
    "importer",
    "multimodal",
    "normalization",
    "profiler",
    "scripts",
    "search",
    "test_data",
    "tests",
    "ui",
    "unified",
    "validation",
]

ALLOWED_TOP_FILES = [
    "app.py",
    "CHATANALYSIS_AUDIT_FINALE_PROVENANCE_20261027.md",
    "CHATANALYSIS_SPRINT_TESI_06NOV2026_REPORT.md",
    "CHATANALYSIS_TESI_RISULTATI_E_DISCUSSIONE_20261027.md",
    "pyproject.toml",
    "README.md",
    "requirements.txt",
    "run_benchmark.sh",
    "run_streamlit.bat",
]

# Allowlist esplicita per scripts/ (esclude script di ricostruzione storica, .disabled e debug ad-hoc)
ALLOWED_SCRIPTS = {
    "__init__.py",
    "benchmark_checkpoint_persistence.py",
    "benchmark_large_ingestion.py",
    "create_sanitized_final_zip.py",
    "create_scalability_audit_archive.py",
    "diagnose_checkpoints_safe.py",
    "diagnose_ubuntu_live_24of26.py",
    "generate_large_synthetic_chat_2000.py",
    "generate_synthetic_export_examples.py",
    "measure_scalability_and_profiling.py",
    "offline_scalability_benchmark.py",
    "preflight_checkpoint_resume.py",
    "run_canonical_live_discovery_26chunks.py",
    "run_phase2_live_detection_and_consistency.py",
    "run_phase3_synthetic_scalability.py",
    "sanitize_synthetic_dataset.py",
    "smoke_operational_ai.py",
    "test_real_microchunk_discovery.py",
    "verify_package_manifest.py",
    "verify_real_1049_chat_discovery.py",
}

FORBIDDEN_DOC_FILES = {
    "CHATANALYSIS_AUDIT_PROVENIENZA_ZERO_TOPICS.md",
    "CHATANALYSIS_DISCOVERY_26_CHUNK_ZERO_TOPICS_ROOT_CAUSE_20261026.md",
    "CHATANALYSIS_FINAL_ENGINE_RECOVERY_REPORT_20261026.md",
}

# Pattern da escludere sempre e tassativamente
FORBIDDEN_EXTENSIONS = {
    ".pyc", ".pyo", ".pyd",
    ".db", ".sqlite", ".sqlite3",
    "-wal", "-shm",
    ".zip", ".tar", ".gz",
    ".gguf", ".bin",
    ".log", ".disabled",
}

FORBIDDEN_DIR_NAMES = {
    ".git", ".github",
    ".venv", ".venv312", "venv", "env",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "output", "temp", "tmp", "scratch",
    "raw_completions_audit", "checkpoints",
}


def is_file_allowed(rel_path: Path) -> bool:
    parts = rel_path.parts
    # Escludi cartelle vietate a qualsiasi livello
    for part in parts:
        if part in FORBIDDEN_DIR_NAMES or part.startswith(".venv"):
            return False

    # Escludi estensioni vietate
    suffix = rel_path.suffix.lower()
    if suffix in FORBIDDEN_EXTENSIONS or rel_path.name.endswith("-wal") or rel_path.name.endswith("-shm") or rel_path.name.endswith(".disabled"):
        return False

    # Allowlist rigorosa per scripts/
    if parts[0] == "scripts":
        return len(parts) == 2 and parts[1] in ALLOWED_SCRIPTS

    # Escludi report di debug intermedi superati in docs/
    if parts[0] == "docs" and rel_path.name in FORBIDDEN_DOC_FILES:
        return False

    # Escludi esplicitamente qualsiasi chat privata o zip in test_data (eccetto synthetic_large)
    if "test_data" in parts:
        if "synthetic_large" not in parts and suffix in (".zip", ".txt", ".csv"):
            return False

    # Verifica allowlist radice
    first_part = parts[0]
    if len(parts) == 1:
        return first_part in ALLOWED_TOP_FILES
    else:
        return first_part in ALLOWED_TOP_DIRS


def create_sanitized_zip():
    print("=" * 80)
    print("CREAZIONE PACCHETTO DI RILASCIO TESI SANITIZZATO E VERIFICATO")
    print(f"Destinazione: {OUTPUT_ZIP_PATH}")
    print("=" * 80)

    if not DESKTOP_DIR.exists():
        DESKTOP_DIR.mkdir(parents=True, exist_ok=True)

    files_to_pack: list[tuple[Path, str]] = []
    skipped_count = 0

    for root, dirs, files in os.walk(REPO_ROOT):
        # Filtra directory vietate in-place
        dirs[:] = [d for d in dirs if d not in FORBIDDEN_DIR_NAMES and not d.startswith(".venv")]

        for file in files:
            abs_p = Path(root) / file
            rel_p = abs_p.relative_to(REPO_ROOT)

            if is_file_allowed(rel_p):
                # Percorso nel zip con prefisso cartella progetto pulita
                arcname = f"ChatAnalysis/{rel_p.as_posix()}"
                files_to_pack.append((abs_p, arcname))
            else:
                skipped_count += 1

    print(f"[*] File ammessi nella allowlist: {len(files_to_pack)}")
    print(f"[*] File e artefatti privati/binari esclusi: {skipped_count}")

    # Scrittura archivio ZIP
    if OUTPUT_ZIP_PATH.exists():
        OUTPUT_ZIP_PATH.unlink()

    with zipfile.ZipFile(OUTPUT_ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for abs_p, arcname in files_to_pack:
            zf.write(abs_p, arcname=arcname)

    zip_size = OUTPUT_ZIP_PATH.stat().st_size
    print(f"[OK] Archivio ZIP creato: {zip_size:,} byte ({zip_size / (1024*1024):.2f} MB)")

    # Calcolo SHA-256 e CRC32
    sha256_hash = hashlib.sha256()
    crc32_val = 0
    with open(OUTPUT_ZIP_PATH, "rb") as f:
        while chunk := f.read(65536):
            sha256_hash.update(chunk)
            crc32_val = zlib.crc32(chunk, crc32_val)

    hex_sha = sha256_hash.hexdigest()
    hex_crc = f"{crc32_val & 0xFFFFFFFF:08x}"

    print(f"[OK] SHA-256: {hex_sha}")
    print(f"[OK] CRC-32:  {hex_crc}")

    # Scrittura manifest di integrità
    manifest_content = (
        f"Archive:   {ZIP_NAME}\n"
        f"Size:      {zip_size} bytes\n"
        f"SHA-256:   {hex_sha}\n"
        f"CRC-32:    {hex_crc}\n"
        f"Timestamp: {datetime.now(timezone.utc).isoformat()}\n"
        f"Total Files: {len(files_to_pack)}\n"
    )
    CHECKSUM_FILE_PATH.write_text(manifest_content, encoding="utf-8")
    print(f"[OK] Manifest salvato in: {CHECKSUM_FILE_PATH}")

    # Ispezione forense del pacchetto generato (nomi file, CRC e contenuto byte-per-byte)
    print("\n[*] Ispezione di sicurezza e integrità del contenuto ZIP:")
    leaks = []
    forbidden_byte_markers = [
        b"SQLite format 3\x00",
        b"Chat WhatsApp con " + b"Gre",
        b"check_gre_chat.py",
        b"inspect_gre_chat_diagnostics.py",
    ]
    with zipfile.ZipFile(OUTPUT_ZIP_PATH, "r") as zf:
        bad_crc = zf.testzip()
        if bad_crc is not None:
            raise RuntimeError(f"ERRORE CRC NELLO ZIP: {bad_crc}")
        infolist = zf.infolist()
        for info in infolist:
            name_lower = info.filename.lower()
            if any(
                forbidden in name_lower
                for forbidden in [
                    ".db", ".sqlite", "-wal", "-shm", ".disabled",
                    "raw_completions", "reference_chat_document.zip",
                    "build_reconstructed_checkpoint.py", ".git/",
                ]
            ):
                leaks.append(f"path:{info.filename}")
                continue
            data = zf.read(info.filename)
            if not info.filename.endswith("create_sanitized_final_zip.py"):
                for marker in forbidden_byte_markers:
                    if marker in data:
                        leaks.append(f"content({marker.decode('ascii', errors='ignore')}):{info.filename}")

    if leaks:
        OUTPUT_ZIP_PATH.unlink()
        raise RuntimeError(f"VIOLAZIONE DI PRIVACY O CONTENUTO NON AMMESSO: {leaks}")

    print(f"[OK] 100% Verificato: CRC=OK, ZERO file sensibili, ZERO database, ZERO perdite di privacy ({len(infolist)} file totali conformi).")
    return OUTPUT_ZIP_PATH, hex_sha, hex_crc


if __name__ == "__main__":
    create_sanitized_zip()
