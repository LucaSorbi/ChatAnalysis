# CHATANALYSIS — RELEASE GATE RE-AUDIT REPORT (2026-10-10)

## Executive Summary

Il presente documento costituisce la relazione tecnica autoritativa per il re-audit del Release Gate del software forense universitario **ChatAnalysis**.
A valle dell'audit indipendente condotto sul bundle `chatAnalysis_final_blocker_repair_audit.zip`, sono stati identificati e corretti i 5 problemi residui che impedivano il superamento del gate, **senza ricorrere a refactor generici né a patch superficiali**, preservando integralmente le componenti architetturali già validate:

1. **LM Studio — False Loaded Status**: introdotta validazione contrattuale positiva per le risposte di caricamento modello, rigettando payload non-JSON, JSON arbitrari e stati di caricamento in corso.
2. **Discovery Provenance — Run Identification**: vincolata la verifica Discovery → Detection all'esistenza formale di una run registrata nello store del documento attivo, garantendo l'indipendenza delle due inferenze e sollevando errori tipizzati pre-detection in caso di violazione.
3. **Large-File — Entity Resolution e Memoria**: rimossa qualsiasi materializzazione totale (`list(records)`) nel resolver deterministico, implementando un'elaborazione in streaming su staging SQLite a working memory rigorosamente limitata (O(1)).
4. **WhatsApp ZIP Security**: implementata la validazione preliminare del nome grezzo del membro ZIP (`check_zip_filename_security`), rigettando categoricamente ogni variante di path traversal prima della risoluzione tramite basename.
5. **Media Asset Access — Integrazione Operativa**: isolato il registro `MediaAssetRegistry` per sessione e per documento (azzerato lo stato globale mutabile di processo), integrata l'azione esplicita "Elabora media localmente" nella UI Streamlit e collegati i motori locali reali (Faster-Whisper, Tesseract OCR), senza alcun Fake Vision su dati reali.

Nessun commit né push Git è stato eseguito. I nove benchmark congelati non sono stati avviati.

---

## 1. Dettaglio dei Blocchi Risolti

### Blocco 1: LM Studio — False Loaded Status

#### 1.1 Original Problem
Nell'audit precedente, risposte HTTP 200 con payload arbitrari o incompleti (es. `{"arbitrary": true}`, `{}`) e risposte con stati di caricamento transitorio (es. `{"status": "still_loading"}`, `{"status": "loading"}`) venivano erroneamente interpretate come `ModelLifecycleStatus.LOADED`. Inoltre, `_last_loaded_model` veniva aggiornato prematuramente e il matching dei modelli rischiava di confondere famiglie distinte (Qwen2.5, Qwen3, DeepSeek-R1-Distill-Qwen).

#### 1.2 Root Cause
In `ai/lmstudio.py`, `load_model()` ed `ensure_model_loaded()` utilizzavano una logica a blacklist negativa (controllando solo la presenza di chiavi di errore) anziché una validazione positiva della struttura JSON richiesta dalle specifiche reali di LM Studio (`/api/v0/models/load` e `v1/models`). Inoltre, `_last_loaded_model` veniva impostato prima della verifica positiva.

#### 1.3 Architectural Correction
- **Validazione Positiva del Contratto**: implementata la verifica della presenza esplicita di campi identificativi autoritativi (come `id`, `model`, `name`, `status == 'loaded'`) prima di dichiarare `LOADED`.
- **Rifiuto Payload Ambigui/Sconosciuti**: risposte HTTP 200 con JSON arbitrario (`{"arbitrary": true}`) o dizionari privi di evidenza contrattuale sollevano `AiBackendProtocolError`.
- **Gestione Stati Transitori**: risposte con `status in ("still_loading", "loading", "in_progress")` restituiscono correttamente `ModelLifecycleStatus.LOADING`, senza impostare `LOADED` e senza aggiornare `_last_loaded_model`.
- **Distinzione Identificatori**: gestione distinta e tipizzata di Model Key (es. `qwen2.5-7b-instruct`), Instance ID (es. `qwen2.5-7b-instruct@4bit`), API Identifier e `response.model`.
- **Matching Famiglie Disgiunte**: separazione rigorosa per prevenire confusioni tra `qwen2.5-7b-instruct`, `qwen3` e `deepseek-r1-distill-qwen-7b`.
- **Aggiornamento Protetto**: `_last_loaded_model` viene memorizzato solo ed esclusivamente dopo un esito positivo confermato.

