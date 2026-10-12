"""
ui/staging.py
-------------
Staging store locale e deterministico su SQLite per l'elaborazione bounded-memory
dei file di grandi dimensioni durante la pipeline di ingestion forense.

Principi architetturali:
1. ZERO NETWORK & ZERO EXTERNAL DAEMONS:
   Database SQLite temporaneo confinato nella sandbox locale della sessione.
2. BOUNDED WORKING MEMORY:
   I record normalizzati non vengono accumulati in liste complete in memoria,
   ma scritti e letti a chunk controllati (batch commits / fetchmany).
3. DETERMINISMO E INTEGRITÀ FORENSE:
   Preservazione dell'ordine sequenziale originario (seq_order), delle chiavi
   di sorgente e dell'isolamento per chat/conversazione.
4. ISOLAMENTO MULTI-CHAT:
   Consente l'iterazione e la costruzione dei ConversationEvidenceDocument chat-by-chat,
   evitando dizionari monolitici contenenti tutti i messaggi dell'intero dataset.
5. PULIZIA GARANTITA:
   Gestito via context manager per chiudere e rilasciare i file descriptor.
"""
from __future__ import annotations

import copyreg
from pathlib import Path
import pickle
import sqlite3
import types
from typing import Any, Iterable, Iterator, Mapping, Sequence

from normalization.models import NormalizedRecord

# Registrazione serializzatore MappingProxyType per pickle
def _reconstruct_mappingproxy(d: dict[str, Any]) -> types.MappingProxyType:
    return types.MappingProxyType(d)

copyreg.pickle(types.MappingProxyType, lambda mp: (_reconstruct_mappingproxy, (dict(mp),)))


