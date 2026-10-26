#!/usr/bin/env python3
"""
scripts/create_engine_recovery_windows_archive.py
-------------------------------------------------
Genera l'archivio ZIP formale e completo di rilascio per il ripristino, stabilizzazione
e validazione live del motore locale ChatAnalysis su Windows 11:
`chatAnalysis_engine_recovery_windows_20261026.zip`

REQUISITI DI CONSEGNA E INTEGRITÀ:
1. Destinazione: `C:\\Users\\Luca\\Desktop\\chatAnalysis_engine_recovery_windows_20261026.zip`.
2. Esclude rigorosamente: .git/, .venv/, .venv312/, build/, __pycache__/, *.pyc,
   database o checkpoint SQLite locali con chat reali (.checkpoints/, checkpoints_archive_*, *.db),
   esportazioni private WhatsApp (Chat WhatsApp con Gre.zip), modelli binari GGUF.
3. Genera il manifest crittografico SHA-256 (`manifest_sha256.txt`).
4. Genera il digest esterno `.zip.sha256`.
5. Valida l'integrità CRC con testzip() ed esegue `verify_package_manifest.py`.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import zipfile

ARCHIVE_NAME = "chatAnalysis_engine_recovery_windows_20261026.zip"
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
    cmd = ["git", "ls-files"]
    proc = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True, check=True)
    tracked_files = [line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()]

    additional_files = [
        "docs/CHATANALYSIS_FINAL_ENGINE_RECOVERY_REPORT_20261026.md",
        "scripts/run_real_topic_detection_and_consistency.py",
        "scripts/create_engine_recovery_windows_archive.py",
    ]

    all_files_set = set(tracked_files)
    for af in additional_files:
        if (repo_root / af).is_file():
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
        ".gguf",
    )
    forbidden_names = {
        MANIFEST_NAME,
        ".DS_Store",
        "Thumbs.db",
        "chat-20261021T182727Z-1-001.zip",
        "Chat WhatsApp con Gre.zip",
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
        if p_posix in forbidden_names or Path(p_posix).name in forbidden_names:
            continue
        if (repo_root / rel_path).is_file():
            cleaned_list.append(p_posix)

    return sorted(cleaned_list)


def create_archive_and_manifest(repo_root: Path, target_zip_path: Path) -> dict[str, any]:
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

    # Verifica CRC tramite testzip()
    print("[*] Esecuzione verifica integrità CRC (zipfile.testzip)...")
    with zipfile.ZipFile(target_zip_path, "r") as zf:
        bad_file = zf.testzip()
        if bad_file is not None:
            raise RuntimeError(f"Corruzione rilevata nello ZIP al file: {bad_file}")
    print("[OK] Verifica CRC completata con successo (0 errori).")

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
    desktop_dir = Path("C:/Users/Luca/Desktop")
    target_zip = desktop_dir / ARCHIVE_NAME

    print("=" * 80)
    print("CHATANALYSIS — CREAZIONE ARCHIVIO DI RELEASE ENGINE RECOVERY")
    print("=" * 80)
    print(f"Repo root:       {repo_root}")
    print(f"Destinazione:    {target_zip}")

    info = create_archive_and_manifest(repo_root, target_zip)

    # Validazione formale con scripts/verify_package_manifest.py
    print("\n" + "=" * 80)
    print("AVVIO VERIFICA CRITTOGRAFICA E STRUTTURALE DEL PACCHETTO...")
    print("=" * 80)

    validator_script = repo_root / "scripts" / "verify_package_manifest.py"
    if validator_script.is_file():
        proc = subprocess.run([sys.executable, str(validator_script), str(target_zip)], capture_output=True, text=True)
        print(proc.stdout)
        if proc.stderr:
            print(proc.stderr, file=sys.stderr)
        if proc.returncode != 0:
            print(f"[ERROR] Validazione manifest fallita con codice {proc.returncode}!", file=sys.stderr)
            return proc.returncode
        print("[OK] Validazione manifest superata con successo!")

    print("\n" + "=" * 80)
    print("RIEPILOGO FINALE CONSEGNA:")
    print("=" * 80)
    print(f"File:    {info['zip_path']}")
    print(f"Size:    {info['zip_size']:,} bytes")
    print(f"Files:   {info['total_files']} file tracciati")
    print(f"SHA-256: {info['zip_sha256']}")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