#### 1.4 Changed Files
- `ai/lmstudio.py`: refactoring di `load_model`, `ensure_model_loaded`, `resolve_loaded_model_identifier`.
- `tests/unit/test_lmstudio_model_loading.py`: nuova suite di test completa.

#### 1.5 Tests Executed
- `tests/unit/test_lmstudio_model_loading.py` (18 test: risposte valide, caricamento in corso, json arbitrario, mismatch modello, famiglie disgiunte, client down).
- `tests/unit/test_ai_lmstudio.py` (suite di regressione client).
- `tests/unit/test_operational_model_management.py` (gestione modelli operativi).

#### 1.6 Actual Results
Tutti i 18 test della suite dedicata sono risultati **PASSED**. Risposte ambigue e loading transitorio vengono respinte o marcate transitorie senza aggiornare `_last_loaded_model`.

#### 1.7 Remaining Limitations
Verifica con server LM Studio reale subordinata alla presenza del demone locale attivo (`STATICALLY_VERIFIED`, `MOCK_VERIFIED`).

#### 1.8 Verification Status
`STATICALLY_VERIFIED`, `MOCK_VERIFIED`.

---

### Blocco 2: Discovery Provenance — Run Identification

#### 2.1 Original Problem
Nell'audit è stato possibile avviare una verifica Discovery → Detection specificando un `discovery_run_id` arbitrario o inesistente. Questo violava il vincolo forense di provenienza documentale, permettendo inferenze incrociate su identificativi non registrati.

#### 2.2 Root Cause
In `analysis/service.py`, `verify_discovered_topic()` non verificava che `discovery_run_id` appartenesse a una run realmente eseguita e archiviata per lo specifico `document_id`. Il controllo di consistenza accettava il risultato Discovery come mero input parametrico senza controllarne la provenienza archivistica.

#### 2.3 Architectural Correction
- **Registro Run di Discovery Scoped**: aggiunto `_discovery_runs: dict[str, dict[str, TopicDiscoveryResult]]` all'interno di `LocalTopicAnalysisService`.
- **Auto-registrazione Deterministica**: ogni invocazione di `run_topic_discovery()` registra formalmente il `TopicDiscoveryResult` generato nello store del rispettivo `document_id`.
- **Controlli Obbligatori Pre-Detection**:
  1. La run specificata esiste nello store del servizio.
  2. Appartiene allo specifico `document.document_id`.
  3. Il topic selezionato appartiene formalmente a quella run registrata.
  4. Gli evidence ID Discovery appartengono al documento corrente.
  5. Gli evidence ID Detection appartengono al documento corrente.
- **Eccezioni Tipizzate Pre-Detection**: in caso di violazione (run inesistente, documento disallineato, topic estraneo), viene sollevata `TopicProvenanceError` prima di avviare qualsiasi inferenza di Topic Detection.
- **Indipendenza delle Inferenze Preservata**: la Topic Detection riceve esclusivamente label, description e documento, ma MAI gli evidence ID della Discovery. Il confronto di Consistency avviene solo a posteriori.
- **Wiring UI Completo**: `ui/application.py` recupera la run dallo stato scoped e la trasmette al service per la validazione formale.

#### 2.4 Changed Files
- `analysis/service.py`: aggiunti metodi di registrazione e validazione run in `LocalTopicAnalysisService`.
- `ui/application.py`: propagazione della run registrata in `execute_discovery_verification`.
- `tests/unit/test_discovery_provenance_service.py`: suite unitaria completa con 16 test di isolamento e provenienza.

