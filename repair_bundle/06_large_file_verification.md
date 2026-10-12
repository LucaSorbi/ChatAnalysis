# VERIFICA GRANDI FILE & STREAMING

## Pipeline di Ingestion a Memoria Limitata
1. **Upload Stream**: La copia da Streamlit a temporary staging file avviene a chunk di 64 KB tramite `_compute_sha256_and_write()`. Nessun utilizzo di `.getvalue()`.
2. **Streaming Parser WhatsApp**:
   - `WhatsAppExportImporter` non chiama più `read_bytes()` né `.splitlines()` sull'intero file.
   - Utilizza decodifica incrementale streaming con `io.TextIOWrapper` e lettura linea per linea (`_parse_lines_stream`).
   - Mantiene in memoria esclusivamente il buffer del singolo messaggio corrente durante la composizione dei messaggi multilinea.
3. **Hardening ZIP**:
   - Rifiuto categorico di qualsiasi traversal con normalizzazione unificata (`/` e `\`).
   - Rifiuto di symlink POSIX, percorsi assoluti Windows/POSIX, UNC paths, e drive letters.
   - Protezione da ZIP bomb con limiti di compression ratio, decompressed size e numero di membri.

## Test di Validazione
- `tests/unit/test_large_file_upload.py::TestStreamingAndChunking::test_hashing_and_writing_executed_in_chunks`
- `tests/unit/test_large_file_upload.py::TestStreamingAndChunking::test_simulated_file_over_200mb_accepted`
- `tests/unit/test_large_file_upload.py::TestStreamingAndChunking::test_file_over_2gb_rejected_immediately`
- `tests/unit/test_large_file_upload.py::TestStreamingAndChunking::test_whatsapp_importer_streams_transcript_without_read_bytes`
- `tests/unit/test_zip_security_hardening.py`: 15 test dedicati ai percorsi malevoli.
