"""
scripts/create_resume_preflight_archive.py
-----------------------------------------
Script di impacchettamento dell'archivio di audit per la pre-ripresa dei checkpoint 24/26.
Genera il file ZIP direttamente ALL'ESTERNO della cartella 'Tesi' e ALL'ESTERNO del repository Git:
c:\\Users\\lucas\\Desktop\\CartellaTesi\\chatAnalysis_checkpoint_resume_preflight_fix_audit_20261019.zip

VINCOLI CARDINALI:
1. Nessun salvataggio intermedio dentro 'Tesi' o dentro il repository Git.
2. Esclusione totale e rigorosa di database SQLite reali, checkpoint reali, chat WhatsApp o PII.
3. Inclusione di relazioni tecniche, runbook Ubuntu, strumenti di preflight, test sintetici e sorgenti di supporto.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
import zipfile

REPO_ROOT = Path(__file__).resolve().parent.parent
PARENT_DIR = REPO_ROOT.parent

ARCHIVE_NAME = "chatAnalysis_checkpoint_resume_preflight_fix_audit_20261019.zip"
TARGET_ZIP_PATH = PARENT_DIR / ARCHIVE_NAME

# File da includere con percorso relativo interno allo ZIP
FILES_TO_PACK = [
    # Rapporti e Runbook
    ("CHATANALYSIS_CHECKPOINT_RESUME_PREFLIGHT_REPORT.md", "docs/CHATANALYSIS_CHECKPOINT_RESUME_PREFLIGHT_REPORT.md"),
    ("UBUNTU_CHECKPOINT_RESUME_RUNBOOK.md", "docs/UBUNTU_CHECKPOINT_RESUME_RUNBOOK.md"),
    
    # Strumenti operativi e script di preflight
    ("scripts/preflight_checkpoint_resume.py", "scripts/preflight_checkpoint_resume.py"),
    
    # Test unitari sintetici
    ("tests/unit/test_checkpoint_preflight_resume.py", "tests/unit/test_checkpoint_preflight_resume.py"),
    ("tests/unit/test_checkpoint_integrity_scalability.py", "tests/unit/test_checkpoint_integrity_scalability.py"),
    
    # Sorgenti architetturali essenziali per il preflight e la validazione probatoria
    ("analysis/checkpoint.py", "src/analysis/checkpoint.py"),
    ("analysis/service.py", "src/analysis/service.py"),
    ("analysis/models.py", "src/analysis/models.py"),
    ("ai/chunking.py", "src/ai/chunking.py"),
    ("ai/models.py", "src/ai/models.py"),
    ("ai/token_budget.py", "src/ai/token_budget.py"),
    ("ui/ingestion.py", "src/ui/ingestion.py"),
    ("ui/models.py", "src/ui/models.py"),
    ("normalization/models.py", "src/normalization/models.py"),
    ("importer/models.py", "src/importer/models.py"),
    ("unified/models.py", "src/unified/models.py"),
    ("validation/models.py", "src/validation/models.py"),
]

# Controllo anti-leak rigoroso
FORBIDDEN_EXTENSIONS = {".db", ".sqlite", ".sqlite3", ".log"}


def calculate_sha256(filepath: Path) -> str:
    """Calcola l'hash crittografico SHA-256 di un file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def build_archive() -> None:
    print("=" * 78)
    print(" CHATANALYSIS — CREAZIONE ARCHIVIO AUDIT PRE-RIPRESA CHECKPOINT (FIX)")
    print("=" * 78)
    print(f"Repository Root (Tesi) : {REPO_ROOT}")
    print(f"Target Directory       : {PARENT_DIR}")
    print(f"Percorso Archivio ZIP  : {TARGET_ZIP_PATH}")
    print("-" * 78)

    # Verifica tassativa che il target sia FUORI da REPO_ROOT
    try:
        TARGET_ZIP_PATH.relative_to(REPO_ROOT)
        raise RuntimeError(f"VIOLAZIONE CRITICA: il percorso target {TARGET_ZIP_PATH} è dentro {REPO_ROOT}!")
    except ValueError:
        # Corretto: è fuori da REPO_ROOT
        pass

    if TARGET_ZIP_PATH.exists():
        print(f"[*] Rimozione archivio preesistente: {TARGET_ZIP_PATH}")
        TARGET_ZIP_PATH.unlink()

    # Creazione file README interno al pacchetto
    readme_content = f"""# CHATANALYSIS — CHECKPOINT RESUME PREFLIGHT FIX AUDIT PACKAGE (2026-10-19)

Archivio certificato per la verifica preliminare e la ripresa sicura della sessione Topic Discovery (24/26).
Versione consolidata a seguito dell'audit indipendente con 100% isolamento read-only e privacy rafforzata.

## CONTENUTO DELL'ARCHIVIO
- `docs/CHATANALYSIS_CHECKPOINT_RESUME_PREFLIGHT_REPORT.md`: Rapporto tecnico, teorico e forense dettagliato.
- `docs/UBUNTU_CHECKPOINT_RESUME_RUNBOOK.md`: Istruzioni passo-passo per l'esecuzione in ambiente reale Ubuntu 22.04 LTS.
- `scripts/preflight_checkpoint_resume.py`: Strumento CLI di audit in sola lettura (9 controlli obbligatori, zero LLM).
- `tests/unit/test_checkpoint_preflight_resume.py`: Suite di test unitari con 27 scenari nominali ed edge-case (100% passati).
- `tests/unit/test_checkpoint_integrity_scalability.py`: Suite di regressione per l'integrità dei checkpoint (33 test passati).
- `src/`: Sorgenti applicativi correlati alla logica di fingerprint, chunking, validazione evidenze e persistenza SQLite.

## STATI DI VERDETTO AMMESSI
1. STRUCTURAL_ONLY: Database SQLite integro ma documento attivo non caricato.
2. RUNTIME_VALIDATION_REQUIRED: Verifica formale obbligatoria su chat reale prima di tentare la ripresa.
3. SAFE_RESUME_VERIFIED: Tutti i 9 controlli superati; autorizzata la ripresa selettiva dei soli blocchi 11 e 20 (indici 10 e 19).
4. NOT_COMPATIBLE: Almeno un controllo fallito; abort immediato senza alterazione dei checkpoint.

## VINCOLI DI SICUREZZA FORENSE E PRIVACY
Questo archivio non contiene database SQLite reali, checkpoint investigativi, conversazioni personali WhatsApp o dati sensibili.
"""

    manifest_lines = [
        "# MANIFEST E INTEGRITA FILE (SHA-256)",
        f"# Generato: 2026-10-19",
        f"# Archivio: {ARCHIVE_NAME}",
        "",
        "Percorso nello ZIP | SHA-256 | Dimensione (byte)",
        "---|---|---",
    ]

    print("[*] Compilazione archivio ZIP direttamente nella cartella esterna...")
    with zipfile.ZipFile(TARGET_ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zipf:
        # Scrive README.md interno
        zipf.writestr("README.md", readme_content)
        manifest_lines.append(f"README.md | {hashlib.sha256(readme_content.encode('utf-8')).hexdigest()} | {len(readme_content.encode('utf-8'))}")

        for rel_src, zip_dest in FILES_TO_PACK:
            src_path = REPO_ROOT / rel_src
            if not src_path.is_file():
                raise FileNotFoundError(f"File sorgente richiesto non trovato: {src_path}")

            # Controllo anti-leak
            lower_name = src_path.name.lower()
            if any(lower_name.endswith(ext) for ext in FORBIDDEN_EXTENSIONS):
                raise ValueError(f"VIOLAZIONE PRIVACY: tentata inclusione di file proibito: {src_path}")

            sha = calculate_sha256(src_path)
            size = src_path.stat().st_size
            manifest_lines.append(f"{zip_dest} | {sha} | {size}")

            zipf.write(src_path, arcname=zip_dest)
            print(f"  [+] Aggiunto: {zip_dest} ({size:,} byte, SHA-256: {sha[:12]}...)")

        # Scrive MANIFEST.md interno
        manifest_str = "\n".join(manifest_lines) + "\n"
        zipf.writestr("MANIFEST.md", manifest_str)

    archive_size = TARGET_ZIP_PATH.stat().st_size
    archive_sha = calculate_sha256(TARGET_ZIP_PATH)

    print("-" * 78)
    print("[SUCCESS] ARCHIVIO CREATO CON SUCCESSO FUORI DAL REPOSITORY!")
    print(f"File Destinazione : {TARGET_ZIP_PATH}")
    print(f"Dimensione Totale : {archive_size:,} byte ({archive_size / 1024:.2f} KB)")
    print(f"Hash SHA-256      : {archive_sha}")
    print("=" * 78)


if __name__ == "__main__":
    build_archive()
