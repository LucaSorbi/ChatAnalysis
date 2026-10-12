# BLOCKER 4 — Media ZIP e Multimodalità Reale

## 1. Problema Originale
L'importer WhatsApp ZIP individuava i riferimenti ai media, ma il file sorgente decompresso veniva cancellato con la directory temporanea di ingestion, lasciando i media inaccessibili per successive analisi offline e generando errori o falsi stati completati.

## 2. Causa Radice
Assenza di un componente di sessione responsabile della persistenza controllata e dell'accesso su richiesta ai membri degli archivi ZIP.

## 3. Decisione Architetturale
Progettazione di `MediaAssetAccessService` e `MediaAssetRegistry` (`multimodal/asset_access.py`):
1. **Conservazione Gestita della Sorgente di Sessione**:
   Durante l'ingestion di un archivio ZIP contenente media, la sorgente viene copiata in streaming (`copy_source_to_managed`) in una sandbox gestita associata al `document_id`.
2. **Accesso e Materializzazione On-Demand**:
   Nessuna estrazione indiscriminata dell'intero archivio su disco. I file media vengono estratti solo su richiesta esplicita dell'utente ("Elabora media localmente").
3. **Sicurezza Rigorosa degli Archivi ZIP**:
   Implementata `validate_zip_member_security` con rifiuto categorico di:
   - Path traversal (`../`, `..\`, `folder/../evil.txt`);
   - Percorsi assoluti POSIX (`/etc/passwd`), Windows drive (`C:\...`) e UNC (`\\server\share\...`);
   - Symlink (attributi esterni POSIX `0o120000`);
   - File eccedenti la dimensione massima (`MAX_SINGLE_FILE_SIZE_BYTES`);
   - Zip bomb (rapporto di compressione anomalo > 100.0);
   - Integrità payload compromessa (controllo CRC32 e SHA-256).
4. **Trasparenza Zero-AI durante l'Ingestion**:
   L'ingestion genera esclusivamente evidenze di tipo `ORIGINAL_TEXT`. L'elaborazione multimodale (Faster-Whisper per audio, Tesseract per OCR) avviene solo offline su comando esplicito.
5. **Nessun Fake Vision su Dati Reali**:
   Se il motore Vision locale non è configurato, viene esplicitamente segnalato ("Vision locale non configurata"), senza mai invocare fake analyzer.

## 4. File Modificati
- `multimodal/asset_access.py`: Creazione di `MediaAssetAccessService`, `MediaAssetRegistry`, `validate_zip_member_security`, `enrich_document_with_local_media`.
- `multimodal/__init__.py`: Esportazione delle nuove classi e funzioni.
- `ui/ingestion.py`: Integrazione della registrazione della sorgente gestita per archivi ZIP.
- `tests/unit/test_media_asset_access.py`: Nuova suite di 22 test di sicurezza e operatività.

## 5. Test Aggiunti ed Esiti
- `test_txt_without_media_returns_no_reference`: PASSED
- `test_zip_with_image_materializes_on_demand`: PASSED
- `test_zip_with_audio_materializes_on_demand`: PASSED
- `test_zip_with_missing_media_returns_missing`: PASSED
- `test_zip_with_ambiguous_media_rejected`: PASSED
- `test_zip_with_malformed_member_raises_error`: PASSED
- `test_zip_traversal_attacks_rejected` (8 varianti di path traversal): PASSED
- `test_zip_symlink_rejected`: PASSED
- `test_zip_bomb_large_member_rejected`: PASSED
- `test_zip_crc_mismatch_rejected`: PASSED
- `test_source_unavailable_after_ingestion_raises_error`: PASSED
- `test_stt_backend_unavailable_marks_status_without_crash`: PASSED
- `test_ocr_backend_unavailable_marks_status_without_crash`: PASSED
- `test_vision_backend_unavailable_does_not_invoke_fake`: PASSED
- `test_real_processing_with_test_fixtures`: PASSED

## 6. Stato di Verifica: MOCK_VERIFIED & SANDBOX_VERIFIED (22/22 PASSED)
