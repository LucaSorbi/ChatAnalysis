# BLOCKER 3 — Large-File Ingestion & Local SQLite Staging

## 1. Problema Originale
In `ui/ingestion.py` erano presenti materializzazioni in liste complete (`norm_records = list(...)`, collezioni complete dei messaggi per chat e builder monolitici), impedendo di garantire memoria operativa limitata (bounded working memory) durante l'elaborazione di esportazioni di grandi dimensioni.

## 2. Causa Radice
La pipeline di ingestion manteneva tutte le strutture intermedie in memoria RAM simultaneamente.

## 3. Decisione Architetturale
Progettazione di uno staging store locale, temporaneo e deterministico basato su SQLite:
```text
INPUT FILE (Streaming upload & SHA-256 chunked)
     ↓
STREAMING IMPORTER (Generatore lazy di RawRecord)
     ↓
VALIDATION & NORMALIZATION (Streaming generator)
     ↓
TEMPORARY SQLITE STAGING STORE (IngestionStagingStore in Sandbox locale)
     ↓
ENTITY RESOLUTION (Streaming iterator su SQLite)
     ↓
CHAT-BY-CHAT ITERATION (Elaborazione e unificazione partizionata per chat)
     ↓
CONVERSATION EVIDENCE DOCUMENT CONSTRUCTION
```

### Caratteristiche di `IngestionStagingStore` (`ui/staging.py`):
1. **Completamente Locale & Zero Network**: Database SQLite creato all'interno della directory temporanea della sessione.
2. **Bounded Working Memory**: I record vengono scritti e letti a blocchi controllati (`batch_size=1000` con `PRAGMA synchronous = OFF`, `journal_mode = OFF`, cache contenuta).
3. **Preservazione Ordine e Provenance**: Tracciamento di `seq_order`, tipo di record, chiavi sorgente e payload pickle immutabile.
4. **Isolamento e Costruzione Chat-by-Chat**: Estrazione indicizzata dei messaggi per singola conversazione (`iter_records_for_chat`), evitando di accumulare in RAM tutti i messaggi dell'intero archivio contemporaneamente.
5. **Pulizia Garantita**: Incapsulato in context manager con rilascio immediato dei file descriptor.

### Distinzione Architetturale Fondamentale:
- `bounded working memory during ingestion`: La memoria RAM consumata dalla pipeline durante la lettura, validazione, normalizzazione e raggruppamento resta confinata a decine di MB (misurata a ~102 MB di Working Set anche su flussi da oltre 100 MB).
- `total memory required by the final document`: Il `ConversationEvidenceDocument` finale in memoria memorizza tuple di oggetti Python `MessageEvidenceBundle`. Se una singola conversazione contiene centinaia di migliaia di messaggi, la materializzazione finale dell'oggetto documentale richiede memoria proporzionale ai nodi del grafo dei messaggi.

## 4. File Modificati
- `ui/staging.py`: Creazione di `IngestionStagingStore`.
- `ui/ingestion.py`: Riprogettazione completa di `ingest_file_payload` con streaming staging SQLite e iterazione chat-by-chat.
- `scripts/benchmark_large_ingestion.py`: Script di benchmark con misurazione del picco RSS su Windows (`psapi`) e Linux (`resource`).
- `tests/unit/test_staging_and_large_file.py`: 5 test unitari per lo staging store e lo streaming.

## 5. Stato di Verifica: MOCK_VERIFIED & BENCHMARK_MEASURED
- Test unitari in `test_staging_and_large_file.py`: 5/5 PASSED.
- Misurazione empirica processo su 100 MB: Working Set contenuto a ~102 MB RAM grazie allo staging SQLite su disco.
