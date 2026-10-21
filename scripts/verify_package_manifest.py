#!/usr/bin/env python3
"""
scripts/verify_package_manifest.py
----------------------------------
Script autonomo, documentato e rigoroso per la verifica dell'integrità crittografica
e strutturale dei pacchetti ZIP di rilascio ChatAnalysis.

REQUISITI DI AUDIT E HARDENING:
1. Rilevazione duplicati: scansione rigorosa di tutti i nomi file nell'archivio ZIP PRIMA
   della costruzione del dizionario, rigettando registrazioni multiple o collisioni normalizzate.
2. Normalizzazione e sicurezza percorsi: percorsi relativi con slash POSIX ('/'),
   rifiuto categorico di percorsi assoluti (Unix o Windows), directory traversal (..) o separatori ambigui.
3. Verifica sintattica degli hash: ogni digest SHA-256 nel manifest deve essere una stringa esadecimale valida di 64 caratteri.
4. Completezza bidirezionale: ogni file nello ZIP (eccetto manifest_sha256.txt) deve essere nel manifest,
   e ogni voce nel manifest deve esistere nello ZIP. Zero file mancanti, zero file non elencati.
5. Integrità crittografica: hash SHA-256 calcolato sui byte effettivi estratti dallo ZIP corrispondente al manifest.
6. Validazione runtime: estrazione temporanea sicura, test compileall ed esecuzione test di regressione F1.

USO:
    python scripts/verify_package_manifest.py [PERCORSO_ZIP]

Se PERCORSO_ZIP non è specificato, cerca automaticamente il pacchetto prescritto nel percorso genitore:
    ../chatAnalysis_f1_trust_boundary_manifest_validator_audit_20261021.zip
    oppure fallback su pacchetti audit recenti.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import zipfile

DEFAULT_ARCHIVE_NAME = "chatAnalysis_f1_trust_boundary_manifest_validator_audit_20261021.zip"
LEGACY_ARCHIVE_NAME = "chatAnalysis_manifest_snapshot_ownership_closure_audit_20261021.zip"

_HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_DRIVE_RE = re.compile(r"^[a-zA-Z]:")


def compute_sha256_bytes(data: bytes) -> str:
    """Calcola il digest SHA-256 esadecimale di un flusso di byte."""
    return hashlib.sha256(data).hexdigest()


def verify_zip_manifest(zip_path: Path, run_tests: bool = True) -> tuple[bool, dict[str, any]]:
    """
    Esegue la verifica autonoma ed esaustiva del manifest crittografico SHA-256
    e della struttura dell'archivio ZIP.
    """
    results: dict[str, any] = {
        "zip_path": str(zip_path.resolve()),
        "archive_size_bytes": 0,
        "total_zip_entries": 0,
        "manifest_entries": 0,
        "correct_hashes": 0,
        "mismatched_hashes": 0,
        "missing_files_in_zip": 0,
        "unlisted_files_in_manifest": 0,
        "duplicate_entries": 0,
        "compileall_passed": False,
        "pytest_passed": False,
        "status": "FAILED",
        "errors": [],
    }

    if not zip_path.is_file():
        results["errors"].append(f"Archivio ZIP non trovato: {zip_path}")
        return False, results

    results["archive_size_bytes"] = zip_path.stat().st_size
    print(f"[*] Apertura archivio ZIP: {zip_path.resolve()} ({results['archive_size_bytes']:,} bytes)")

    with zipfile.ZipFile(zip_path, "r") as zf:
        infolist = zf.infolist()
        file_entries = [info for info in infolist if not info.is_dir()]
        results["total_zip_entries"] = len(file_entries)

        # 1. Rilevazione preliminare di nomi duplicati grezzi all'interno dell'archivio ZIP
        seen_raw_names: set[str] = set()
        for info in file_entries:
            raw_name = info.filename
            if raw_name in seen_raw_names:
                results["duplicate_entries"] += 1
                results["errors"].append(f"Nome file grezzo duplicato nell'archivio ZIP: '{raw_name}'")
            seen_raw_names.add(raw_name)

        # 2. Verifica presenza del manifest all'interno dell'archivio
        manifest_info = None
        for info in file_entries:
            if info.filename.replace("\\", "/") == "manifest_sha256.txt":
                manifest_info = info
                break

        if not manifest_info:
            results["errors"].append("Il file obbligatorio 'manifest_sha256.txt' è ASSENTE dall'archivio ZIP.")
            return False, results

        try:
            manifest_content = zf.read(manifest_info).decode("utf-8")
        except UnicodeDecodeError as ude:
            results["errors"].append(f"Impossibile decodificare 'manifest_sha256.txt' in UTF-8: {ude}")
            return False, results

        manifest_lines = [l.strip() for l in manifest_content.splitlines() if l.strip()]

        # 3. Parsing del manifest, verifica sintattica SHA-256 e controllo duplicati
        manifest_records: dict[str, str] = {}
        for line_no, line in enumerate(manifest_lines, 1):
            if line.startswith("#"):
                continue
            parts = line.split("  ", 1)
            if len(parts) != 2:
                parts = line.split(maxsplit=1)
            if len(parts) != 2:
                results["errors"].append(f"Riga {line_no} malformata nel manifest: '{line}'")
                continue
            sha, rel_path = parts[0].strip().lower(), parts[1].strip().replace("\\", "/")

            # Verifica sintattica rigorosa dell'hash SHA-256 (64 caratteri esadecimali)
            if not _HEX_SHA256_RE.match(sha):
                results["errors"].append(
                    f"Hash SHA-256 non valido sintatticamente alla riga {line_no} del manifest: '{sha}' per '{rel_path}'"
                )

            # Verifica divieto auto-referenzialità
            if rel_path == "manifest_sha256.txt":
                results["errors"].append("Il manifest non deve referenziare se stesso ('manifest_sha256.txt').")

            if rel_path in manifest_records:
                results["duplicate_entries"] += 1
                results["errors"].append(f"Percorso duplicato nel manifest alla riga {line_no}: '{rel_path}'")
            manifest_records[rel_path] = sha

        results["manifest_entries"] = len(manifest_records)
        print(f"[*] Voci caricate dal manifest: {results['manifest_entries']}")

        # 4. Costruzione della mappa dei file presenti nello ZIP con rilevazione collisioni e percorsi non sicuri
        zip_files_map: dict[str, zipfile.ZipInfo] = {}
        seen_normalized_names: dict[str, str] = {}

        for info in file_entries:
            raw_name = info.filename
            norm_name = raw_name.replace("\\", "/")

            if norm_name == "manifest_sha256.txt":
                continue

            # Rifiuto di separatori ambigui (es. // o \\)
            if "//" in norm_name or "\\\\" in raw_name:
                results["errors"].append(f"Separatori ambigui o doppi nel percorso: '{raw_name}'")

            # Rifiuto di percorsi assoluti (Unix o Windows)
            if norm_name.startswith("/") or raw_name.startswith("\\") or _WINDOWS_DRIVE_RE.match(raw_name):
                results["errors"].append(f"Percorso assoluto non consentito nello ZIP: '{raw_name}'")

            # Rifiuto di Directory Traversal o riferimenti relativi correnti
            path_segments = norm_name.split("/")
            if ".." in path_segments or "." in path_segments:
                results["errors"].append(f"Path traversal ('..' o '.') rilevato nello ZIP: '{raw_name}'")

            # Rilevazione collisioni normalizzate (es. 'foo/bar' vs 'foo\\bar')
            if norm_name in seen_normalized_names:
                results["duplicate_entries"] += 1
                results["errors"].append(
                    f"Collisione di nomi normalizzati rilevata nello ZIP: '{norm_name}' "
                    f"(voci in collisione: '{seen_normalized_names[norm_name]}' e '{raw_name}')"
                )
            else:
                seen_normalized_names[norm_name] = raw_name
                zip_files_map[norm_name] = info

        # 5. Verifica completezza bidirezionale: ZIP vs Manifest
        unlisted = set(zip_files_map.keys()) - set(manifest_records.keys())
        missing = set(manifest_records.keys()) - set(zip_files_map.keys())

        results["unlisted_files_in_manifest"] = len(unlisted)
        results["missing_files_in_zip"] = len(missing)

        if unlisted:
            for u in sorted(unlisted):
                results["errors"].append(f"File presente nello ZIP ma non registrato nel manifest: '{u}'")
        if missing:
            for m in sorted(missing):
                results["errors"].append(f"File registrato nel manifest ma non presente nello ZIP: '{m}'")

        # 6. Verifica crittografica SHA-256 dei byte effettivi di ciascun file
        print(f"[*] Calcolo SHA-256 dei byte per ciascuno dei {len(manifest_records)} file registrati...")
        for rel_path, expected_sha in manifest_records.items():
            if rel_path not in zip_files_map:
                continue
            file_bytes = zf.read(zip_files_map[rel_path])
            actual_sha = compute_sha256_bytes(file_bytes)
            if actual_sha == expected_sha:
                results["correct_hashes"] += 1
            else:
                results["mismatched_hashes"] += 1
                results["errors"].append(f"Mismatch hash per '{rel_path}': atteso {expected_sha}, effettivo {actual_sha}")

        print(f"[*] Esito crittografico: {results['correct_hashes']}/{results['manifest_entries']} hash corrispondenti.")

    # 7. Test runtime opzionale in directory temporanea isolata (compileall + pytest preflight)
    if run_tests and not results["errors"]:
        with tempfile.TemporaryDirectory() as td:
            extract_dir = Path(td)
            print(f"[*] Estrazione sicura in area temporanea di validazione: {extract_dir}")
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)

            # Test compilazione Python (compileall)
            print("[*] Esecuzione compileall sui sorgenti estratti...")
            cmd_comp = [sys.executable, "-m", "compileall", "-q", str(extract_dir)]
            comp_res = subprocess.run(cmd_comp, capture_output=True, text=True)
            if comp_res.returncode == 0:
                results["compileall_passed"] = True
                print("  [OK] compileall: 100% superato senza errori.")
            else:
                results["errors"].append(f"compileall fallito: {comp_res.stderr}")

            # Test esecuzione suite di regressione F1 (test 145..163)
            test_file = extract_dir / "tests" / "unit" / "test_checkpoint_preflight_resume.py"
            if test_file.is_file():
                print("[*] Esecuzione test di regressione F1 sui file estratti dallo ZIP...")
                cmd_pytest = [
                    sys.executable, "-m", "pytest",
                    str(test_file),
                    "-k", "test_145 or test_146 or test_147 or test_148 or test_149 or test_150 or test_151 or test_152 or test_153 or test_154 or test_155 or test_156 or test_157 or test_158 or test_159 or test_160 or test_161 or test_162 or test_163",
                    "-q",
                ]
                pyt_res = subprocess.run(cmd_pytest, cwd=str(extract_dir), capture_output=True, text=True)
                if pyt_res.returncode == 0:
                    results["pytest_passed"] = True
                    print("  [OK] Pytest regressioni F1: 100% superato.")
                else:
                    results["errors"].append(f"Pytest fallito:\n{pyt_res.stdout}\n{pyt_res.stderr}")

    success = (
        len(results["errors"]) == 0
        and results["mismatched_hashes"] == 0
        and results["missing_files_in_zip"] == 0
        and results["unlisted_files_in_manifest"] == 0
        and results["duplicate_entries"] == 0
        and results["correct_hashes"] == results["manifest_entries"]
        and results["manifest_entries"] > 0
    )
    results["status"] = "VERIFIED_PASS" if success else "FAILED"
    return success, results


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica crittografica e strutturale dell'archivio ChatAnalysis ZIP.")
    parser.add_argument("zip_path", nargs="?", default=None, help="Percorso del file ZIP da verificare.")
    parser.add_argument("--skip-tests", action="store_true", help="Salta la fase di compilazione e test Pytest.")
    args = parser.parse_args()

    if args.zip_path:
        zip_p = Path(args.zip_path)
    else:
        # Cerca nel path genitore (fuori dalla cartella Tesi)
        repo_dir = Path(__file__).resolve().parent.parent
        parent_dir = repo_dir.parent
        candidate = parent_dir / DEFAULT_ARCHIVE_NAME
        if candidate.is_file():
            zip_p = candidate
        else:
            candidate_legacy = parent_dir / LEGACY_ARCHIVE_NAME
            if candidate_legacy.is_file():
                zip_p = candidate_legacy
            else:
                zip_p = repo_dir / DEFAULT_ARCHIVE_NAME

    print("=" * 78)
    print("CHATANALYSIS — VERIFICA AUTOMATIZZATA DEL MANIFEST E DELL'ARCHIVIO ZIP")
    print("=" * 78)

    success, metrics = verify_zip_manifest(zip_p, run_tests=not args.skip_tests)

    print("\n" + "=" * 78)
    print(f"VERDETTO FINALE: {metrics['status']}")
    print(f"- File totali registrati nel manifest: {metrics['manifest_entries']}")
    print(f"- Hash SHA-256 esatti: {metrics['correct_hashes']}/{metrics['manifest_entries']}")
    print(f"- Hash discordanti: {metrics['mismatched_hashes']}")
    print(f"- File mancanti nello ZIP: {metrics['missing_files_in_zip']}")
    print(f"- File non elencati nel manifest: {metrics['unlisted_files_in_manifest']}")
    print(f"- Voci duplicate nel manifest / ZIP: {metrics['duplicate_entries']}")
    print(f"- Verifica compilazione (compileall): {'PASS' if metrics['compileall_passed'] else 'N/A'}")
    print(f"- Verifica test F1 (pytest): {'PASS' if metrics['pytest_passed'] else 'N/A'}")
    print("=" * 78)

    if not success:
        print("\n[!] ERRORI RISCONTRATI:")
        for err in metrics["errors"]:
            print(f"  - {err}")
        return 1

    print("\n[SUCCESS] L'archivio ZIP e il relativo manifest soddisfano il 100% dei requisiti di integrità.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
