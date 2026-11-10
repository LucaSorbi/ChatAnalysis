"""
scripts/archive_private_local.py
--------------------------------
Archiviazione LOCALE (sul PC dell'utente) di analisi private dentro `Trash/Privato_Locale/`.

Garanzie:
- DRY-RUN di default: senza `--execute` non viene creato, copiato o modificato nulla.
- Gli originali NON vengono mai spostati, modificati o cancellati: si COPIA.
- Mai sovrascrive: se la destinazione esiste il file viene saltato e segnalato.
- File normali: copia + verifica SHA-256 sorgente/destinazione.
- Database SQLite: copia con l'Online Backup API (`sqlite3.Connection.backup`), mai con una copia ingenua del file
  (WAL/SHM); verifica con `PRAGMA integrity_check` e confronto del numero di righe per tabella.
  Nota: aprire in sola lettura un DB in modalita' WAL puo' creare file `-wal`/`-shm` vuoti accanto alla sorgente.
- I checkpoint ATTIVI (cartella di `CHATANALYSIS_CHECKPOINT_DIR` oppure `.checkpoints/`) sono ESCLUSI di default;
  per includerli bisogna chiudere l'app e passare `--include-active-checkpoints`.
- La destinazione deve stare dentro `Trash/Privato_Locale/` (la sola cartella ignorata da git): altrimenti rifiuta.
- Esclusi sempre: `.git`, `.venv*`, `__pycache__`, file `-wal`/`-shm`/`-journal`.

Uso (Windows, dalla radice del repository):
    python scripts\\archive_private_local.py --source output --label analisi_2026_10          (dry-run)
    python scripts\\archive_private_local.py --source output --label analisi_2026_10 --execute

Nessun dato viene letto in modo semantico: lo script lavora solo su byte, dimensioni, hash e conteggi di righe.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

SQLITE_MAGIC = b"SQLite format 3\x00"
EXCLUDED_DIR_NAMES = {".git", "__pycache__", ".pytest_cache", "node_modules"}
EXCLUDED_SUFFIXES = ("-wal", "-shm", "-journal")
PRIVATE_DEST_REL = Path("Trash") / "Privato_Locale"


@dataclass(frozen=True)
class PlanItem:
    source: Path
    rel: Path
    size: int
    method: str  # "COPY" | "SQLITE_BACKUP"
    action: str  # "COPY" | "SKIP_EXISTS" | "SKIP_ACTIVE_CHECKPOINT"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def is_sqlite(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(16) == SQLITE_MAGIC
    except OSError:
        return False


def _excluded(p: Path) -> bool:
    if any(part in EXCLUDED_DIR_NAMES or part.startswith(".venv") for part in p.parts):
        return True
    return p.name.endswith(EXCLUDED_SUFFIXES)


def active_checkpoint_dir(repo_root: Path) -> Path:
    env = os.environ.get("CHATANALYSIS_CHECKPOINT_DIR")
    return (Path(env) if env else repo_root / ".checkpoints").resolve()


def resolve_dest_root(repo_root: Path, dest: Path | None, label: str) -> Path:
    base = (repo_root / PRIVATE_DEST_REL).resolve()
    target = (dest if dest else base).resolve()
    if target != base and base not in target.parents:
        raise ValueError(f"Destinazione rifiutata: deve stare dentro {PRIVATE_DEST_REL} (trovato: {target})")
    if not label or any(c in label for c in "/\\") or label in {".", ".."}:
        raise ValueError("--label deve essere un nome semplice (niente separatori di percorso)")
    return target / label


def build_plan(
    sources: Sequence[Path], dest_root: Path, repo_root: Path, include_active: bool = False
) -> list[PlanItem]:
    active = active_checkpoint_dir(repo_root)
    dest_resolved = dest_root.resolve()
    items: list[PlanItem] = []
    for src in sources:
        src = src.resolve()
        if not src.exists():
            raise FileNotFoundError(f"Sorgente inesistente: {src}")
        files: Iterable[Path] = [src] if src.is_file() else (p for p in sorted(src.rglob("*")) if p.is_file())
        base = src.parent if src.is_file() else src
        for f in files:
            if _excluded(f.relative_to(base)) or dest_resolved in f.resolve().parents:
                continue
            rel = Path(src.name) / f.relative_to(base) if src.is_dir() else Path(f.name)
            in_active = active == f.resolve() or active in f.resolve().parents
            method = "SQLITE_BACKUP" if is_sqlite(f) else "COPY"
            if in_active and not include_active:
                action = "SKIP_ACTIVE_CHECKPOINT"
            elif (dest_root / rel).exists():
                action = "SKIP_EXISTS"
            else:
                action = "COPY"
            items.append(PlanItem(f, rel, f.stat().st_size, method, action))
    return items


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    return {n: conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}


def backup_sqlite(src: Path, dst: Path) -> tuple[bool, str]:
    dst.parent.mkdir(parents=True, exist_ok=True)
    s = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    d = sqlite3.connect(str(dst))
    try:
        s.backup(d)
        d.commit()
        integrity = d.execute("PRAGMA integrity_check").fetchone()[0]
        same = _table_counts(s) == _table_counts(d)
    finally:
        d.close()
        s.close()
    ok = integrity == "ok" and same
    return ok, f"integrity={integrity};righe_coerenti={same}"


def copy_file(src: Path, dst: Path) -> tuple[bool, str, str]:
    dst.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    with open(src, "rb") as fi, open(dst, "xb") as fo:  # "x": non sovrascrive mai
        for block in iter(lambda: fi.read(1 << 20), b""):
            h.update(block)
            fo.write(block)
    src_hash = h.hexdigest()
    dst_hash = sha256_file(dst)
    return src_hash == dst_hash, src_hash, dst_hash


def run(
    sources: Sequence[Path],
    label: str,
    repo_root: Path,
    dest: Path | None = None,
    execute: bool = False,
    include_active: bool = False,
    out=print,
) -> int:
    dest_root = resolve_dest_root(repo_root, dest, label)
    plan = build_plan(sources, dest_root, repo_root, include_active)
    out(f"Modalita': {'ESECUZIONE' if execute else 'DRY-RUN (nulla viene copiato)'}")
    out(f"Destinazione: {dest_root}")
    out(f"File considerati: {len(plan)}")
    for it in plan:
        tag = it.action if it.action != "COPY" else f"{it.method}"
        out(f"  [{tag}] {it.rel}  ({it.size} byte)")
    todo = [it for it in plan if it.action == "COPY"]
    if not execute:
        out(f"DRY-RUN completato: {len(todo)} file verrebbero copiati. Rilanciare con --execute per eseguire.")
        return 0

    manifest_rows: list[dict[str, str]] = []
    failures = 0
    for it in todo:
        dst = dest_root / it.rel
        try:
            if it.method == "SQLITE_BACKUP":
                ok, detail = backup_sqlite(it.source, dst)
                src_hash, dst_hash = "(n/a: backup SQLite)", sha256_file(dst)
            else:
                ok, src_hash, dst_hash = copy_file(it.source, dst)
                detail = "sha256_uguale" if ok else "SHA256_DIVERSO"
        except Exception as exc:  # noqa: BLE001 - riportiamo il tipo, non il contenuto
            ok, src_hash, dst_hash, detail = False, "", "", f"ERRORE:{type(exc).__name__}"
        failures += 0 if ok else 1
        manifest_rows.append(
            dict(rel_path=it.rel.as_posix(), method=it.method, size_bytes=str(it.size), sha256_source=src_hash,
                 sha256_dest=dst_hash, verified="YES" if ok else "NO", detail=detail)
        )
        out(f"  {'OK ' if ok else 'ERR'} {it.rel}  {detail}")
    if manifest_rows:
        dest_root.mkdir(parents=True, exist_ok=True)
        mpath = dest_root / "ARCHIVE_MANIFEST.csv"
        new = not mpath.exists()
        with open(mpath, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(manifest_rows[0].keys()))
            if new:
                w.writeheader()
            w.writerows(manifest_rows)
        out(f"Manifest: {mpath}")
    out(f"Copiati e verificati: {len(manifest_rows) - failures}/{len(manifest_rows)}; errori: {failures}. Originali intatti.")
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", action="append", required=True, type=Path, help="file o cartella da copiare (ripetibile)")
    ap.add_argument("--label", required=True, help="nome della sottocartella di destinazione (es. analisi_2026_10)")
    ap.add_argument("--dest", type=Path, default=None, help="radice di destinazione (default: Trash/Privato_Locale)")
    ap.add_argument("--execute", action="store_true", help="esegue davvero (senza: dry-run)")
    ap.add_argument("--include-active-checkpoints", action="store_true", help="include i checkpoint attivi (chiudere prima l'app)")
    ap.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = ap.parse_args(argv)
    try:
        return run(args.source, args.label, args.repo_root.resolve(), args.dest, args.execute, args.include_active_checkpoints)
    except (ValueError, FileNotFoundError) as exc:
        print(f"RIFIUTATO: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
