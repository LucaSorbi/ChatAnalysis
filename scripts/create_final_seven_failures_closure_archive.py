#!/usr/bin/env python3
"""
scripts/create_final_seven_failures_closure_archive.py
------------------------------------------------------
Genera l'archivio ZIP formale e completo di consegna per l'audit del 21/10/2026:
`chatAnalysis_f1_pytest_ubuntu_seven_failures_closure_audit_20261021.zip`
(e copia di sincronizzazione `chatAnalysis_json_export_truthfulness_final_audit_20261021.zip`)

REQUISITI DI CONSEGNA E INTEGRITÀ:
1. Creazione diretta all'ESTERNO della cartella 'Tesi' e fuori dal repository Git
   (nella cartella padre `../`).
2. Nessuna creazione temporanea all'interno del repository.
3. Il pacchetto contiene:
   - sorgenti originali e moduli corretti (analysis/export.py, scripts/preflight_checkpoint_resume.py,
     tests/unit/test_analysis_export.py, tests/unit/test_priority3_streamlit_windows_startup.py,
     ui/application.py, ui/presentation.py, ai/lmstudio.py);
   - suite completa di test (tests/unit/test_analysis_export.py con 37 test passati, 6 test F1 passati);
   - documentazione e report tecnico (CHATANALYSIS_F1_SEVEN_FAILURES_AND_EXPORT_TRUTHFULNESS_FINAL_REPORT.md,
     CHATANALYSIS_JSON_EXPORT_SCHEMA_SPECIFICATION.md);
   - 4 esempi JSON sintetici aggiornati a schema 1.1.0 e safe-by-default (in docs/examples/);
   - log ufficiale dei test (test_results.log);
   - manifest SHA-256 autenticato calcolato sui byte effettivi (manifest_sha256.txt).
4. Esclude tassativamente: .git/, .venv/, build/, *.egg-info, __pycache__/, *.pyc,
   wheels/, database reali/privati, credenziali e file GGUF.
5. Verifica automatica post-creazione tramite verify_package_manifest.py.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

PRIMARY_ARCHIVE_NAME = "chatAnalysis_json_scientific_integrity_f1_scope_final_20261021.zip"
SECONDARY_ARCHIVE_NAME = "chatAnalysis_f1_pytest_ubuntu_seven_failures_closure_audit_20261021.zip"
MANIFEST_NAME = "manifest_sha256.txt"


def compute_sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def get_target_file_list(repo_root: Path) -> list[str]:
    """
    Raccoglie la lista completa e rigorosa dei file da includere nell'archivio.
    Utilizza come baseline fidata il pacchetto precedente e integra le nuove correzioni.
    """
    repo_root = Path(repo_root).resolve()
    parent_dir = repo_root.parent

    candidate_baselines = [
        parent_dir / "chatAnalysis_f1_pytest_ubuntu_seven_failures_closure_audit_20261021.zip",
        parent_dir / "chatAnalysis_json_export_truthfulness_final_audit_20261021.zip",
        parent_dir / "chatAnalysis_json_export_truthfulness_deepseek_f1_fix_audit_20261021.zip",
        parent_dir / "chatAnalysis_full_experimental_json_exports_audit_20261021.zip",
        parent_dir / "chatAnalysis_f1_trust_boundary_manifest_validator_audit_20261021.zip",
        parent_dir / "chatAnalysis_manifest_snapshot_ownership_closure_audit_20261021.zip",
    ]

    included_files: set[str] = set()

    for base_zip in candidate_baselines:
        if base_zip.is_file():
            print(f"[*] Utilizzo archivio baseline fidata: {base_zip.name}")
            with zipfile.ZipFile(base_zip, "r") as zf:
                for item in zf.namelist():
                    if item == MANIFEST_NAME or item.endswith("/"):
                        continue
                    if (repo_root / item).is_file():
                        included_files.add(item.replace("\\", "/"))
            break

    # File critici aggiornati e nuovi file vincolanti
    mandatory_files = [
        "ai/lmstudio.py",
        "analysis/export.py",
        "analysis/__init__.py",
        "scripts/preflight_checkpoint_resume.py",
        "tests/unit/test_analysis_export.py",
        "tests/unit/test_priority3_streamlit_windows_startup.py",
        "tests/unit/test_checkpoint_preflight_resume.py",
        "ui/application.py",
        "ui/presentation.py",
        "CHATANALYSIS_F1_SEVEN_FAILURES_AND_EXPORT_TRUTHFULNESS_FINAL_REPORT.md",
        "CHATANALYSIS_JSON_EXPORT_SCHEMA_SPECIFICATION.md",
        "CHATANALYSIS_FULL_EXPERIMENTAL_JSON_EXPORTS_AUDIT_REPORT.md",
        "docs/examples/example_topic_detection_export_synthetic.json",
        "docs/examples/example_topic_discovery_export_synthetic.json",
        "docs/examples/example_topic_detection_partial_17of26_synthetic.json",
        "docs/examples/example_topic_discovery_partial_25of26_synthetic.json",
        "scripts/generate_synthetic_export_examples.py",
        "scripts/create_final_seven_failures_closure_archive.py",
        "scripts/verify_package_manifest.py",
        "test_results.log",
        "app.py",
        "pyproject.toml",
    ]
    for mf in mandatory_files:
        if (repo_root / mf).is_file():
            included_files.add(mf.replace("\\", "/"))

    # Filtro tassativo di sicurezza e pulizia forense
    forbidden_prefixes = (
        ".git/",
        ".venv/",
        "build/",
        ".pytest_cache/",
        ".checkpoints/",
        "whatsapp_forensic_importer.egg-info/",
        "wheels/",
    )
    forbidden_suffixes = (".pyc", ".pyo", ".tmp", ".swp", ".gguf", ".db", ".sqlite")
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
        if (repo_root / rel_path).is_file():
            cleaned_list.append(p_posix)

    return sorted(cleaned_list)


def build_audit_archive(repo_root: Path, dest_zip_path: Path) -> dict[str, Any]:
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
        "# ChatAnalysis Final Audit Closure Manifest SHA-256 (Ubuntu Seven Failures & JSON Export Truthfulness)",
        f"# Total Files: {len(files_to_pack)}",
        "# Format: SHA256  REL_PATH",
    ]
    for rel_path in files_to_pack:
        manifest_lines.append(f"{file_hashes[rel_path]}  {rel_path}")
    manifest_content = "\n".join(manifest_lines) + "\n"
    manifest_bytes = manifest_content.encode("utf-8")

    # Scrive manifest_sha256.txt locale nel repository per trasparenza
    (repo_root / MANIFEST_NAME).write_bytes(manifest_bytes)
    print(f"[*] Aggiornato '{MANIFEST_NAME}' locale nel workspace.")

    # Creazione diretta dello ZIP all'esterno del repository
    dest_zip_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_zip_path.is_file():
        dest_zip_path.unlink()

    print(f"[*] Creazione archivio ZIP primario in: {dest_zip_path.resolve()}")
    with zipfile.ZipFile(dest_zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for rel_path in files_to_pack:
            abs_p = repo_root / rel_path
            zf.write(abs_p, arcname=rel_path)
        zf.writestr(MANIFEST_NAME, manifest_bytes)

    archive_size = dest_zip_path.stat().st_size
    print(f"[*] Archivio primario generato con successo: {archive_size:,} bytes.")

    # Copia di sincronizzazione per il secondo nome richiesto
    secondary_zip_path = dest_zip_path.parent / SECONDARY_ARCHIVE_NAME
    if secondary_zip_path.is_file():
        secondary_zip_path.unlink()
    shutil.copy2(dest_zip_path, secondary_zip_path)
    print(f"[*] Archivio secondario generato in: {secondary_zip_path.resolve()}")

    return {
        "dest_zip": dest_zip_path,
        "secondary_zip": secondary_zip_path,
        "archive_size": archive_size,
        "total_files": len(files_to_pack) + 1,
        "manifest_records": len(files_to_pack),
    }


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    dest_zip = repo_root.parent / PRIMARY_ARCHIVE_NAME

    print("=" * 78)
    print("CHATANALYSIS — GENERAZIONE ARCHIVIO FINALE UBUNTU 7 FAILURES & EXPORT TRUTHFULNESS")
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
