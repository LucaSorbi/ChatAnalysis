"""
Script di ripristino NON distruttivo dalla sottocartella di pulizia Trash.
Non sovrascrive mai file gia esistenti nella destinazione originale e verifica SHA-256.

Uso dalla root del repository:
  # Dry-run di un singolo file o prefisso (es. tests/):
  python Trash/repository_cleanup_2026-11-07/restore_from_trash.py --prefix tests/ --dry-run

  # Ripristino effettivo di tests/ e test_data/:
  python Trash/repository_cleanup_2026-11-07/restore_from_trash.py --prefix tests/
  python Trash/repository_cleanup_2026-11-07/restore_from_trash.py --prefix test_data/

  # Ripristino integrale di tutti i file spostati:
  python Trash/repository_cleanup_2026-11-07/restore_from_trash.py --all
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Ripristino non distruttivo da manifest_cleanup.json")
    parser.add_argument("--path", help="Percorso relativo esatto da ripristinare (es. docs/ARCHITECTURE.md)")
    parser.add_argument("--prefix", help="Prefisso di cartella da ripristinare (es. tests/)")
    parser.add_argument("--all", action="store_true", help="Ripristina tutti i file registrati nel manifest")
    parser.add_argument("--dry-run", action="store_true", help="Mostra le operazioni senza spostare alcun file")
    args = parser.parse_args()

    if not (args.path or args.prefix or args.all):
        parser.error("Specificare --path, --prefix oppure --all.")

    cleanup_dir = Path(__file__).resolve().parent
    repo_root = cleanup_dir.parent.parent
    manifest_file = cleanup_dir / "manifest_cleanup.json"
    data = json.loads(manifest_file.read_text(encoding="utf-8"))

    selected = []
    for rec in data["records"]:
        orig = rec["original_path"]
        if args.all or (args.path and orig == args.path) or (args.prefix and orig.startswith(args.prefix)):
            selected.append(rec)

    if not selected:
        print("Nessun file corrispondente trovato nel manifest.")
        return 1

    print(f"File selezionati per il ripristino: {len(selected)} (dry_run={args.dry_run})")
    restored = 0
    skipped = 0
    for rec in selected:
        src = repo_root / Path(rec["moved_path"])
        dst = repo_root / Path(rec["original_path"])
        if not src.exists():
            print(f"  [MANCANTE IN TRASH] {rec['moved_path']}")
            skipped += 1
            continue
        if dst.exists():
            print(f"  [COLLISIONE - NON SOVRASCRITTO] {rec['original_path']} esiste gia!")
            skipped += 1
            continue
        curr_sha = sha256_file(src)
        if curr_sha != rec["sha256_after"]:
            print(f"  [ERRORE HASH] {rec['moved_path']}: atteso {rec['sha256_after']}, trovato {curr_sha}")
            skipped += 1
            continue
        if args.dry_run:
            print(f"  [DRY-RUN] {rec['moved_path']} -> {rec['original_path']}")
            restored += 1
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.rename(src, dst)
            post_sha = sha256_file(dst)
            assert post_sha == rec["sha256_before"], "Discrepanza SHA-256 post-ripristino"
            restored += 1

    print(f"Completato: ripristinati={restored}, saltati={skipped}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
