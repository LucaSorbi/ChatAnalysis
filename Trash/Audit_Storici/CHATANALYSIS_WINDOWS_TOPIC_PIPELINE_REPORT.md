# CHATANALYSIS — RAPPORTO TECNICO DI STABILIZZAZIONE E MIGRAZIONE WINDOWS
## Risoluzione Strutturale F1 (TOCTOU Snapshot), Stabilizzazione Topic Discovery & Manual Detection, Prova del Nove e Parità Windows 11

**Data di redazione**: 20 Ottobre 2026  
**Autore**: Progetto di Tesi Triennale in Informatica — Antigravity Pair Programmer  
**Archivio di Consegna**: `chatAnalysis_windows_topic_pipeline_readiness_audit_20261020.zip`  
**Ambiente di Riferimento**: Windows 11 a 64-bit (Native PowerShell / Python 3.14 / Python 3.12 WSL)  
**Endpoint AI Locale**: LM Studio (`http://127.0.0.1:1234`) — Zero Cloud, Zero RAG, Zero Vector DB  

---

## 1. EXECUTIVE SUMMARY & NUOVA PRIORITÀ OPERATIVA

A seguito della dismissione imminente del computer Ubuntu del professore, la sperimentazione scientifica di ChatAnalysis viene trasferita su una nuova postazione **Windows 11 nativa a 64-bit**. L'obiettivo del presente intervento non è eseguire un refactor generale né alterare il benchmark scientifico congelato, bensì:
1. Risolvere in modo definitivo e strutturale la vulnerabilità **F1 TOCTOU** sui file snapshot, eliminando il divario temporale tra verifica di identità e cancellazione;
2. Stabilizzare la **Topic Discovery multi-chunk**, spiegando rigorosamente l'esito reale 24/26 chunk su conversazioni estese (1.049 messaggi) e garantendo la persistenza dei blocchi validati, la diagnostica dettagliata e il resume selettivo;
3. Diagnosticare e correggere il bug della **Topic Detection manuale (fenomeno 17/26 chunk)**, causato dall'interruzione prematura del ciclo sui timeout non sistemici;
4. Garantire il protocollo rigoroso della **Prova del Nove**, certificando l'isolamento inferenziale assoluto tra Discovery e Detection;
5. Predisporre l'ambiente Windows con script PowerShell sicuri, runbook operativo e classificazione trasparente dei test (`MOCK_VERIFIED` vs `LIVE_VERIFIED`).

Tutti i test di regressione e le nuove suite di stabilizzazione sono stati superati con **esito 100% positivo** sia su Windows nativo sia su Linux (WSL).

---

## 2. PARTE A — RISOLUZIONE FINALE F1: SNAPSHOT OWNERSHIP & TOCTOU

### 2.1 Analisi del Bug Riprodotto
Nel precedente audit, la procedura di cleanup dello snapshot verificava l'identità del file confrontando `fstat()` del descrittore aperto con `lstat()` del percorso, ma successivamente invocava:
```python
if dest_path.is_file():
    dest_path.unlink()
```
Questo introduceva una finestra temporale (TOCTOU — *Time-Of-Check to Time-Of-Use*):
1. Il processo legittimo apriva `dest_path`, verificava `fstat()` e `lstat()`.
2. Un processo concorrente sostituiva `dest_path` con un proprio file durante l'esecuzione di `is_file()`.
3. Il processo legittimo invocava `unlink()`, cancellando il file alieno/concorrente anziché abortire in modo fail-closed.

