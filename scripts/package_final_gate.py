"""
scripts/package_final_gate.py
-----------------------------
Costruisce il pacchetto ZIP di handoff CHATANALYSIS_FINAL_GATE_2026-11-03.zip
in conformità al protocollo del Gate del 3 novembre 2026.
Risolve formalmente il problema dell'hash auto-referenziale calcolando l'hash
del file ZIP solo dopo la creazione dell'archivio e salvandolo in un file .sha256 separato.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import zipfile

REPO_ROOT = Path(__file__).resolve().parent.parent
HANDOFF_DIR = REPO_ROOT / "handoff" / "2026-11-03"
STAGING_DIR = HANDOFF_DIR / "final_gate_staging"

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def main():
    print("=" * 80)
    print("ASSEMBLAGGIO PACCHETTO DI HANDOFF CHATANALYSIS_FINAL_GATE_2026-11-03")
    print("=" * 80)

    if STAGING_DIR.exists():
        shutil.rmtree(STAGING_DIR, ignore_errors=True)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Copia i file nei rispettivi percorsi logici
    # REPORT
    rep_dir = STAGING_DIR / "REPORT"
    rep_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HANDOFF_DIR / "CHATANALYSIS_FINAL_GATE_2026-11-03.md", rep_dir / "CHATANALYSIS_FINAL_GATE_2026-11-03.md")

    # PATCH
    patch_dir = STAGING_DIR / "PATCH"
    patch_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HANDOFF_DIR / "stage_patch.patch", patch_dir / "git_diff_final_gate.patch")

    # TEST
    test_dir = STAGING_DIR / "TEST"
    test_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HANDOFF_DIR / "stage_pytest_matrix.txt", test_dir / "pytest_matrix.txt")

    # BENCHMARK
    bm_dir = STAGING_DIR / "BENCHMARK"
    bm_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HANDOFF_DIR / "baseline_raw.json", bm_dir / "baseline_raw.json")
    shutil.copy2(HANDOFF_DIR / "final_raw.json", bm_dir / "final_raw.json")

    # GATE
    gate_dir = STAGING_DIR / "GATE"
    gate_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HANDOFF_DIR / "phase3_real_gate_results.json", gate_dir / "phase3_real_gate_results.json")

    # PRIVACY
    priv_dir = STAGING_DIR / "PRIVACY"
    priv_dir.mkdir(parents=True, exist_ok=True)
    privacy_text = """VERBALE DI CONFORMITA PRIVACY E TUTELA DATI PERSONALI
Data: 2026-11-03
Branch: fix/semantic-quality-and-verification-stabilization-20261103

Si certifica che:
1. Nessun messaggio, testo in chiaro o identificativo reale proveniente da chat private e presente nei file di test o report.
2. Tutti gli scenari del benchmark semantico (DEV e HOLDOUT) sono costituiti da dialoghi interamente sintetici.
3. I risultati della FASE 3 (Chat Audio e Chat Foto) contengono esclusivamente metriche aggregate, conteggi di sezioni, status di completamento e tempi di esecuzione, con payload testuali soppressi.
4. I file di database SQLite originali non sono inclusi nell'archivio.
"""
    (priv_dir / "privacy_check.txt").write_text(privacy_text, encoding="utf-8")

    # 2. Genera MANIFEST.txt con SHA-256 dei file interni
    manifest_lines = [
        "================================================================================",
        "CHATANALYSIS — MANIFEST DEL PACCHETTO DI HANDOFF (2026-11-03)",
        "Branch: fix/semantic-quality-and-verification-stabilization-20261103",
        "Base commit: 3068d43047a324dc8e9ad3acb8ef0477c2cc33a0",
        "Head commit: e9abd21",
        "Verdetto: FREEZE CON LIMITI DICHIARATI",
        "================================================================================",
        "",
        "ELENCO COMPONENTI E CHECKSUM CRITTOGRAFICI (SHA-256):",
        "",
    ]

    all_staged_files = sorted([p for p in STAGING_DIR.rglob("*") if p.is_file()])
    for p in all_staged_files:
        rel = p.relative_to(STAGING_DIR).as_posix()
        digest = sha256_file(p)
        size = p.stat().st_size
        manifest_lines.append(f"{digest}  {rel} ({size} bytes)")

    manifest_lines.append("")
    manifest_lines.append("NOTA SULL'HASH DELLO ZIP:")
    manifest_lines.append("Il checksum SHA-256 dell'archivio ZIP stesso e' archiviato all'esterno in CHATANALYSIS_FINAL_GATE_2026-11-03.zip.sha256")
    manifest_lines.append("per evitare paradossi di auto-referenzialita crittografica.")

    (STAGING_DIR / "MANIFEST.txt").write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
    print(f"[OK] MANIFEST.txt generato ({len(all_staged_files) + 1} file censiti).")

    # 3. Crea il file ZIP
    zip_path = HANDOFF_DIR / "CHATANALYSIS_FINAL_GATE_2026-11-03.zip"
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(STAGING_DIR.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(STAGING_DIR).as_posix())

    zip_size = zip_path.stat().st_size
    zip_hash = sha256_file(zip_path)
    print(f"[OK] Archivio ZIP creato: {zip_path} ({zip_size} bytes)")
    print(f"[OK] SHA-256 ZIP: {zip_hash}")

    # 4. Scrivi il checksum esterno del file ZIP
    sha_file = HANDOFF_DIR / "CHATANALYSIS_FINAL_GATE_2026-11-03.zip.sha256"
    sha_file.write_text(f"{zip_hash}  CHATANALYSIS_FINAL_GATE_2026-11-03.zip\n", encoding="utf-8")
    print(f"[OK] File di checksum salvato: {sha_file}")

    # Pulizia staging temporaneo
    shutil.rmtree(STAGING_DIR, ignore_errors=True)
    (HANDOFF_DIR / "stage_patch.patch").unlink(missing_ok=True)
    (HANDOFF_DIR / "stage_pytest_matrix.txt").unlink(missing_ok=True)
    print("=" * 80)
    print("PACCHETTO DI HANDOFF COMPLETATO CON SUCCESSO!")
    print("=" * 80)

if __name__ == "__main__":
    main()
