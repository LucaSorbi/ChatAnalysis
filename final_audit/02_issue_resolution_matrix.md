# Matrice di Risoluzione delle Criticità e Release Gate (Criteri 1 - 50)

> **Convenzione Stati Ammessi**:
> - `IMPLEMENTED`: Requisito implementato a livello di codice architetturale.
> - `STATICALLY_VERIFIED`: Verificato mediante ispezione statica, tipizzazione o grep search.
> - `MOCK_VERIFIED`: Verificato tramite test unitari, AppTest o test con mock/fake client.
> - `LIVE_VERIFIED`: Verificato su hardware finale con istanza reale attiva.
> - `NOT_VERIFIED`: Non ancora eseguito nell'ambiente di test corrente.
> - `FAILED`: Verifica fallita.

| # | Requisito / Criticità | Stato | Dettagli di Verifica ed Evidenza |
|---|---|---|---|
| 1 | `analysis/` presente nella wheel di release | MOCK_VERIFIED | `pyproject.toml` aggiornato con `"analysis*"`. Test reale `test_packaging_release.py` PASSED: verifica pacchetto dentro la wheel reale. |
| 2 | Wheel isolata importa `analysis` senza repo root | MOCK_VERIFIED | Wheel estratta in tempdir isolata con `PYTHONPATH` esclusivo: `import analysis`, `ui.application`, `ai.lmstudio` eseguiti con successo. |
| 3 | Session State ha UNA sola source of truth per document_id | MOCK_VERIFIED | `ui/state.py` riscritta: `analysis_by_document[doc_id]` è la sola fonte autoritativa. Vecchie chiavi globali eliminate o adattate come viste derivate pure. |
| 4 | Nessun risultato contamina una nuova conversazione | MOCK_VERIFIED | Nuovi stati inizializzati vuoti; test `test_multiconversation_preserves_results_across_switches` PASSED. |
| 5 | Passaggio A -> B -> A preserva i risultati | MOCK_VERIFIED | Verificato via Streamlit AppTest: alternanza tra conversazioni mantiene intatti i risultati per ciascun `document_id`. |
| 6 | Provenance e storico delle run coerente | IMPLEMENTED | `DocumentAnalysisState` implementa `detection_runs`, `discovery_runs`, `consistency_runs` immutabili tracciati con `run_id`. |
| 7 | Discovery contabilizza tutti i chunk | MOCK_VERIFIED | `DiscoveryChunkOutcome` traccia stato, error_type e chunk_id. Test in `test_discovery_detection_invariants.py` PASSED. |
| 8 | Discovery non ingoia eccezioni silenziose | MOCK_VERIFIED | Eccezioni catturate registrate in outcome; fatal errors propagati immediatamente. Test `test_fatal_backend_error_propagates_immediately` PASSED. |
| 9 | Zero successful Discovery chunks = errore operativo | MOCK_VERIFIED | Solleva `OperationalAnalysisFailedError`. Test in `test_discovery_detection_invariants.py` PASSED. |
| 10 | `chunk_count` è rigorosamente int | MOCK_VERIFIED | Rimossa virgola tupla in `analysis/service.py`; `assert isinstance(meta["chunk_count"], int)` verificato in unit test. |
| 11 | Nessun `[:max_topics]` silenzioso | MOCK_VERIFIED | `aggregate_topic_discovery` acquisisce tutti i topic e applica Deterministic Support Ranking dichiarato. Test PASSED. |
| 12 | Deterministic Support Ranking per max_topics | MOCK_VERIFIED | Ordinamento per: supporting chunks DESC, unique evidence DESC, first appearance index ASC, label ASC. Test PASSED. |
| 13 | Discovery inoltra `max_tokens` operativo | MOCK_VERIFIED | `LocalTopicAnalysisService.run_topic_discovery` inoltra `max_tokens` a tutte le chiamate single e multi chunk. |
| 14 | Detection zero successful chunks = errore operativo | MOCK_VERIFIED | Solleva `OperationalAnalysisFailedError` se completed chunks == 0 (mai falso ABSENT o UNCERTAIN). |
| 15 | Errori di backend fatali propagati immediatamente | MOCK_VERIFIED | `FATAL_BACKEND_ERRORS` interrompe immediatamente il ciclo chunk senza tentativi inutili. Test PASSED. |
| 16 | Partial chunk failure non produce falso ABSENT | MOCK_VERIFIED | Con almeno un chunk valido e un fallimento, la regola deterministica emette `UNCERTAIN` se non ci sono evidenze `PRESENT`. |
| 17 | Fallimento operativo non produce ConsistencyResult | MOCK_VERIFIED | La Prova del Nove esegue Consistency solo a Detection riuscita; in caso di errore operativo emette errore controllato. |
| 18 | Service verifica Discovery provenance | MOCK_VERIFIED | `verify_discovered_topic` verifica `provenance_document_id == document.document_id` e appartenenza del topic. Test PASSED. |
| 19 | ConsistencyService verifica Detection provenance | MOCK_VERIFIED | `ConsistencyService.evaluate` rifiuta discrepanze cross-documento. Test `test_consistency_service_rejects_cross_document` PASSED. |
| 20 | DeepSeek non viene risolto come Qwen | MOCK_VERIFIED | `OperationalModelSpec` per DeepSeek isolata: `forbidden_tokens=("llama",)`. Test `test_deepseek_never_resolved_as_qwen25` PASSED. |
| 21 | Qwen3 non viene risolto come Qwen2.5 | MOCK_VERIFIED | Qwen2.5 richiede tassativamente `"2.5"` e proibisce `"qwen3"`. Test `test_qwen3_never_resolved_as_qwen25` PASSED. |
| 22 | Llama, Qwen e DeepSeek hanno model spec separate | STATICALLY_VERIFIED | Definite in `ai/lmstudio.py` come istanze distinte di `OperationalModelSpec`. Test `test_model_resolution_strict_family_isolation` PASSED. |
| 23 | Model key, instance id, api identifier distinti | MOCK_VERIFIED | Implementato `ResolvedOperationalModel` con validazione `is_equivalent_operational_model`. Test PASSED. |
| 24 | JIT fallback 404/405 non finge LOADED | MOCK_VERIFIED | Restituisce `status="jit_unverified"`, `load_mode="jit_unverified"`, non memorizza `_last_loaded_model`. Test PASSED. |
| 25 | `is_model_loaded` non ingoia eccezioni reali | MOCK_VERIFIED | JSONDecodeError, errori di protocollo e timeout sollevano `AiBackendProtocolError`/`AiBackendRequestError`. Test PASSED. |
| 26 | Privacy gating usa metadata `data_classification` | MOCK_VERIFIED | Rimosso matching superficiale su stringhe nome file. Gating su `data_classification == "SYNTHETIC_DEMO"`. Test PASSED. |
| 27 | `source_name=None` non provoca crash | MOCK_VERIFIED | Gestito con valore di fallback sicuro; test con `source_name=None` in invariants PASSED. |
| 28 | Documento senza evidenze solleva `AnalysisNoEvidenceError` | MOCK_VERIFIED | Non viene interpretato come ABSENT operativo ma solleva `AnalysisNoEvidenceError`. Test PASSED. |
| 29 | Dead `prepare_operational_model` rimosso | STATICALLY_VERIFIED | Rimosso da `ui/application.py` e `ui/presentation.py`. Static ripgrep search conferma 0 occorrenze in codice attivo. |
| 30 | `TopicAnalysisRunMetadata` usato autoritativamente | MOCK_VERIFIED | Usato per popolare i metadati delle run in Detection e Discovery con tutti i campi di provenance. Test PASSED. |
| 31 | EvidenceIndex non ricostruito inutilmente | MOCK_VERIFIED | `get_cached_evidence_index` memorizza l'indice nello stato del documento e lo riutilizza. |
| 32 | Streaming WhatsApp resta line-by-line | MOCK_VERIFIED | Nessun `read_bytes()` globale o `splitlines()`; generatore a consumo incrementale. Test `test_line_by_line_streaming_generator` PASSED. |
| 33 | Nessun `errors="replace"` sui dati forensi | MOCK_VERIFIED | File e stream aperti con `errors="strict"`; sequenze byte corrotte sollevano `ValueError`. Test PASSED. |
| 34 | Pipeline ingestion bounded-memory a generatori | MOCK_VERIFIED | Pipeline riscritta a stream (`normalize_stream`, `stream_primary_validated`). Test `test_synthetic_large_stream_ingestion` PASSED. |
| 35 | Large file report dichiara la verità architetturale | STATICALLY_VERIFIED | Documentato nel report 11: upload streaming, parser streaming, staging locale, limite esplicito 2GB. |
| 36 | Media reali non falsamente dichiarati analizzati | STATICALLY_VERIFIED | File reali generano bundle con `ORIGINAL_TEXT`. Nessuna simulazione di OCR/Vision su dati reali. |
| 37 | Fake Vision non usata su dati reali | STATICALLY_VERIFIED | `FakeImageVisionAnalyzer` bandito dall'ingestion reale; visibile solo nei test mock. |
| 38 | Accesso media ZIP protetto e sandboxed | MOCK_VERIFIED | Protezione path traversal, UNC, symlink, absolute path e zip bomb. Test `test_zip_security_hardening.py` (15 test) PASSED. |
| 39 | AppTest DEMO passa | MOCK_VERIFIED | 7 AppTest in `test_apptest_flows.py` completati con successo (Discovery, Detection, Prova del Nove). |
| 40 | AppTest FILE passa | MOCK_VERIFIED | Test di ingestion ed esecuzione topic su FILE reale completati con successo. |
| 41 | Prova del Nove mock passa | MOCK_VERIFIED | Test di coerenza e overlap tra Discovery e Detection superati sia in unit che in AppTest. |
| 42 | Pytest = 0 failed, 0 errors | MOCK_VERIFIED | Suite completa eseguita: 1365 PASSED, 1 SKIPPED, 5 DESELECTED, 0 FAILED, 0 ERRORS. |
| 43 | Wheel build passa | MOCK_VERIFIED | Costruzione reale con pip wheel completata con codice 0. |
| 44 | Isolated wheel imports passano | MOCK_VERIFIED | Test subprocess su package estratti in tempdir isolata completato con successo. |
| 45 | `run_benchmark.sh` mode Git 100755 | STATICALLY_VERIFIED | `git ls-files -s run_benchmark.sh` conferma permessi `100755`. |
| 46 | `run_benchmark.sh` terminatori riga LF | STATICALLY_VERIFIED | Verificato b'\r\n' assente e terminazione con '\n'. |
| 47 | `bash -n run_benchmark.sh` passa | STATICALLY_VERIFIED | Sintassi bash verificata con `bash -n` terminata con codice 0. |
| 48 | Benchmark runner usa context 8192 | STATICALLY_VERIFIED | `run_benchmark.sh` configurato con `CONTEXT_LENGTH=8192` e passato esplicitamente al runner. |
| 49 | Benchmark core immutato | STATICALLY_VERIFIED | `ai/experiment.py`, `ai/benchmark.py`, ground truth e prompt benchmark intatti. |
| 50 | Nessun commit / Nessun push | STATICALLY_VERIFIED | Nessun commit o push effettuato nel repository. Working tree preservato per audit. |