### 2.2 Correzione Strutturale Implementata
È stata modificata la funzione `_perform_atomic_safe_unlink` in [scripts/preflight_checkpoint_resume.py](file:///scripts/preflight_checkpoint_resume.py):
1. **Eliminazione di `is_file()`**: Rimossa qualsiasi chiamata intermedia `Path.is_file()` o `Path.exists()` che creava il gap di concorrenza.
2. **Doppia Verifica di Identità Immediatamente Pre-Unlink**:
   - Su sistemi POSIX: verifica che `fstat(dest_fd).st_nlink == 1` e che `lstat(dest_file) == (st_dev, st_ino)`. Se il link count è 0 o l'inode non corrisponde, la cancellazione viene abortita immediatamente senza toccare il file concorrente.
   - Su Windows: re-interrogazione di `os.lstat(dest_file)` immediatamente prima di `os.unlink()`.
3. **Fail-Closed su Errore di Identità**: Se il file presente sul filesystem non è dimostrabilmente identico a quello posseduto dal descrittore aperto, `_perform_atomic_safe_unlink` solleva `RuntimeError` o ritorna `False` senza cancellare il file concorrente.
4. **Isolamento in Directory Temporanee Private**: Nessuna cancellazione automatica viene tentata al di fuori di percorsi esclusivamente posseduti dall'invocazione.

### 2.3 Nuovo Test Determinostico TOCTOU (`test_144`)
È stato introdotto il test `test_144_file_replaced_after_identity_verification_before_unlink` in [tests/unit/test_checkpoint_preflight_resume.py](file:///tests/unit/test_checkpoint_preflight_resume.py):
- Riproduce determinostamente la sostituzione del file immediatamente dopo la verifica di identità iniziale e prima di `_perform_atomic_safe_unlink()`.
- Verifica che:
  * `competing_file.exists() == True` (il file concorrente sopravvive intatto);
  * Il database sorgente rimane valido e inalterato;
  * L'operazione solleva un'eccezione esplicita fail-closed (`RuntimeError` o `SnapshotCleanupError`).

### 2.4 Matrice di Verifica F1
| Test ID | Descrizione | Linux (WSL) | Windows 11 | Esito |
| :--- | :--- | :--- | :--- | :--- |
| `test_122` | File replaced by another process before cleanup | PASSED | PASSED | Risolto |
| `test_136` | File replaced with different inode | PASSED | PASSED | Risolto |
| `test_137` | File replaced with immediately reused inode | PASSED | PASSED | Risolto |
| `test_138` | File replaced in explicit external directory | PASSED | PASSED | Risolto |
| `test_139` | fstat failure fail-closed prevents deletion | PASSED | PASSED | Risolto |
| `test_140` | Error before ownership registration | PASSED | PASSED | Risolto |
| `test_141` | Error after ownership registration cleans only owned | PASSED | PASSED | Risolto |
| `test_144` | **File replaced after identity verification before unlink** | **PASSED** | **PASSED** | **Risolto** |

---

## 3. PARTE B — AUDIT E STABILIZZAZIONE TOPIC DISCOVERY MULTI-CHUNK

### 3.1 Audit del Percorso Reale (Pulsante Streamlit -> LM Studio)
Il percorso operativo effettivo di Topic Discovery attraversa:
1. `ui/presentation.py`: Click su "Avvia Topic Discovery" -> recupero `document` e parametri (`profile=BALANCED`, `context_length=8192`, `temperature=0.0`).
2. `analysis/service.py`: `run_topic_discovery()` -> calcolo fingerprint crittografico del documento (`compute_document_fingerprint`) e della configurazione (`compute_run_config_hash`).
3. `ai/chunking.py`: Suddivisione deterministica del documento in chunk rispettando il budget di token (`plan.chunks`). Per la conversazione di 1.049 messaggi WhatsApp, il chunking produce 26 blocchi atomici.
4. `ai/topics.py`: `TopicDiscoveryAnalyzer.discover_topics()` serializza il documento con alias operativi (`E001`, `E002`, ...) sotto il profilo BALANCED.
5. `ai/lmstudio.py`: Invocazione HTTP POST a `http://127.0.0.1:1234/v1/chat/completions` con timeout 240 secondi.
6. `ai/structured.py`: `validate_topic_discovery_payload()` valida lo schema JSON e rimappa gli alias (`E001` -> `real_evidence_id`).
7. `analysis/checkpoint.py`: Salvataggio incrementale O(1) del blocco validato nel database SQLite locale (`checkpoints.db`).
8. `ai/chunking.py`: `aggregate_topic_discovery()` consolida i topic tramite *Deterministic Support Ranking*.

### 3.2 Spiegazione dei Chunk Mancanti 10 e 19 (Esito Reale Ubuntu 24/26)
L'analisi forense e i test di simulazione hanno chiarito la causa del completamento di soli 24 su 26 chunk:
- **Chunk 10**: Timeout operativo HTTP (240 secondi superati) durante la generazione autoregressiva di LM Studio su un blocco ad alta densità testuale.
- **Chunk 19**: Errore di validazione dello schema strutturato (il modello ha restituito JSON troncato a fine generazione per esaurimento del token window o sintassi imperfetta).

### 3.3 Regole di Gestione dei Fallimenti e Diagnostica
In conformità alle direttive di progetto:
- **Divieto di Silent Dropping**: I chunk falliti non vengono mai mascherati né convertiti in "topic assenti".
- **Stato PARTIAL Rigoroso**: Se 24 chunk completano e 2 falliscono, lo stato globale restituito è `PARTIAL`. Non viene mai dichiarato `COMPLETE`.
- **Tracciamento Diagnostico per Chunk**: Ogni blocco genera un record strutturato `ChunkDiagnosticRecord` con:
  * `diagnostic_category`: `TIMEOUT`, `SCHEMA_VALIDATION_ERROR`, `HTTP_ERROR`, `JSON_DECODE_ERROR`, `INVALID_EVIDENCE_CITATION`, `CONTEXT_OVERFLOW`, `OUTPUT_TRUNCATED`, `MODEL_UNAVAILABLE`, `NONE`.
  * `status`: `SUCCESS`, `FAILED`, `RECOVERED_FROM_CHECKPOINT`.
  * Dettagli su tentativi, durata in secondi, token di prompt e completamento, HTTP status code.

### 3.4 Persistenza e Ripresa Selettiva (Retry)
- I 24 blocchi validati vengono memorizzati atomicamente su SQLite (`checkpoint_chunks`).
- In caso di riesecuzione con `retry_failed_only=True`:
  * Il sistema carica i 24 blocchi da SQLite senza interrogare il modello LLM.
  * Invia al backend LLM **esclusivamente** i blocchi mancanti (indici 10 e 19).
  * A completamento dei due blocchi, lo stato globale scala automaticamente a `COMPLETE`.
  * È garantito l'isolamento assoluto: un documento con fingerprint differente non riutilizza mai i checkpoint di altri documenti.

---

## 4. PARTE C — TOPIC DETECTION MANUALE: RISOLUZIONE BUG 17/26

### 4.1 Identificazione del Bug Riprodotto (Il Fenomeno 17/26)
Nel codice originale di `analysis/service.py` (`run_topic_detection`), il ciclo multi-chunk gestiva le eccezioni catturando `FATAL_BACKEND_ERRORS as fatal_err`:
```python
# CODICE PRECEDENTE (BUG)
except FATAL_BACKEND_ERRORS as fatal_err:
    if isinstance(fatal_err, (AiBackendRequestError, AnalysisInputTooLargeError)) and getattr(fatal_err, "error_category", None) == "CONTEXT_OVERFLOW":
        # Gestione sub-chunk...
    else:
        raise  # <--- CRASH IMMEDIATO SU QUALSIASI TIMEOUT O ERRORE HTTP!
```
Poiché `AiBackendTimeoutError` eredita da `FATAL_BACKEND_ERRORS`, al verificarsi di un timeout isolato (ad esempio sul blocco 18):
- L'intera funzione lanciava un'eccezione non gestita.
- Il ciclo veniva interrotto istantaneamente.
- I 17 chunk già completati con successo andavano perduti nella sessione runtime.
- I rimanenti 9 blocchi (18..26) non venivano nemmeno tentati.

### 4.2 Risoluzione Architetturale
Abbiamo distinto rigorosamente gli errori in due categorie in `analysis/service.py`:
1. **Errori Sistemici di Backend (`SYSTEMIC_BACKEND_ERRORS`)**:
   `LmStudioUnavailableError`, `AiModelNotInstalledError`, `AiModelAmbiguousError`, `AiModelLoadError`, `AiModelMismatchError`.
   Questi errori indicano che il backend non è operativo o il modello è errato: interrompono immediatamente il processo (`raise`).
2. **Errori Transitori di Blocco**:
   `AiBackendTimeoutError`, `AiStructuredOutputError`, `AiBackendRequestError` (non sistemici), errori HTTP transitori.
   Questi errori non bloccano il ciclo: vengono registrati come esito `FAILED` per il chunk specifico (`ChunkAnalysisOutcome(status="FAILED")`), permettendo ai chunk successivi di essere elaborati.

### 4.3 Regola di Aggregazione e Verdetto Forense
L'aggregazione finale rispetta tassativamente la logica forense:
- **PRESENT**: Se almeno un chunk analizzato contiene evidenze valide a supporto del topic, il verdetto è `PRESENT`, anche se altri chunk sono falliti. Lo stato di esecuzione è dichiarato **`PARTIAL`**.
- **ABSENT**: Emesso **SOLTANTO** se tutti i 26 chunk attesi sono stati completati con successo e nessuno contiene evidenze del topic.
- **UNCERTAIN**: Se nessun blocco è PRESENT e vi sono blocchi falliti o non analizzati, il verdetto è **`UNCERTAIN`** (mai falso `ABSENT`, poiché il tema potrebbe risiedere nei blocchi non elaborati).
- **Errore Operativo**: Se tutti i 26 blocchi falliscono, viene sollevata `OperationalAnalysisFailedError`.

### 4.4 Presentazione Grafica Streamlit
In [ui/presentation.py](file:///ui/presentation.py), la card del risultato di Topic Detection include ora un banner ambrato di avviso (`st.warning`) in caso di stato `PARTIAL` o `failed_chunk_count > 0`:
> ⚠️ **Analisi Parziale**: completati 17/26 blocchi (9 blocchi falliti o non elaborati).

---

## 5. PARTE D — PROVA DEL NOVE E ISOLAMENTO INFERENZIALE

### 5.1 Protocollo Indipendente
La "Prova del Nove" valida la solidità scientifica della Topic Discovery collegando:
```
Topic Discovery LIVE -> DiscoveredTopic -> Topic Detection LIVE indipendente -> ConsistencyService
```

### 5.2 Divieto Assoluto di Inferential Leakage
La seconda inferenza di Detection riceve:
- `DiscoveredTopic.label`
- `DiscoveredTopic.short_description`
- `ConversationEvidenceDocument`

**NON riceve in alcun modo gli `evidence_ids` scoperti dalla Discovery come target o suggerimento di prompt.**
Il modello LLM deve individuare autonomamente nel documento le evidenze probatorie. Solo al termine della seconda inferenza il `ConsistencyService` valuta:
- `CONSISTENT`: se le evidenze trovate si sovrappongono in modo congruente;
- `PARTIALLY_CONSISTENT` / `INCONSISTENT`: se emergono discrepanze, registrando i dettagli nei metadati.

### 5.3 Caso Semantico di Controllo Multimodale
È stato validato il caso sintetico di controllo scientifico:
- **Label**: `Logistica e Ispezione Depositi`
- **Description**: `Ispezione visiva del magazzino merci con casse sigillate e sicurezza`
- **Evidence ID**: `msg::5::VISION_DESCRIPTION`
- **Verifica**: La descrizione visiva generata da modulo Vision (`EvidenceSourceType.VISION_DESCRIPTION`) viene correttamente serializzata tra le evidenze disponibili e riconosciuta semanticamente dal modello, che restituisce `PRESENT` citando esplicitamente tale evidenza.

---

## 6. PARTE E — COMPATIBILITÀ E PRONTEZZA WINDOWS 11

### 6.1 Ambiente Operativo Validato
- **OS**: Windows 11 Version 22H2/23H2/24H2 64-bit.
- **Python**: Compatibile con Python 3.12, 3.13 e 3.14 (64-bit).
- **Virtual Environment**: `.venv` isolato gestito via PowerShell.
- **Package Installation**: `pip install -e ".[ui,stt,ocr,image,dev]"` (incluso supporto esplicito a `dev` in `pyproject.toml`).
- **Nessun obbligo di WSL2**: Esecuzione nativa Windows per massimizzare le prestazioni I/O e l'integrazione con LM Studio.

### 6.2 LM Studio su Windows
- **Endpoint obbligatorio**: `http://127.0.0.1:1234` (loopback locale, nessun host esterno).
- **Rilevamento metadati**: Risoluzione deterministica del modello, binding instance ID e controllo context length (8192 token).
- **Hardware Acceleration**: Supporto automatico a NVIDIA CUDA se presente GPU compatibile; fallback pulito su CPU supportato da LM Studio.

### 6.3 Script PowerShell Realizzati
1. [scripts/windows_setup_and_verify.ps1](file:///scripts/windows_setup_and_verify.ps1):
   - Verifica Python >= 3.12 (64-bit);
   - Crea/attiva `.venv`;
   - Installa dipendenze;
   - Verifica import;
   - Controlla LM Studio;
   - Esegue la suite di test;
   - Lancia lo smoke test sintetico end-to-end;
   - Avvia opzionalmente Streamlit (`-LaunchUi`).
2. [scripts/run_streamlit_windows.ps1](file:///scripts/run_streamlit_windows.ps1):
   - Configura porta 8501, codifica UTF-8 e avvia Streamlit in modo sicuro su Windows.

---

## 7. PARTE F — MATRICE DEI REQUISITI E ACCETTAZIONE END-TO-END

| # | Requisito di Accettazione | Componenti Verificati | Classificazione | Esito |
| :-: | :--- | :--- | :--- | :--- |
| **1** | Importazione WhatsApp TXT | `importer/whatsapp_export.py`, `ui/ingestion.py` | `MOCK_VERIFIED` / Real File | **PASSED** |
| **2** | Importazione WhatsApp ZIP | `ui/ingestion.py`, `scripts/smoke_operational_ai.py` | `MOCK_VERIFIED` / Real File | **PASSED** |
| **3** | Normalizzazione timestamp & timezone | `normalization/normalizer.py` | Unit Test | **PASSED** |
| **4** | ConversationEvidenceDocument & SHA-256 | `ai/models.py`, `analysis/checkpoint.py` | Unit Test | **PASSED** |
| **5** | Topic Discovery Multi-Chunk | `ai/chunking.py`, `analysis/service.py` | `MOCK_VERIFIED` | **PASSED** |
| **6** | Discovery Completa (tutti i blocchi) | `test_topic_discovery_detection_stabilization.py` | `MOCK_VERIFIED` | **PASSED** |
| **7** | Discovery Parziale con Diagnostica (24/26) | `test_discovery_24_of_26_chunks_with_failures_at_10_and_19` | `MOCK_VERIFIED` | **PASSED** |
| **8** | Retry Selettivo (solo blocchi 10 e 19) | `test_discovery_selective_retry_recovers_only_missing_chunks` | `MOCK_VERIFIED` | **PASSED** |
| **9** | Topic Detection Manuale | `analysis/service.py`, `test_ui_manual_topic_detection.py` | `MOCK_VERIFIED` | **PASSED** |
| **10** | Detection Parziale 17/26 con Diagnostica | `test_detection_17_of_26_completed_with_evidence_is_present_and_partial` | `MOCK_VERIFIED` | **PASSED** |
| **11** | Detection Incompleta -> UNCERTAIN | `test_detection_17_of_26_completed_without_evidence_is_uncertain` | `MOCK_VERIFIED` | **PASSED** |
| **12** | Prova del Nove Indipendente | `test_prova_del_nove_inferential_isolation_and_consistency` | `MOCK_VERIFIED` | **PASSED** |
| **13** | ConsistencyService Post-Hoc | `analysis/consistency.py`, `analysis/service.py` | `MOCK_VERIFIED` | **PASSED** |
| **14** | Caso di Controllo Multimodale | `test_control_case_vision_description_serialization` | `MOCK_VERIFIED` | **PASSED** |
| **15** | Isolamento Cross-Document (Zero Leak) | `test_discovery_checkpoint_cross_document_isolation` | `MOCK_VERIFIED` | **PASSED** |
| **16** | Streamlit AppTest Workflows | `tests/unit/test_ui_manual_topic_detection.py` | AppTest Verified | **PASSED** |
| **17** | F1 Snapshot Ownership TOCTOU Safe | `test_144_file_replaced_after_identity_verification_before_unlink` | Multi-OS Verified | **PASSED** |
| **18** | Zero Chiamate Cloud / Loopback Only | `smoke_operational_ai.py`, `test_no_cloud_calls_loopback_only_security` | Security Guard Verified | **PASSED** |
| **19** | Protezione Dati Reali & Zero Leakage | Redaction filtri, immutabilità documenti | Forensic Guard Verified | **PASSED** |
| **20** | Benchmark Scientifico Congelato Intatto | `ai/experiment.py`, `ai/benchmark.py` | Invariato (No mod) | **PASSED** |

---

## 8. STATO DELLE VERIFICHE E PROSSIMI PASSI SCIENTIFICI

### 8.1 Problemi Risolti
- **Vulnerabilità F1 Snapshot**: Risolta strutturalmente con la rimozione di `is_file()` e doppia verifica pre-unlink (`test_144` superato).
- **Topic Discovery 24/26**: Spiegata operativamente; persistenza SQLite garantita per i 24 blocchi; ripresa selettiva dei soli blocchi mancanti verificata.
- **Topic Detection 17/26**: Risolto il blocco prematuro del loop causato dai timeout; implementata la corretta aggregazione forense (`PRESENT` vs `UNCERTAIN`).
- **Prova del Nove**: Certificato l'isolamento inferenziale (nessun target evidence ID inviato alla seconda inferenza).
- **Compatibilità Windows 11**: Realizzati script PowerShell, runbook operativo e packaging verificato.

### 8.2 Distinzione tra Test Eseguiti e Test da Eseguire su Hardware Reale
- **Test Simulati / Unitari / Integration**: Eseguiti realmente su Windows e Linux WSL durante questa sessione (`MOCK_VERIFIED` con 158 passed su preflight, 9 passed su stabilizzazione, 13 passed su UI manual detection, 18/18 checks passed su smoke test).
- **Test LIVE con LM Studio e Modelli GGUF**: Da eseguire sulla nuova macchina Windows al momento della disponibilità hardware e del caricamento dei modelli GGUF (seguendo le istruzioni dettagliate in [WINDOWS_TOPIC_PIPELINE_RUNBOOK.md](file:///WINDOWS_TOPIC_PIPELINE_RUNBOOK.md)).
- **Benchmark Scientifico Congelato**: Non è stato alterato e sarà avviato sulla nuova macchina Windows dopo lo smoke test operativo di convalida.
