#!/usr/bin/env python3
"""
scripts/create_microchunk_fix_release_archive.py
-----------------------------------------------------------
Genera l'archivio ZIP sanificato di rilascio per la correzione della Discovery:
`C:\\Users\\Luca\\Desktop\\CHATANALYSIS_MICROCHUNK_DISCOVERY_FIX_20261027.zip`

REQUISITI DI INTEGRITÀ E BONIFICA FORENSE:
1. Destinazione: Desktop utente (C:\\Users\\Luca\\Desktop).
2. Esclusione TASSATIVA di dati privati e conversazioni reali:
   - Nessun file contenente chat WhatsApp reali (esclusi test_data/reference_chat_document.zip, Chat WhatsApp con Gre.zip).
   - Nessun archivio ZIP annidato contenente log, dump, benchmark raw o chat.
   - Nessun database SQLite, checkpoint o file temporaneo (*.db, *.db-wal, *.db-shm, .checkpoints/).
   - Nessuna directory di runtime o cache (.git/, .venv/, .venv312/, __pycache__/, *.pyc, .pytest_cache/).
3. Verifica ricorsiva di tutti gli archivi inclusi (ispezione interna dei file).
4. Inclusione del rapporto finale:
   `docs/CHATANALYSIS_MICROCHUNK_DISCOVERY_FIX_20261027.md`.
5. Generazione di manifest_sha256.txt interno e digest .zip.sha256 esterno.
6. Verifica di integrità CRC zipfile.testzip().
"""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import zipfile

ARCHIVE_BASENAME = "CHATANALYSIS_MICROCHUNK_DISCOVERY_FIX_20261027"
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


def inspect_nested_zip_for_privacy_violations(zip_bytes: bytes, zip_name: str) -> list[str]:
    """Ispeziona ricorsivamente il contenuto di un archivio ZIP per rilevare file privati o non ammessi."""
    violations = []
    forbidden_terms = ["gre", "whatsapp", "chat", "msgstore", ".db", "transcript", "log", ".wal"]
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            for item in z.infolist():
                fn_lower = item.filename.lower()
                for term in forbidden_terms:
                    if term in fn_lower and not fn_lower.endswith(".py"):
                        violations.append(f"{zip_name} -> {item.filename} (contiene termine riservato '{term}')")
    except Exception as e:
        violations.append(f"{zip_name} (errore ispezione archivio: {e})")
    return violations


def get_repo_files_to_pack(repo_root: Path) -> list[str]:
    # Elenco file tracciati da git
    cmd = ["git", "ls-files"]
    proc = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True, check=True)
    tracked_files = [line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()]

    # File addizionali autorizzati creati durante l'intervento
    additional_files = [
        "docs/CHATANALYSIS_MICROCHUNK_DISCOVERY_FIX_20261027.md",
        "scripts/test_real_microchunk_discovery.py",
        "scripts/create_microchunk_fix_release_archive.py",
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
        "checkpoints_",
        ".checkpoints_test_live/",
        "output/real_chunks_diagnostic/",
        "output/pytest_temp/",
        "output/isolated_microchunk_test_db/",
        "output/live_audit_test_payloads/",
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
    explicit_forbidden_files = {
        "test_data/reference_chat_document.zip",
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
        if "gre" in norm.lower() and not norm.endswith(".py"):
            continue
        p = repo_root / norm
        if not p.is_file():
            continue

        # Ispezione ricorsiva di eventuali archivi .zip ammessi
        if norm.endswith(".zip"):
            raw_z = p.read_bytes()
            violations = inspect_nested_zip_for_privacy_violations(raw_z, norm)
            if violations:
                print(f"[BONIFICA] Archivio escluso per violazione forense/privacy: {norm}")
                for v in violations:
                    print(f"   -> {v}")
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

    # Creazione archivio ZIP compresso
    output_zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr(MANIFEST_NAME, manifest_bytes)
        for rel_path in sorted(files_to_pack):
            full_path = repo_root / rel_path
            zf.write(full_path, arcname=rel_path)

    # Verifica integrità CRC
    with zipfile.ZipFile(output_zip_path, "r") as zf:
        bad_file = zf.testzip()
        if bad_file:
            raise RuntimeError(f"Errore CRC durante la verifica dell'archivio: {bad_file}")

    # Calcolo digest esterno SHA-256
    zip_sha256 = compute_file_sha256(output_zip_path)
    sha256_path = output_zip_path.with_name(f"{output_zip_path.name}.sha256")
    sha256_path.write_text(f"{zip_sha256}  {output_zip_path.name}\n", encoding="utf-8")

    print(f"[OK] Archivio ZIP creato con successo: {output_zip_path}")
    print(f"[OK] Digest SHA-256: {zip_sha256}")
    print(f"[OK] File digest esterno: {sha256_path}")
    print(f"[OK] Verifica integrità CRC superata (zero errori).")

    return output_zip_path, sha256_path, zip_sha256


if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parent.parent
    desktop_dir = Path("C:/Users/Luca/Desktop")
    if not desktop_dir.exists():
        desktop_dir = repo_root / "output"
    out_zip = desktop_dir / ARCHIVE_NAME
    create_sanitized_archive(repo_root, out_zip)
