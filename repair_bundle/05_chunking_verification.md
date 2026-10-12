# VERIFICA DEL CHUNKING OPERATIVO

## Architettura del Chunking
Il chunking operativo è gestito interamente all'interno di `LocalTopicAnalysisService` (`analysis/service.py`).
Per documenti che superano la soglia di budget conservativa (8192 token di contesto con margine di sicurezza):
1. Il documento viene ripartito atomicamente preservando i bundle di evidenza tramite `split_document_into_chunks()`.
2. Ogni chunk viene analizzato singolarmente con tracking degli esiti (`ChunkAnalysisOutcome`).
3. L'aggregazione segue regole rigorose:
   - **PRESENT**: Se almeno un chunk completato produce `PRESENT` valido.
   - **ABSENT**: Soltanto se TUTTI i chunk attesi hanno avuto successo e hanno restituito `ABSENT`.
   - **UNCERTAIN**: Se nessun chunk è PRESENT e almeno un chunk è fallito o incerto. **Nessun errore di chunk produce falso ABSENT.**

## Test di Validazione
- `tests/unit/test_analysis_service.py::test_service_single_chunk_detection`: verifica documento singolo.
- `tests/unit/test_analysis_service.py::test_service_multi_chunk_detection_aggregates_present`: verifica aggregazione su più chunk.
- `tests/unit/test_analysis_service.py::test_service_multi_chunk_failure_produces_uncertain_not_absent`: verifica che un chunk fallito produca UNCERTAIN e non ABSENT.
- `tests/unit/test_analysis_service.py::test_service_multi_chunk_discovery`: verifica aggregazione su topic discovery multi-chunk.
