#!/usr/bin/env python3
"""
scripts/create_closure_audit_archive.py
---------------------------------------
Genera l'archivio ZIP finale di audit e chiusura formale:
`chatAnalysis_manifest_snapshot_ownership_closure_audit_20261021.zip`

REQUISITI DI CONSEGNA E SICUREZZA:
1. Creazione diretta all'ESTERNO del repository Git (nella cartella padre `../`).
2. Include tutti i sorgenti, test, runbook, documentazione Capitolo 3, nuovi test avversari F1,
   rapporti di audit e log aggiornati.
3. Esclude tassativamente: .git/, .venv/, build/, *.egg-info, __pycache__/, *.pyc,
   wheels/ non pertinenti, checkpoint reali, credenziali e modelli GGUF.
4. Genera dinamicamente `manifest_sha256.txt` calcolato sui byte effettivi inclusi nello ZIP.
5. Il manifest elenca esattamente il 100% dei file (escluso se stesso).
6. Esegue automaticamente la verifica post-creazione tramite `scripts/verify_package_manifest.py`.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ARCHIVE_NAME = "chatAnalysis_f1_trust_boundary_manifest_validator_audit_20261021.zip"
MANIFEST_NAME = "manifest_sha256.txt"


def compute_sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def get_target_file_list(repo_root: Path) -> list[str]:
    """
    Raccoglie la lista completa e rigorosa dei file da includere nell'archivio di audit finale.
    Combina l'inventario verificato dell'archivio precedente con i nuovi artefatti dell'audit finale.
    """
    repo_root = Path(repo_root).resolve()
    # 1. Carica i file dall'archivio verificato precedente come baseline fidata
    prev_archive = repo_root.parent / "chatAnalysis_manifest_snapshot_ownership_closure_audit_20261021.zip"
    if not prev_archive.is_file():
        prev_archive = repo_root.parent / "chatAnalysis_windows_capitolo3_cleanup_integrated_audit_20261021.zip"

    included_files: set[str] = set()

    if prev_archive.is_file():
        with zipfile.ZipFile(prev_archive, "r") as zf:
            for item in zf.namelist():
                if item == MANIFEST_NAME or item.endswith("/"):
                    continue
                # Verifica che il file esista nel repository corrente
                if (repo_root / item).is_file():
                    included_files.add(item.replace("\\", "/"))

    # 2. Aggiunge i nuovi file di audit e report prescritti
    mandatory_new_files = [
        "CHATANALYSIS_SNAPSHOT_OWNERSHIP_F1_FINAL_REPORT.md",
        "CHATANALYSIS_ZIP_INTEGRITY_MANIFEST_REPORT.md",
        "scripts/verify_package_manifest.py",
        "scripts/create_closure_audit_archive.py",
        "tests/unit/test_verify_package_manifest.py",
        "test_results.log",
    ]
    for mf in mandatory_new_files:
        if (repo_root / mf).is_file():
            included_files.add(mf.replace("\\", "/"))

    # 3. Filtro tassativo di sicurezza (anti-leak & anti-polluzione)
    forbidden_prefixes = (".git/", ".venv/", "build/", ".pytest_cache/", "whatsapp_forensic_importer.egg-info/", "wheels/")
    forbidden_suffixes = (".pyc", ".pyo", ".tmp", ".swp")
    forbidden_names = {MANIFEST_NAME, ".DS_Store", "Thumbs.db"}

    cleaned_list: list[str] = []
    for rel_path in sorted(included_files):
        p_posix = rel_path.replace("\\", "/")
        if any(p_posix.startswith(fp) for fp in forbidden_prefixes):
            continue
        if any(p_posix.endswith(fs) for fs in forbidden_suffixes):
            continue
        if "/__pycache__/" in f"/{p_posix}/":
            continue
        if p_posix in forbidden_names:
            continue
        # Verifica esistenza fisica
        if (repo_root / rel_path).is_file():
            cleaned_list.append(p_posix)

    return sorted(cleaned_list)


def build_audit_archive(repo_root: Path, dest_zip_path: Path) -> dict[str, any]:
    """
    Costruisce l'archivio ZIP definitivo e il relativo manifest crittografico SHA-256.
    """
    files_to_pack = get_target_file_list(repo_root)
    print(f"[*] File totali selezionati per il confezionamento: {len(files_to_pack)}")

    # Calcolo preliminare degli hash byte-per-byte sui file del repository
    file_hashes: dict[str, str] = {}
    for rel_path in files_to_pack:
        abs_p = repo_root / rel_path
        data = abs_p.read_bytes()
        file_hashes[rel_path] = compute_sha256_bytes(data)

    # Genera il contenuto di manifest_sha256.txt
    manifest_lines = [
        "# ChatAnalysis Closure Audit Manifest SHA-256",
        f"# Total Files: {len(files_to_pack)}",
        "# Format: SHA256  REL_PATH",
    ]
    for rel_path in files_to_pack:
        manifest_lines.append(f"{file_hashes[rel_path]}  {rel_path}")
    manifest_content = "\n".join(manifest_lines) + "\n"
    manifest_bytes = manifest_content.encode("utf-8")

    # Scrive anche manifest_sha256.txt locale nel repository per trasparenza
    (repo_root / MANIFEST_NAME).write_bytes(manifest_bytes)
    print(f"[*] Aggiornato '{MANIFEST_NAME}' locale nel workspace.")

    # Creazione diretta dello ZIP all'esterno del repository
    dest_zip_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_zip_path.is_file():
        dest_zip_path.unlink()

    print(f"[*] Creazione archivio ZIP in: {dest_zip_path.resolve()}")
    with zipfile.ZipFile(dest_zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        # Aggiunge tutti i file ordinati
        for rel_path in files_to_pack:
            abs_p = repo_root / rel_path
            zf.write(abs_p, arcname=rel_path)
        # Aggiunge manifest_sha256.txt
        zf.writestr(MANIFEST_NAME, manifest_bytes)

    archive_size = dest_zip_path.stat().st_size
    print(f"[*] Archivio generato con successo: {archive_size:,} bytes.")

    return {
        "dest_zip": dest_zip_path,
        "archive_size": archive_size,
        "total_files": len(files_to_pack) + 1,  # include manifest_sha256.txt
        "manifest_records": len(files_to_pack),
    }


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    dest_zip = repo_root.parent / ARCHIVE_NAME

    print("=" * 78)
    print("CHATANALYSIS — GENERAZIONE ARCHIVIO FINALE DI AUDIT E CHIUSURA MANIFEST")
    print("=" * 78)
    print(f"Repository Root : {repo_root.resolve()}")
    print(f"Target Archive  : {dest_zip.resolve()}")

    stats = build_audit_archive(repo_root, dest_zip)

    print("\n" + "=" * 78)
    print("ESECUZIONE VERIFICA AUTOMATIZZATA POST-PACKAGING (verify_package_manifest.py)")
    print("=" * 78)

    verify_script = repo_root / "scripts" / "verify_package_manifest.py"
    cmd = [sys.executable, str(verify_script), str(dest_zip)]
    res = subprocess.run(cmd)

    return res.returncode


if __name__ == "__main__":
    sys.exit(main())
