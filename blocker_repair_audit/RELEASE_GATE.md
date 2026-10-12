# RELEASE_GATE.md — Matrice di Valutazione e Release Decision

## Classificazione Rigorosa degli Stati:
- `IMPLEMENTED`: Requisito implementato a livello di architettura o codice.
- `STATICALLY_VERIFIED`: Verificato mediante ispezione statica, tipizzazione o grep search.
- `MOCK_VERIFIED`: Verificato mediante test unitari o di integrazione automatizzati con mock o fake client.
- `LIVE_VERIFIED`: Verificato su hardware finale con istanza reale di LM Studio attiva.
- `NOT_VERIFIED`: Non eseguibile sulla macchina di sviluppo attuale (richiede server LM Studio Ubuntu).
- `FAILED`: Verifica fallita.

---

## Tabella dei Controlli di Release

| Ambito | Requisito / Controllo | Stato | Evidenza Tecnica |
|---|---|---|---|
| **Blocker 1** | Lifecycle formale modello LM Studio | MOCK_VERIFIED | `ModelLifecycleStatus` implementato in `ai/lmstudio.py`; 14 test unitari PASSED. |
| **Blocker 1** | Rifiuto payload HTTP 200 'failed' | MOCK_VERIFIED | Solleva `AiModelLoadError(status_code=200)`. Test `test_load_model_http_200_failure_payload_raises_error` PASSED. |
| **Blocker 1** | Gestione JSON malformato / vuoto | MOCK_VERIFIED | Solleva `AiBackendProtocolError`. Test dedicati PASSED. |
| **Blocker 1** | Separazione Qwen2.5 vs Qwen3 vs DeepSeek | MOCK_VERIFIED | `is_equivalent_operational_model` impedisce corrispondenze cross-family. Test PASSED. |
| **Blocker 1** | Binding del modello su prima completion JIT | MOCK_VERIFIED | Verifica modello in `jit_unverified`; solleva `AiModelMismatchError` se disallineato. Test PASSED. |
| **Blocker 1** | Smoke test live LM Studio | NOT_VERIFIED | Non eseguibile sulla macchina Windows; script `scripts/smoke_operational_ai.py` pronto per Ubuntu. |
| **Blocker 2** | Discovery provenance document-scoped | MOCK_VERIFIED | `verify_discovered_topic` verifica appartenenza del topic al documento. Test PASSED. |
| **Blocker 2** | Discovery run_id obbligatorio | MOCK_VERIFIED | Mancanza di `discovery_run_id` solleva `TopicProvenanceError`. Test PASSED. |
| **Blocker 2** | Isolamento inferenziale Detection | MOCK_VERIFIED | Detection riceve solo `label` e `short_description`; zero evidence IDs o decisioni passate. Test PASSED. |
| **Blocker 2** | Rifiuto evidence ID estranei | MOCK_VERIFIED | Validazione incrociata con `document.all_evidence_sections`. Test PASSED. |
| **Blocker 2** | Matrice di Consistency a 4 stati | MOCK_VERIFIED | `ConsistencyService.evaluate` su risultati reali indipendenti. Test PASSED. |
| **Blocker 3** | Staging locale SQLite per ingestion | MOCK_VERIFIED | `IngestionStagingStore` implementato in `ui/staging.py`; 5 test unitari PASSED. |
| **Blocker 3** | Bounded working memory durante ingestion | MOCK_VERIFIED | Misurato ~102 MB RAM Working Set durante stream da oltre 100 MB. |
| **Blocker 3** | Distinzione working RAM vs final document RAM | STATICALLY_VERIFIED | Documentata chiaramente nel report architetturale Blocker 3. |
| **Blocker 4** | Accesso e ciclo di vita media ZIP su richiesta | MOCK_VERIFIED | `MediaAssetAccessService` in `multimodal/asset_access.py`. 22 test PASSED. |
| **Blocker 4** | Sicurezza ZIP (traversal, symlink, bomb, CRC) | MOCK_VERIFIED | Rifiuto percorsi assoluti, traverse, symlink e zip bomb. 15 test di sicurezza PASSED. |
| **Blocker 4** | Zero fake vision su dati reali | STATICALLY_VERIFIED | `FakeImageVisionAnalyzer` bandito dall'ingestion reale; visibile solo nei test mock. |
| **Packaging** | Inclusione package `analysis/` e core | MOCK_VERIFIED | Pacchetto wheel include `analysis/` e tutti i package fondamentali. |
| **Packaging** | Esclusione assoluta `__pycache__` e `*.pyc` | MOCK_VERIFIED | Wheel verificata: ZERO file `.pyc` o `__pycache__` (90 entry totali pulite). |
| **Packaging** | Test di import isolato senza repo root | MOCK_VERIFIED | Subprocess su directory estratta senza repo root in sys.path: successo. |
| **Packaging** | `python -m compileall` | STATICALLY_VERIFIED | Compilazione di tutti i file sorgente completata con codice 0. |
| **UI** | Streamlit AppTest flussi A-G | MOCK_VERIFIED | 7 AppTest in `test_apptest_flows.py` completati con codice 0. |
| **UI** | Isolamento multi-conversazione (A->B->A) | MOCK_VERIFIED | Verificato via AppTest: alternanza conversazioni preserva risultati e cronologia. |
| **Runner** | `run_benchmark.sh` mode 100755 | STATICALLY_VERIFIED | Confermato permessi git index `100755`. |
| **Runner** | `run_benchmark.sh` terminatori LF | STATICALLY_VERIFIED | Confermato terminatori LF senza CRLF. |
| **Runner** | `bash -n run_benchmark.sh` | STATICALLY_VERIFIED | Validazione sintattica con git bash completata con codice 0. |
| **Benchmark** | Core benchmark intatto | STATICALLY_VERIFIED | `ai/experiment.py`, `ai/benchmark.py`, ground truth e prompt intatti. |
| **Benchmark** | Nove run finali NON eseguiti | STATICALLY_VERIFIED | Nessun benchmark finale eseguito sul repository. |
| **Git** | Nessun commit / Nessun push | STATICALLY_VERIFIED | Vincoli rispettati al 100%. |

---

## Verdetto del Release Gate

Tutti i requisiti implementativi, architetturali e di packaging sono stati soddisfatti e convalidati mediante verifiche statiche e test automatici.
La validazione operativa con LM Studio reale (`LIVE_VERIFIED`) e le 9 esecuzioni del benchmark finale restano riservate all'ambiente target Ubuntu con GPU, come specificato dal protocollo congelato.