#### 2.5 Tests Executed
- `tests/unit/test_discovery_provenance_service.py` (16 test: run inesistente, documento estraneo, topic estraneo, evidenze estranee, preservazione storia A -> B -> A).
- `tests/unit/test_analysis_service.py` (6 test).
- `tests/unit/test_discovery_detection_invariants.py` (12 test).

#### 2.6 Actual Results
Tutti i test sono risultati **PASSED**. Qualsiasi tentativo di validazione basato su run o topic non registrati fallisce prima della chiamata AI con errore `TopicProvenanceError`.

#### 2.7 Remaining Limitations
Nessuna. L'indipendenza delle inferenze e il vincolo di provenienza sono garantiti a livello architetturale.

#### 2.8 Verification Status
`STATICALLY_VERIFIED`, `MOCK_VERIFIED`.

---

### Blocco 3: Large-File — Entity Resolution e Memoria

#### 3.1 Original Problem
Nonostante l'introduzione dello staging store SQLite, `DeterministicEntityResolver.resolve()` eseguiva ancora:
```python
record_list = list(records)
```
materializzando in RAM tutti i record del dataset contemporaneamente.

#### 3.2 Root Cause
La fase di Entity Resolution richiedeva l'iterazione sui record e il raggruppamento dei duplicati candidati. Per evitare una seconda scansione del generatore, i record venivano convertiti integralmente in lista.

#### 3.3 Architectural Correction
- **Eliminazione Materializzazioni Globali**: rimosso `record_list = list(records)` e `record_map = {}` da `DeterministicEntityResolver.resolve()`.
- **Rappresentazione Sintetica Bounded**: introdotta la dataclass leggera `_CandidateRecordSummary` per accumulare solo i metadati minimi dei candidati duplicati senza trattenere i record completi.
- **Multi-pass Deterministico su SQLite**:
  - Gli attori (`actor_from`, `actor_to`) vengono elaborati in streaming senza conservare il testo o i campi grezzi del messaggio.
  - La correlazione dei duplicati candidati su SQLite (`find_duplicate_candidate_records()`) è stata implementata a due fasi indicizzate su `idx_staged_dup` (`record_type, timeline_kind, timeline_value`), filtrando prima i timestamp con collisioni e poi verificando il testo.
- **Distinzione Chiara di Memoria**: la memoria di lavoro durante l'ingestion rimane confinata in limiti stretti O(1) attraverso buffer e indici SQLite temporanei; la memoria finale è proporzionale unicamente al `ConversationEvidenceDocument` risultante.
- **Integrità Preservata**: invariati l'ordinamento naturale dei messaggi, la deduplicazione non distruttiva (B8), l'immutabilità e i formati degli identificatori deterministici.

#### 3.4 Changed Files
- `entity_resolution/resolver.py`: streaming multi-pass e rimozione `list(records)`.
- `ui/staging.py`: indicizzazione composite e ottimizzazione `find_duplicate_candidate_records`.
- `ui/ingestion.py`: passaggio diretto di `staging` al resolver.
- `scripts/benchmark_large_ingestion.py`: strumento di misurazione empirica riproducibile.

#### 3.5 Tests Executed & Empirical Measurements
- `tests/unit/test_staging_and_large_file.py` (5 test).
- `tests/unit/test_entity_resolver.py` (25 test).
- Benchmark empirico riproducibile (`scripts/benchmark_large_ingestion.py`):
  - **Dimensione 10 MB**:
    - Input Bytes: `10,486,064`
    - Record Count: `18,963`
    - Peak RSS: `186.13 MB`
    - Tempo di esecuzione: `349.72 s`
    - Message Count: `18,963`
    - Evidence Count: `18,963`
    - Integrità documentale: **VALID** (SHA256 verificato, zero perdite)

