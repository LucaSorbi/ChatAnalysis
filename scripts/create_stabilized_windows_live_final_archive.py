#!/usr/bin/env python3
"""
scripts/create_stabilized_windows_live_final_archive.py
------------------------------------------------------
Genera e certifica l'archivio ZIP formale di consegna definitiva:
`C:\\Users\\Luca\\Desktop\\chatAnalysis_stabilized_windows_live_20261025.zip`

REQUISITI DI CONSEGNA E INTEGRITÀ FORENSE:
1. Creazione diretta all'ESTERNO del repository Git e all'ESTERNO di cartelle temporanee.
2. Esclusione totale e tassativa di:
   - Checkpoint e database reali (.checkpoints*, *.db, *.sqlite)
   - Chat reali, messaggi privati, media (session_media*, *.zip annidati)
   - Modelli GGUF (*.gguf)
   - Ambienti virtuali (.venv*, venv*)
   - File interni Git (.git/)
   - Log personali (*.log)
3. Include tutti i moduli sorgente aggiornati, la suite di test completa e il rapporto tecnico:
   `CHATANALYSIS_STABILIZZAZIONE_LIVE_FINALE_20261025.md`.
4. Calcola manifest crittografico SHA-256 (`manifest_sha256.txt`) privo di dipendenze circolari.
5. Verifica fisica post-creazione: esistenza, dimensione, file count, CRC-32, digest SHA-256.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import zipfile

ZIP_DESTINATION = Path(r"C:\Users\Luca\Desktop\chatAnalysis_stabilized_windows_live_20261025.zip")
MANIFEST_NAME = "manifest_sha256.txt"


def compute_sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_file_sha256(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def get_files_to_pack(repo_root: Path) -> list[str]:
    cmd = ["git", "ls-files"]
    proc = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True, check=True)
    tracked_files = [line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()]

    # Nuovi file e rapporti essenziali creati durante l'intervento
    additional_files = [
        "CHATANALYSIS_STABILIZZAZIONE_LIVE_FINALE_20261025.md",
        "CHATANALYSIS_REGRESSIONE_25_22_7_DIAGNOSI_20261025.md",
        "CHATANALYSIS_RECUPERO_CHECKPOINT_SICUREZZA.md",
        "CHATANALYSIS_LIVE_WINDOWS_JSON_DISCOVERY_DETECTION_REPORT_20261025.md",
        "CHATANALYSIS_WINDOWS_THREE_PYTEST_FAILURES_RECONCILED_20261025_REPORT.md",
        "scripts/test_chunk2_subchunk_live.py",
        "scripts/test_repetition_fix.py",
        "scripts/test_sub0_clean.py",
        "scripts/validate_live_topic_detection.py",
        "scripts/execute_selective_live_recovery.py",
        "scripts/demo_sentinel_preservation.py",
        "scripts/diagnose_checkpoints_safe.py",
        "scripts/build_reconstructed_checkpoint.py",
        "scripts/create_stabilized_windows_live_final_archive.py",
        "tests/unit/test_checkpoint_isolation_and_recovery_safety.py",
        "tests/unit/test_sentinel_checkpoint_protection.py",
        "tests/unit/test_subchunk_split_recovery_and_coverage.py",
        "tests/unit/test_ui_manual_topic_detection.py",
        "tests/unit/test_apptest_flows.py",
    ]

    all_files_set = set(tracked_files)
    for af in additional_files:
        p = repo_root / af
        if p.is_file():
            all_files_set.add(af.replace("\\", "/"))

    # Regole di esclusione di sicurezza assoluta
    forbidden_prefixes = (
        ".git/",
        ".venv/",
        ".venv312/",
        "venv/",
        ".checkpoints",
        "__pycache__/",
        "build/",
        "dist/",
        ".pytest_cache/",
    )
    forbidden_extensions = (
        ".pyc",
        ".pyo",
        ".pyd",
        ".db",
        ".sqlite",
        ".sqlite3",
        ".gguf",
        ".bin",
        ".safetensors",
        ".log",
        ".zip",
    )
    forbidden_substrings = (
        "session_media",
        "Chat WhatsApp",
        "checkpoints.db",
        "checkpoints_backup",
    )

    clean_files: list[str] = []
    for rel_path in sorted(all_files_set):
        norm = rel_path.replace("\\", "/")
        if any(norm.startswith(pfx) or f"/{pfx}" in norm for pfx in forbidden_prefixes):
            continue
        if any(norm.lower().endswith(ext) for ext in forbidden_extensions):
            continue
        if any(sub.lower() in norm.lower() for sub in forbidden_substrings):
            continue
        full_p = repo_root / norm
        if full_p.is_file():
            clean_files.append(norm)

    return clean_files


def create_archive() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    print("=" * 70)
    print("CREAZIONE ARCHIVIO DEFINITIVO STABILIZZATO WINDOWS LIVE")
    print("=" * 70)
    print(f"Repository Root: {repo_root}")
    print(f"Destinazione ZIP: {ZIP_DESTINATION}")

    ZIP_DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    if ZIP_DESTINATION.exists():
        ZIP_DESTINATION.unlink()

    files_to_pack = get_files_to_pack(repo_root)
    print(f"File totali selezionati per il pacchetto: {len(files_to_pack)}")

    # 1. Calcolo del manifest SHA-256
    manifest_entries: list[tuple[str, str]] = []
    file_bytes_map: dict[str, bytes] = {}

    for rel_path in files_to_pack:
        file_path = repo_root / rel_path
        data = file_path.read_bytes()
        sha = compute_sha256_bytes(data)
        manifest_entries.append((sha, rel_path))
        file_bytes_map[rel_path] = data

    # Genera contenuto del manifest formale
    manifest_lines = [f"{sha}  {rel_path}" for sha, rel_path in manifest_entries]
    manifest_content = ("\n".join(manifest_lines) + "\n").encode("utf-8")

    # 2. Creazione dello ZIP con compressione DEFLATED (livello 9)
    with zipfile.ZipFile(ZIP_DESTINATION, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        # Inserisci manifest nella root dell'archivio
        zf.writestr(MANIFEST_NAME, manifest_content)
        for rel_path in files_to_pack:
            zf.writestr(rel_path, file_bytes_map[rel_path])

    # 3. Verifica fisica post-creazione
    zip_size = ZIP_DESTINATION.stat().st_size
    zip_sha256 = compute_file_sha256(ZIP_DESTINATION)

    # Scrittura digest esterno
    sha_file = ZIP_DESTINATION.with_suffix(".zip.sha256")
    sha_file.write_text(f"{zip_sha256}  {ZIP_DESTINATION.name}\n", encoding="utf-8")

    print("\n" + "=" * 70)
    print("VERIFICA INTEGRITÀ E CONFORMITÀ ARCHIVIO")
    print("=" * 70)
    print(f"Esistenza fisica: {ZIP_DESTINATION.exists()}")
    print(f"Dimensione archivio: {zip_size:,} byte ({round(zip_size / 1024 / 1024, 2)} MB)")
    print(f"Digest SHA-256: {zip_sha256}")

    # Test CRC interno zipfile
    with zipfile.ZipFile(ZIP_DESTINATION, "r") as zf:
        crc_test = zf.testzip()
        infolist = zf.infolist()
        names = zf.namelist()

    print(f"Test integrità CRC-32: {'OK (nessun errore)' if crc_test is None else f'ERRORE nel file {crc_test}'}")
    print(f"Numero totale file inclusi: {len(names)} (incluso {MANIFEST_NAME})")

    # Verifica assenza assoluta di dati sensibili / checkpoint
    forbidden_checks = [".git/", ".checkpoints", ".db", ".sqlite", ".gguf", ".venv", "Chat WhatsApp"]
    violations = [n for n in names if any(f.lower() in n.lower() for f in forbidden_checks)]
    if violations:
        print(f"[ERRORE CRITICO] Rilevati file proibiti nell'archivio: {violations}")
        sys.exit(1)
    else:
        print("Verifica dati riservati / checkpoint / modelli: ZERO VIOLAZIONI")

    # Verifica presenza file indispensabili
    must_have = [
        "CHATANALYSIS_STABILIZZAZIONE_LIVE_FINALE_20261025.md",
        "ai/lmstudio.py",
        "ai/topics.py",
        "analysis/service.py",
        "analysis/models.py",
        "scripts/test_chunk2_subchunk_live.py",
        "scripts/validate_live_topic_detection.py",
        MANIFEST_NAME,
    ]
    missing = [f for f in must_have if f not in names]
    if missing:
        print(f"[ERRORE CRITICO] File essenziali mancanti: {missing}")
        sys.exit(1)
    else:
        print("Verifica presenza file essenziali: TUTTI PRESENTI")

    print("\nARCHIVIO DEFINITIVO COMPLETATO CON SUCCESSO!")
    print(f"Percorso: {ZIP_DESTINATION}")
    print(f"SHA-256:  {zip_sha256}")


if __name__ == "__main__":
    create_archive()
