#!/usr/bin/env python3
"""
scripts/create_real_discovery_validation_release_archive.py
-----------------------------------------------------------
Genera l'archivio ZIP sanificato di rilascio per la validazione della Discovery:
`C:\\Users\\Luca\\Desktop\\CHATANALYSIS_REAL_DISCOVERY_VALIDATION_20261027.zip`

REQUISITI DI INTEGRITÀ E BONIFICA:
1. Destinazione: Desktop utente.
2. Esclusione tassativa di:
   - Database SQLite e checkpoint (.checkpoints/, checkpoints_*, *.db, *.db-wal, *.db-shm).
   - Export contenenti testi reali o citazioni private (output/*26of26*).
   - Archivi WhatsApp privati non anonimi (Chat WhatsApp con Gre.zip).
   - Directory di ambiente e cache (.git/, .venv/, .venv312/, __pycache__/, *.pyc).
3. Inclusione del rapporto di validazione:
   `docs/CHATANALYSIS_REAL_DISCOVERY_VALIDATION_20261027.md`.
4. Inclusione del codice sorgente aggiornato, script di verifica live e test di attestazione.
5. Generazione di manifest_sha256.txt e digest esterno .zip.sha256.
6. Verifica di integrità CRC zipfile.testzip().
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import zipfile

ARCHIVE_BASENAME = "CHATANALYSIS_REAL_DISCOVERY_VALIDATION_20261027"
ARCHIVE_NAME = f"{ARCHIVE_BASENAME}.zip"
MANIFEST_NAME = "manifest_sha256.txt"


def compute_sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_file_sha256(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def get_repo_files_to_pack(repo_root: Path) -> list[str]:
    # Elenco file tracciati da git
    cmd = ["git", "ls-files"]
    proc = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True, check=True)
    tracked_files = [line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()]

    # File addizionali creati durante la sessione
    additional_files = [
        "docs/CHATANALYSIS_REAL_DISCOVERY_VALIDATION_20261027.md",
        "scripts/archive_disabled_restore_authentic_discovery_26of26.py.disabled",
        "scripts/verify_live_discovery_provenance.py",
        "scripts/create_real_discovery_validation_release_archive.py",
        "tests/unit/test_export_attestation_provenance.py",
        "output/live_audit_test_payloads/synthetic/chunk_0_raw_http_response.json",
        "output/live_audit_test_payloads/synthetic/chunk_1_raw_http_response.json",
        "output/live_audit_test_payloads/synthetic/chunk_2_raw_http_response.json",
        "output/live_audit_test_payloads/synthetic/live_discovery_verification_summary.json",
        "test_data/reference_chat_document.zip",
    ]

    all_files_set = set(tracked_files)
    for af in additional_files:
        p = repo_root / af
        if p.is_file():
            all_files_set.add(af.replace("\\", "/"))

    forbidden_prefixes = (
        ".git/",
        ".venv/",
        ".venv312/",
        "build/",
        ".pytest_cache/",
        "whatsapp_forensic_importer.egg-info/",
        "wheels/",
        ".checkpoints/",
        "checkpoints/",
        "checkpoints_archive_",
        ".checkpoints_test_live/",
        "output/real_chunks_diagnostic/",
        "output/pytest_temp/",
    )
    forbidden_suffixes = (
        ".pyc",
        ".pyo",
        ".tmp",
        ".swp",
        "~",
        ".db",
        ".db-wal",
        ".db-shm",
        ".db-journal",
    )
    # File espressamente vietati da escludere per privacy forense
    explicit_forbidden_files = {
        "Chat WhatsApp con Gre.zip",
        "output/discovery_26of26_report.json",
        "output/discovery_26of26_diagnostics.csv",
        "output/detection_26of26_results.json",
        "output/detection_26of26_results.csv",
        "scratch_call_385_request_body.json",
    }

    selected: list[str] = []
    for rel_str in sorted(all_files_set):
        norm = rel_str.replace("\\", "/")
        if any(norm.startswith(p) for p in forbidden_prefixes):
            continue
        if any(norm.endswith(s) for s in forbidden_suffixes):
            continue
        if Path(norm).name in explicit_forbidden_files or norm in explicit_forbidden_files:
            continue
        p = repo_root / norm
        if not p.is_file():
            continue
        selected.append(norm)

    return selected


def create_sanitized_archive(repo_root: Path, output_zip_path: Path) -> tuple[Path, Path, str]:
    files_to_pack = get_repo_files_to_pack(repo_root)
    print(f"[*] Totale file selezionati per il pacchetto sanificato: {len(files_to_pack)}")

    # Calcola hash dei file per il manifest interno
    manifest_entries: list[tuple[str, str]] = []
    for rel_path in sorted(files_to_pack):
        full_path = repo_root / rel_path
        sha = compute_file_sha256(full_path)
        manifest_entries.append((sha, rel_path))

    # Formatta il manifest interno
    manifest_lines = [f"{sha}  {rel_path}" for sha, rel_path in manifest_entries]
    manifest_content = "\n".join(manifest_lines) + "\n"
    manifest_bytes = manifest_content.encode("utf-8")

    # Scrittura archivio ZIP
    if output_zip_path.exists():
        output_zip_path.unlink()

    with zipfile.ZipFile(output_zip_path, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        # Inserisci il manifest come primo file
        zf.writestr(MANIFEST_NAME, manifest_bytes)
        for rel_path in files_to_pack:
            full_path = repo_root / rel_path
            zf.write(full_path, arcname=rel_path)

    # Validazione CRC ZIP
    with zipfile.ZipFile(output_zip_path, mode="r") as zf:
        bad_file = zf.testzip()
        if bad_file is not None:
            raise RuntimeError(f"Corruzione CRC riscontrata nel file '{bad_file}' dell'archivio ZIP!")

    zip_sha256 = compute_file_sha256(output_zip_path)
    digest_path = output_zip_path.with_name(f"{output_zip_path.name}.sha256")
    digest_path.write_text(f"{zip_sha256}  {output_zip_path.name}\n", encoding="utf-8")

    return output_zip_path, digest_path, zip_sha256


def main():
    repo_root = Path(r"c:\Users\Luca\Desktop\Tesi\ChatAnalysis").resolve()
    desktop_dir = Path(r"C:\Users\Luca\Desktop").resolve()
    target_zip = desktop_dir / ARCHIVE_NAME

    print("=" * 80)
    print("   CHATANALYSIS — CREAZIONE ARCHIVIO DI RILASCIO SANIFICATO   ")
    print(f"   Destinazione: {target_zip}")
    print("=" * 80)

    zip_path, digest_path, zip_sha = create_sanitized_archive(repo_root, target_zip)

    print(f"[+] Archivio ZIP creato con successo: {zip_path.name} ({zip_path.stat().st_size} bytes)")
    print(f"[+] SHA-256 Archivio: {zip_sha}")
    print(f"[+] File digest esterno: {digest_path.name}")

    # Verifica formale contenuto archivio
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        print(f"[+] Verifica archivio: {len(names)} voci presenti.")
        # Controllo assenza file proibiti
        for name in names:
            assert not name.endswith(".db"), f"File database trovato nell'archivio: {name}"
            assert not name.endswith(".db-wal"), f"File WAL trovato nell'archivio: {name}"
            assert not name.startswith(".checkpoints"), f"Directory checkpoint trovata nell'archivio: {name}"
            assert "Gre" not in name, f"File privato trovato nell'archivio: {name}"
            assert not (name.startswith("output/") and "discovery_26of26" in name), f"Export non sanificato trovato nell'archivio: {name}"
            assert not (name.startswith("output/") and "detection_26of26" in name), f"Export non sanificato trovato nell'archivio: {name}"

        # Controllo presenza rapporto e manifest
        assert MANIFEST_NAME in names, f"{MANIFEST_NAME} mancante!"
        assert "docs/CHATANALYSIS_REAL_DISCOVERY_VALIDATION_20261027.md" in names, "Rapporto di validazione mancante!"
        assert "scripts/verify_live_discovery_provenance.py" in names, "verify_live_discovery_provenance mancante!"
        assert "tests/unit/test_export_attestation_provenance.py" in names, "test_export_attestation_provenance mancante!"

    print("[+] CONTROLLO DI INTEGRITA' E BONIFICA SUPERATO AL 100%.")


if __name__ == "__main__":
    main()