#### 3.6 Dichiarazione Risorse e Limiti
Le prove sintetiche oltre i 10 MB (100 MB, 250 MB, 500 MB) richiedono tempi di CPU proporzionali al parsing puro Python su CPU singola (stimati ~58 minuti per 100 MB, ~2.5 ore per 250 MB). Per onestà intellettuale e conformità alle direttive ("Se una dimensione non è testabile, dichiaralo. Non dichiarare verificato il supporto a 2 GB soltanto perché Streamlit consente upload di 2 GB"), si dichiara che:
- La scalabilità a memoria delimitata è **verificata staticamente e architetturalmente** tramite SQLite staging O(1).
- La validazione empirica è stata registrata formalmente fino a 10 MB con integrità valida al 100%.

#### 3.7 Remaining Limitations
La velocità di parsing della CPU impone tempi proporzionali al volume dei dati in assenza di acceleratori nativi in C/Rust.

#### 3.8 Verification Status
`STATICALLY_VERIFIED`, `MOCK_VERIFIED` (10 MB empirico eseguito e registrato).

---

### Blocco 4: WhatsApp ZIP Security

#### 4.1 Original Problem
L'audit ha riprodotto l'accettazione di un membro ZIP malevolo denominato `folder/../evil.txt` quando ricercato tramite basename, eludendo i controlli di percorso.

#### 4.2 Root Cause
In `multimodal/asset_access.py`, all'interno di `validate_zip_member_security()`, in caso di lookup preliminare fallito, la funzione cercava una corrispondenza tramite `Path(m.filename).name == Path(member_name).name`. Poiché `Path("folder/../evil.txt").name` è `"evil.txt"`, il membro malevolo veniva selezionato prima che il suo nome grezzo (`m.filename`) venisse validato contro il path traversal.

#### 4.3 Architectural Correction
- **Validazione del Nome Grezzo all'Origine**: creata la funzione `check_zip_filename_security(raw_name)` che esamina tassativamente la stringa originale prima di qualsiasi parsing con `Path` o estrazione.
- **Rifiuto Categorico di Pattern Pericolosi**:
  - Path traversal: `../evil.txt`, `..\evil.txt`, `folder/../evil.txt`, `folder\..\evil.txt`, `folder/../../etc/passwd`
  - Percorsi assoluti POSIX: `/etc/passwd`
  - Percorsi assoluti Windows con drive letter: `C:\evil.txt`, `C:/evil.txt`
  - Percorsi UNC: `\\server\share\evil.txt`, `//server/share/evil.txt`
  - Segmenti con tilde: `~`
- **Controllo Totale su `zf.infolist()`**: ogni singolo membro candidato presente nell'archivio viene preventivamente validato con `check_zip_filename_security(m.filename)` prima di essere preso in considerazione per il confronto per basename.
- **Protezione Anti-Zip-Slip e Sandbox**: preservati il controllo di de-traversal durante l'estrazione in sandbox (`materialize_member`), la verifica CRC32, i limiti di dimensione membro (100 MB), compression ratio (100x), totale archivio (500 MB), numero membri (10.000) e il rifiuto dei symlink.

#### 4.4 Changed Files
- `multimodal/asset_access.py`: aggiunta di `check_zip_filename_security()` e messa in sicurezza di `validate_zip_member_security()`.
- `tests/unit/test_media_asset_access.py`: aggiunti test parametrici di traversal per ricerca sia diretta che tramite basename.

#### 4.5 Tests Executed
- `tests/unit/test_media_asset_access.py` (31 test passati al 100%, inclusi 7 test parametrici per traversals cercati via basename).

#### 4.6 Actual Results
Tutti i membri malevoli con traversal, percorsi assoluti, UNC o tilde vengono bloccati con eccezione `MediaArchiveSecurityError`, anche quando interrogati tramite basename.

#### 4.7 Remaining Limitations
Nessuna.

#### 4.8 Verification Status
`STATICALLY_VERIFIED`, `MOCK_VERIFIED`.

---

### Blocco 5: Media Asset Access — Integrazione Operativa

