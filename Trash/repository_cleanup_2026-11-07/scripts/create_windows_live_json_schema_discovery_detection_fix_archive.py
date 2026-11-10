#!/usr/bin/env python3
"""
scripts/create_windows_live_json_schema_discovery_detection_fix_archive.py
--------------------------------------------------------------------------
Genera l'archivio ZIP formale e completo di rilascio per la diagnosi e risoluzione
dell'incidente LIVE Windows Topic Detection e Discovery su LM Studio:
`chatAnalysis_windows_live_json_schema_discovery_detection_fix_verified_20261025.zip`

REQUISITI DI CONSEGNA E INTEGRITÀ:
1. Creazione diretta all'ESTERNO del repository Git e all'ESTERNO della cartella 'Tesi':
   destinazione: `C:\\Users\\Luca\\Desktop\\chatAnalysis_windows_live_json_schema_discovery_detection_fix_verified_20261025.zip`.
2. Nessuna creazione temporanea all'interno del repository o dentro 'Tesi'.
3. Include tutti i sorgenti del repository (git ls-files), i moduli corretti
   (ai/lmstudio.py, ai/topics.py, analysis/models.py, analysis/service.py, tests/unit/*),
   i nuovi script diagnostici e di validazione live, la suite di test mirata,
   e la documentazione tecnica obbligatoria:
   `CHATANALYSIS_LIVE_WINDOWS_JSON_DISCOVERY_DETECTION_REPORT_20261025.md`.
4. Esclude tassativamente: .git/, .venv/, .venv312/, build/, __pycache__/, *.pyc,
   database o checkpoint SQLite locali con chat reali, file temporanei e segreti.
5. Genera il manifest crittografico SHA-256 (`manifest_sha256.txt`) calcolato sui byte
   effettivi inclusi nello ZIP senza autoriferimenti circolari.
6. Genera il file di digest esterno `.sha256`.
7. Esegue automaticamente la verifica post-creazione tramite `scripts/verify_package_manifest.py`.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import zipfile

ARCHIVE_NAME = "chatAnalysis_windows_live_json_schema_discovery_detection_fix_verified_20261025.zip"
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
    """
    Raccoglie la lista completa dei file da includere nell'archivio.
    Utilizza 'git ls-files' come base e include i nuovi artefatti verificati.
    """
    cmd = ["git", "ls-files"]
    proc = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True, check=True)
    tracked_files = [line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()]

    # Aggiungi nuovi file esplicitamente creati per questa consegna
    additional_files = [
        "CHATANALYSIS_LIVE_WINDOWS_JSON_DISCOVERY_DETECTION_REPORT_20261025.md",
        "CHATANALYSIS_WINDOWS_THREE_PYTEST_FAILURES_RECONCILED_20261025_REPORT.md",
        "scripts/diagnose_lmstudio_json_capabilities.py",
        "scripts/run_live_lmstudio_smoke_and_validation.py",
        "scripts/run_live_windows_complete_verification.py",
        "scripts/create_windows_live_json_schema_discovery_detection_fix_archive.py",
        "scripts/create_windows_three_pytest_failures_reconciled_archive.py",
        "tests/unit/test_live_windows_json_capabilities_and_fixes.py",
    ]

    all_files_set = set(tracked_files)
    for af in additional_files:
        if (repo_root / af).is_file():
            all_files_set.add(af.replace("\\", "/"))

    # Regole di esclusione di sicurezza
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
        ".checkpoints_test_live/",
    )
    forbidden_suffixes = (
        ".pyc",
        ".pyo",
        ".tmp",
        ".swp",
        "~",
        ".db",
        ".sqlite",
        ".sqlite3",
        ".wal",
        ".shm",
        ".log",
    )
    forbidden_names = {
        MANIFEST_NAME,
        ".DS_Store",
        "Thumbs.db",
        "chat-20261021T182727Z-1-001.zip",
    }

    cleaned_list: list[str] = []
    for rel_path in sorted(all_files_set):
        p_posix = rel_path.replace("\\", "/")
        if any(p_posix.startswith(fp) for fp in forbidden_prefixes):
            continue
        if any(p_posix.endswith(fs) for fs in forbidden_suffixes):
            continue
        if "/__pycache__/" in f"/{p_posix}/" or p_posix.startswith("__pycache__/"):
            continue
        if p_posix in forbidden_names:
            continue
        if (repo_root / rel_path).is_file():
            cleaned_list.append(p_posix)

    return sorted(cleaned_list)


def create_archive_and_manifest(repo_root: Path, target_zip_path: Path) -> dict[str, any]:
    """
    Costruisce lo ZIP direttamente al percorso target esterno e inserisce il manifest SHA-256.
    """
    target_zip_path = target_zip_path.resolve()
    target_dir = target_zip_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)

    if target_zip_path.is_file():
        target_zip_path.unlink()

    files_to_pack = get_repo_files_to_pack(repo_root)
    print(f"[*] File totali da includere nell'archivio: {len(files_to_pack)}")

    manifest_records: dict[str, str] = {}

    print(f"[*] Creazione archivio ZIP: {target_zip_path}")
    with zipfile.ZipFile(target_zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for rel_path in files_to_pack:
            abs_path = repo_root / rel_path
            with open(abs_path, "rb") as f:
                content = f.read()
            digest = compute_sha256_bytes(content)
            manifest_records[rel_path] = digest
            zf.writestr(rel_path, content)

        # Generazione manifest_sha256.txt
        manifest_lines = [
            "# ChatAnalysis Package Manifest",
            f"# Archive: {target_zip_path.name}",
            f"# Total Files: {len(manifest_records)}",
            "# Algorithm: SHA-256",
            "# Format: <digest>  <posix_rel_path>",
        ]
        for p in sorted(manifest_records.keys()):
            manifest_lines.append(f"{manifest_records[p]}  {p}")

        manifest_content = ("\n".join(manifest_lines) + "\n").encode("utf-8")
        zf.writestr(MANIFEST_NAME, manifest_content)

    print(f"[OK] Archivio ZIP creato: {target_zip_path} ({target_zip_path.stat().st_size:,} bytes)")

    # Calcolo SHA-256 dell'archivio ZIP stesso
    zip_digest = compute_file_sha256(target_zip_path)
    checksum_file = target_zip_path.with_suffix(".zip.sha256")
    checksum_file.write_text(f"{zip_digest}  {target_zip_path.name}\n", encoding="utf-8")
    print(f"[OK] Digest SHA-256 dell'archivio generato: {checksum_file}")
    print(f"     {zip_digest}  {target_zip_path.name}")

    return {
        "zip_path": str(target_zip_path),
        "zip_size": target_zip_path.stat().st_size,
        "zip_sha256": zip_digest,
        "total_files": len(manifest_records),
    }


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    grandparent_dir = repo_root.parent.parent
    target_zip = grandparent_dir / ARCHIVE_NAME

    print("=" * 80)
    print("CHATANALYSIS — CREAZIONE ARCHIVIO DI CONSEGNA WINDOWS LIVE JSON FIX")
    print("=" * 80)
    print(f"Repo root:       {repo_root}")
    print(f"Destinazione:    {target_zip}")

    info = create_archive_and_manifest(repo_root, target_zip)

    # Validazione automatizzata con scripts/verify_package_manifest.py
    print("\n" + "=" * 80)
    print("AVVIO VERIFICA CRITTOGRAFICA E STRUTTURALE DEL PACCHETTO...")
    print("=" * 80)

    sys.path.insert(0, str(repo_root))
    from scripts.verify_package_manifest import verify_zip_manifest

    is_valid, report = verify_zip_manifest(target_zip)

    print(f"\n[RISULTATO VERIFICA MANIFEST]")
    print(f"  * Status:           {report.get('status')}")
    print(f"  * File manifest:    {report.get('manifest_entries')}")
    print(f"  * Hash corretti:    {report.get('correct_hashes')}")
    print(f"  * Mismatch hash:    {report.get('mismatched_hashes')}")
    print(f"  * File non listati: {report.get('unlisted_files_in_manifest')}")
    print(f"  * Compileall test:  {report.get('compileall_passed')}")
    print(f"  * Pytest F1 test:   {report.get('pytest_passed')}")
    print(f"  * Dettaglio errori: {report.get('errors')}")

    if is_valid:
        print("\n[OK] VERIFICA COMPLETATA CON SUCCESSO: ARCHIVIO VALIDO E INTEGRO.")
        return 0
    else:
        print("\n[FAIL] VERIFICA FALLITA: DISCREPANZE RILEVATE.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