class IngestionStagingStore:
    """
    Store locale temporaneo SQLite per lo staging dei NormalizedRecord.
    Garantisce bounded working memory durante l'ingestion e accesso indicizzato per chat.
    """

    def __init__(
        self,
        db_path: Path | str,
        batch_size: int = 1000,
    ) -> None:
        self.db_path = Path(db_path)
        self.batch_size = max(1, batch_size)
        self._seq_order = 0
        self._buffer: list[tuple[int, str, str, str, str, bytes]] = []

        # Inizializza database SQLite locale
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            isolation_level=None,  # Gestione manuale delle transazioni
        )
        self._conn.row_factory = sqlite3.Row

        # Ottimizzazioni per prestazioni elevate su storage locale temporaneo
        self._conn.execute("PRAGMA synchronous = OFF;")
        self._conn.execute("PRAGMA journal_mode = OFF;")
        self._conn.execute("PRAGMA temp_store = MEMORY;")
        self._conn.execute("PRAGMA cache_size = -16000;")  # ~16 MB RAM cache

        self._create_tables()

    def _create_tables(self) -> None:
        with self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS staged_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    seq_order INTEGER NOT NULL,
                    record_type TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    source_record_id TEXT NOT NULL,
                    chat_key TEXT NOT NULL,
                    payload BLOB NOT NULL
                );
            """)
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_staged_seq ON staged_records(seq_order);")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_staged_type ON staged_records(record_type);")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_staged_chat ON staged_records(chat_key);")

    def stage_record(
        self,
        record: NormalizedRecord,
        chat_key: str | None = None,
    ) -> None:
        """
        Aggiunge un record al buffer di staging, svuotando su SQLite al raggiungimento del batch_size.
        """
        self._seq_order += 1
        eff_chat_key = chat_key or self.extract_chat_key(record)
        blob = pickle.dumps(record, protocol=pickle.HIGHEST_PROTOCOL)

        self._buffer.append((
            self._seq_order,
            str(record.record_type or "unknown"),
            str(record.source_name or "unknown"),
            str(record.source_record_id or str(self._seq_order)),
            eff_chat_key,
            blob,
        ))

        if len(self._buffer) >= self.batch_size:
            self.flush()

    def stage_stream(
        self,
        records: Iterable[NormalizedRecord],
    ) -> int:
        """
        Riceve uno stream di NormalizedRecord, lo inserisce a blocchi in SQLite e restituisce il conteggio totale.
        """
        count = 0
        for rec in records:
            self.stage_record(rec)
            count += 1
        self.flush()
        return count

    def flush(self) -> None:
        """Esegue il commit immediato dei record nel buffer su SQLite."""
        if not self._buffer:
            return
        cursor = self._conn.cursor()
        cursor.execute("BEGIN TRANSACTION;")
        cursor.executemany(
            """
            INSERT INTO staged_records (seq_order, record_type, source_name, source_record_id, chat_key, payload)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            self._buffer,
        )
        self._conn.commit()
        self._buffer.clear()

    @staticmethod
    def extract_chat_key(record: NormalizedRecord) -> str:
        """
        Estrae determinoristicamente la chiave di raggruppamento chat dal record normalizzato.
        """
        if record.chat_id is not None and str(record.chat_id).strip():
            return str(record.chat_id).strip()
        if record.actor_to is not None and record.actor_to.raw_value:
            return f"peer:{record.actor_to.raw_value.strip()}"
        return "UNRESOLVED"

    def count(self, record_type: str | None = None) -> int:
        """Restituisce il numero totale di record staged (o per record_type)."""
        self.flush()
        cursor = self._conn.cursor()
        if record_type:
            cursor.execute("SELECT COUNT(*) FROM staged_records WHERE record_type = ?;", (record_type,))
        else:
            cursor.execute("SELECT COUNT(*) FROM staged_records;")
        row = cursor.fetchone()
        return int(row[0]) if row else 0

    def iter_all_records(self, chunk_size: int = 1000) -> Iterator[NormalizedRecord]:
        """Itera su tutti i record staged in ordine sequenziale originario (seq_order)."""
        self.flush()
        cursor = self._conn.cursor()
        cursor.execute("SELECT payload FROM staged_records ORDER BY seq_order ASC;")
        while True:
            rows = cursor.fetchmany(chunk_size)
            if not rows:
                break
            for r in rows:
                yield pickle.loads(r["payload"])

    def iter_records_by_type(self, record_type: str, chunk_size: int = 1000) -> Iterator[NormalizedRecord]:
        """Itera sui record dello specifico record_type in ordine seq_order."""
        self.flush()
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT payload FROM staged_records WHERE record_type = ? ORDER BY seq_order ASC;",
            (record_type,),
        )
        while True:
            rows = cursor.fetchmany(chunk_size)
            if not rows:
                break
            for r in rows:
                yield pickle.loads(r["payload"])

    def get_distinct_chat_keys(self) -> list[str]:
        """
        Restituisce l'elenco deterministico delle chiavi di chat per i record di tipo 'message'.
        UNRESOLVED è sempre posizionato in coda se presente.
        """
        self.flush()
        cursor = self._conn.cursor()
        cursor.execute("SELECT DISTINCT chat_key FROM staged_records WHERE record_type = 'message';")
        keys = [r[0] for r in cursor.fetchall() if r[0] is not None]

        standard_keys = sorted([k for k in keys if k != "UNRESOLVED"])
        ordered = list(standard_keys)
        if "UNRESOLVED" in keys:
            ordered.append("UNRESOLVED")
        return ordered

    def iter_records_for_chat(self, chat_key: str, chunk_size: int = 1000) -> Iterator[NormalizedRecord]:
        """Itera sui soli record di tipo 'message' appartenenti alla specifica chat."""
        self.flush()
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT payload FROM staged_records WHERE record_type = 'message' AND chat_key = ? ORDER BY seq_order ASC;",
            (chat_key,),
        )
        while True:
            rows = cursor.fetchmany(chunk_size)
            if not rows:
                break
            for r in rows:
                yield pickle.loads(r["payload"])

    def extract_chat_titles(self) -> dict[str, str]:
        """Estrae i titoli delle chat registrati nei record di tipo 'chat'."""
        titles: dict[str, str] = {}
        for rec in self.iter_records_by_type("chat"):
            subj = None
            if hasattr(rec, "raw_record") and hasattr(rec.raw_record, "raw_fields"):
                raw_f = rec.raw_record.raw_fields
                subj = raw_f.get("subject") or raw_f.get("title") or raw_f.get("chat_name")
            cid = rec.chat_id or (rec.source_record_id if rec.source_record_id else None)
            if cid and subj and str(subj).strip():
                titles[str(cid).strip()] = str(subj).strip()
        return titles

    def close(self) -> None:
        """Chiude la connessione SQLite."""
        try:
            self.flush()
        except Exception:
            pass
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self) -> IngestionStagingStore:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