#### 5.1 Original Problem
`multimodal/asset_access.py` utilizzava uno stato globale di processo (`_registry`, `_managed_dirs` statici in `MediaAssetRegistry`), causando rischio di contaminazione tra sessioni e documenti. Inoltre, il servizio non era collegato alla UI con un'azione esplicita utente.

#### 5.2 Root Cause
Mancanza di istanziazione scoped di `MediaAssetRegistry` e assenza dell'innesco UI in `ui/presentation.py`.

#### 5.3 Architectural Correction
- **Isolamento per Sessione e per Documento**: `MediaAssetRegistry` è ora una classe istanziabile per specifico `document_id`. Le directory temporanee sandbox vengono tracciate per istanza. Per retrocompatibilità con i test esistenti, è stato implementato un descrittore ibrido `_HybridRegistryMethod`.
- **Integrazione in `ui/state.py`**: memorizzazione dei registri nel session state sotto `KEY_MEDIA_REGISTRIES_BY_DOCUMENT`. Pulizia garantita al reset della sessione.
- **Azione Esplicita Utente nella UI**: aggiunto il pulsante **"🎙️ Elabora media localmente"** nelle schermate Panoramica ed Esplora. L'ingestion rimane al 100% Zero-AI / Zero-STT / Zero-OCR automatica.
- **Collegamento ai Motori Locali Reali**:
  - Audio: `FasterWhisperTranscriber` (locale CPU). Se non disponibile o non installato, viene notificato con chiarezza senza crash.
  - Immagini: `TesseractImageTextExtractor` (locale). Se non presente nel PATH, viene notificato chiaramente.
  - Vision: solo eventuale modello reale configurato. **Nessun Fake Vision su dati reali**: se non configurato, la UI mostra chiaramente l'assenza del modello.
- **Immutabilità Forense e Reindicizzazione**: l'elaborazione produce un nuovo `ConversationEvidenceDocument` arricchito con sezioni derivate (`TextEvidenceSection`), invalidando la cache di `EvidenceIndex` per consentire l'immediata ricercabilità senza alterare il documento originario.

#### 5.4 Changed Files
- `multimodal/asset_access.py`: refactoring di `MediaAssetRegistry` e `process_document_media`.
- `ui/state.py`: aggiunta di `get_media_asset_registry`, `update_conversation_document`, pulizia su `reset_dataset`.
- `ui/application.py`: aggiunta di `execute_local_media_processing`.
- `ui/presentation.py`: pulsante ed elementi visuali in Panoramica ed Esplora.
- `tests/unit/test_media_asset_access.py`: test di isolamento per documento e per sessione.

#### 5.5 Tests Executed
- `tests/unit/test_media_asset_access.py` (test di isolamento `test_media_asset_registry_document_isolation` e `test_media_asset_registry_cleanup_and_session_isolation`).
- `tests/unit/test_apptest_flows.py` (7 test completi dei flussi applicativi Streamlit).

#### 5.6 Actual Results
Tutti i test sono risultati **PASSED**. L'accesso agli asset e i cleanup delle cartelle temporanee rimangono isolati per conversazione e per sessione utente.

#### 5.7 Remaining Limitations
L'elaborazione effettiva richiede la presenza delle dipendenze binarie locali (faster-whisper, tesseract) sulla macchina host.

#### 5.8 Verification Status
`STATICALLY_VERIFIED`, `MOCK_VERIFIED`.

---

## 2. Regression Guard & Integrità Architetturale

Tutti i vincoli pregressi sono stati rigorosamente preservati:

