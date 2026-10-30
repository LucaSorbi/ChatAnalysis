"""
scripts/detection_microchunk_status.py
--------------------------------------
Diagnostica di SOLA LETTURA dei checkpoint di Topic Detection a microchunk.

Garanzie:
- apertura SQLite con URI `mode=ro` e PRAGMA query_only = ON: nessuna scrittura, nessuna cancellazione;
- output costituito da SOLI CONTATORI e da un identificativo opaco (prime 8 cifre esadecimali del
  fingerprint): nessun testo di conversazione, nessun evidence_id, nessun percorso, nessun risultato di modello.

Uso:
    python scripts/detection_microchunk_status.py --db <cartella_checkpoint>\\checkpoints.db
    python scripts/detection_microchunk_status.py --db ... --json

Snapshot consistente (app in esecuzione): NON copiare a mano checkpoints.db/-wal/-shm (una copia ingenua di file
in scrittura può essere incoerente o corrotta). Usare invece:
    python scripts/detection_microchunk_status.py --db <...>\\checkpoints.db --snapshot-dir <cartella_nuova>
che crea la copia con l'Online Backup API di SQLite (sorgente aperta in sola lettura), verifica
`PRAGMA integrity_check` sulla copia e legge lo stato dalla copia. In alternativa: chiudere l'app e copiare i file offline.
I checkpoint originali non vengono mai modificati.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any

_HEX = re.compile(r"^[0-9a-fA-F]+$")


def _opaque(fp: Any) -> str:
    s = str(fp or "")
    return s[:8].lower() if _HEX.match(s) else "opaco"


def collect_status(db_path: Path) -> list[dict[str, Any]]:
    """Restituisce, per ogni checkpoint di tipo 'detection', soli contatori numerici."""
    uri = f"file:{Path(db_path).resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.execute("PRAGMA query_only = ON;")
        rows = conn.execute(
            "SELECT document_fingerprint, config_fingerprint, total_planned_chunks, updated_at "
            "FROM checkpoints WHERE analysis_type = 'detection' ORDER BY updated_at DESC;"
        ).fetchall()
        out: list[dict[str, Any]] = []
        for doc_fp, cfg_fp, planned, updated in rows:
            done = {
                int(r[0])
                for r in conn.execute(
                    "SELECT chunk_index FROM checkpoint_chunks WHERE document_fingerprint = ? AND config_fingerprint = ?;",
                    (doc_fp, cfg_fp),
                )
            }
            per_parent = [
                {"parent_chunk_index": int(p), "microchunks_saved": int(n), "microchunks_planned": int(t)}
                for p, n, t in conn.execute(
                    "SELECT parent_chunk_index, COUNT(*), MAX(total_subchunks) FROM checkpoint_subchunks "
                    "WHERE document_fingerprint = ? AND config_fingerprint = ? GROUP BY parent_chunk_index "
                    "ORDER BY parent_chunk_index;",
                    (doc_fp, cfg_fp),
                )
            ]
            out.append(
                {
                    "checkpoint": f"{_opaque(doc_fp)}/{_opaque(cfg_fp)}",
                    "parent_chunks_planned": int(planned),
                    "parent_chunks_completed": len(done),
                    "microchunks_saved_total": sum(p["microchunks_saved"] for p in per_parent),
                    "parents_with_partial_microchunks": sum(
                        1 for p in per_parent if p["parent_chunk_index"] not in done
                    ),
                    "per_parent": per_parent,
                    "updated_at": str(updated)[:19],
                }
            )
        return out
    finally:
        conn.close()


def snapshot_db(src: Path, dest_dir: Path) -> Path:
    """Copia consistente di `src` in `dest_dir` con l'Online Backup API + verifica di integrità. Non tocca l'originale."""
    src = Path(src).resolve()
    dest_dir = Path(dest_dir).resolve()
    dest = dest_dir / "checkpoints_snapshot.db"
    if dest == src:
        raise ValueError("destinazione coincidente con la sorgente")
    if dest.exists():
        raise FileExistsError("lo snapshot di destinazione esiste già (usare una cartella nuova)")
    dest_dir.mkdir(parents=True, exist_ok=True)
    src_conn = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    try:
        src_conn.execute("PRAGMA query_only = ON;")
        dst_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
    chk = sqlite3.connect(f"file:{dest.as_posix()}?mode=ro", uri=True)
    try:
        verdict = chk.execute("PRAGMA integrity_check;").fetchone()[0]
    finally:
        chk.close()
    if verdict != "ok":
        raise sqlite3.DatabaseError("integrity_check dello snapshot non riuscito")
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Stato (sola lettura) dei checkpoint di Topic Detection a microchunk")
    ap.add_argument("--db", required=True, help="Percorso di checkpoints.db")
    ap.add_argument("--json", action="store_true", help="Output JSON")
    ap.add_argument("--snapshot-dir", default=None, help="Cartella NUOVA in cui creare uno snapshot consistente (Online Backup API)")
    args = ap.parse_args(argv)
    db = Path(args.db)
    if not db.is_file():
        print("Database non trovato.", file=sys.stderr)
        return 2
    try:
        if args.snapshot_dir:
            db = snapshot_db(db, Path(args.snapshot_dir))
            print("Snapshot consistente creato e verificato (integrity_check=ok); stato letto dallo snapshot.", file=sys.stderr)
        status = collect_status(db)
    except (sqlite3.Error, OSError, ValueError) as exc:
        print(f"Operazione non riuscita ({type(exc).__name__}); nessuna modifica ai checkpoint originali.", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0
    if not status:
        print("Nessun checkpoint di Topic Detection presente.")
        return 0
    for s in status:
        print(
            f"[{s['checkpoint']}] aggiornato {s['updated_at']} | parent completati "
            f"{s['parent_chunks_completed']}/{s['parent_chunks_planned']} | microchunk salvati "
            f"{s['microchunks_saved_total']} | parent con microchunk parziali {s['parents_with_partial_microchunks']}"
        )
        for p in s["per_parent"]:
            print(f"    parent {p['parent_chunk_index']}: microchunk {p['microchunks_saved']}/{p['microchunks_planned']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