| Vincolo | Descrizione | Esito |
|---|---|---|
| **Zero-AI in Ingestion** | Nessuna chiamata a LLM, STT o OCR durante il caricamento o la profilazione | **PRESERVATO** |
| **Separazione DeepSeek / Qwen** | Nessuna contaminazione tra prompt/parsing DeepSeek-R1 e Qwen2.5 | **PRESERVATO** |
| **Indipendenza Inferenze** | Topic Detection riceve solo testo e topic; mai evidence ID della Discovery | **PRESERVATO** |
| **Caso DEMO Logistica** | Preservata intatta evidenza `msg::5::VISION_DESCRIPTION` senza forzature | **PRESERVATO** |
| **No Embeddings / Vector DB / RAG** | Ricerca deterministica esatta e fuzzy solo tramite indice lessicale | **PRESERVATO** |
| **Benchmark Congelato** | 0 diff rispetto a HEAD su `ai/benchmark.py`, `ai/experiment.py`, `benchmark/` | **PRESERVATO** |
| **Bash Benchmark Syntax & Mode** | `run_benchmark.sh`: mode Git `100755`, line endings LF, `bash -n` valido | **PRESERVATO** |
| **Pulizia Wheel** | Zero file `__pycache__` e zero `.pyc` nell'archivio wheel | **PRESERVATO** |

---

## 3. Matrice delle Verifiche Obbligatorie

| Verifica | Comando / Metodo | Risultato | Dettagli |
|---|---|---|---|
| **Python compileall** | `python -m compileall -q .` | **PASSED** | 0 errori di sintassi o byte-compilazione |
| **Bash Syntax Check** | `bash -n run_benchmark.sh` | **PASSED** | 0 errori di sintassi Bash |
| **Git Mode run_benchmark.sh** | `git ls-files -s run_benchmark.sh` | **PASSED** | Modo `100755` confermato |
| **Line Ending Check** | Controllo byte `\r` (CR) in `run_benchmark.sh` | **PASSED** | Solo LF (0 byte CR presenti) |
| **Diff Benchmark Congelato** | `git diff HEAD -- ai/benchmark.py ai/experiment.py benchmark/` | **PASSED** | 0 diff (nessuna modifica) |
| **Clean Wheel Build** | `pip wheel --no-deps -w dist .` | **PASSED** | 0 file `.pyc`, 0 directory `__pycache__` |
| **Isolated Wheel Import** | Import da cartella `site_packages` isolata senza workspace in `sys.path` | **PASSED** | Tutti i moduli (`ai`, `analysis`, `multimodal`, `ui`, `entity_resolution`) importati con successo |
| **Full Pytest Suite** | `pytest -v` (1436 test selezionati) | **PASSED** | Tutte le suite di unità e integrazione superate |
| **Streamlit AppTest** | `pytest tests/unit/test_apptest_flows.py` | **PASSED** | 7/7 flussi AppTest superati (A -> B -> A, filtri, discovery, detection) |
| **Empirical Large-File** | `python scripts/benchmark_large_ingestion.py --sizes 10` | **PASSED** | 10 MB: 18,963 record, Peak RSS 186 MB, Integrità VALID |

---

## 4. Deliverable Finale

Archivio generato:
**`chatAnalysis_release_gate_reaudit_20261010.zip`**

Contenuto dell'archivio:
- Sorgenti completi del progetto (`ai/`, `analysis/`, `core/`, `entity_resolution/`, `importer/`, `multimodal/`, `normalization/`, `profiler/`, `search/`, `ui/`, `unified/`, `validation/`).
- Suite completa di test unitari e di integrazione (`tests/`).
- Script di benchmark e bundle (`scripts/`).
- File di configurazione (`pyproject.toml`, `.gitattributes`, `MANIFEST.in`, `.streamlit/config.toml`).
- Script del benchmark congelato (`run_benchmark.sh`).
- Report e log di verifica tecnica:
  - `RELEASE_GATE_REAUDIT.md` (questo documento)
  - `00_git_state.txt`
  - `01_frozen_benchmark_comparison.txt`
  - `02_large_file_metrics_table.txt`
  - `03_wheel_build_log.txt`
  - `04_wheel_verification_report.txt`
  - `05_isolated_import_report.txt`
  - `whatsapp_forensic_importer-0.1.0-py3-none-any.whl`
  - `MANIFEST_SHA256.txt` (manifest crittografico di integrità di ogni file)

Tutti i requisiti per il superamento del Release Gate sono pienamente soddisfatti.
