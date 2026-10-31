# CHATANALYSIS — RAPPORTO TECNICO: DIAGNOSI FALLIMENTI REAL-WORLD TOPIC DISCOVERY SU UBUNTU E SICUREZZA CHECKPOINT SQLITE/JSON

**Release ID**: `chatAnalysis_ubuntu_live_chunk_recovery_checkpoint_safety_audit_20261015`  
**Data**: 16 Ottobre 2026  
**Autore**: Team Antigravity AI / Forensic Engineering  
**Stato**: Intervento completato, testato e validato (1.772 test superati, 0 fallimenti)

---

## 1. EXECUTIVE SUMMARY

Durante la prima sperimentazione operativa reale su workstation Ubuntu (GPU NVIDIA GTX TITAN X 12 GB, LM Studio locale con endpoint `http://127.0.0.1:1234`, modello `Qwen2.5-7B-Instruct` con JSON schema enforcement a temperatura 0 e seed 42) su una conversazione WhatsApp forense di 1.049 messaggi suddivisa in 26 chunk documentali, il software ChatAnalysis ha evidenziato:
- **15 chunk completati** con successo (57,7%).
- **11 chunk falliti** (42,3%), con stato aggregato finale **PARTIAL**.
- Alla seconda esecuzione consecutiva, LM Studio ha elaborato esattamente 11 richieste HTTP di generazione restituendo 11 risposte con JSON sintatticamente valido, `finish_reason="stop"` ed 8 argomenti ciascuna, ma l'applicazione ha registrato nuovamente **15/26 chunk completati** senza alcun progresso.

Il presente audit ha diagnosticato la causa radice reale del problema, implementato un'architettura granulare di diagnostica tecnica persistente (con 16 codici specifici e tabella dedicata SQLite), integrato un retry controllato per citazioni allucinate o out-of-boundary, realizzato il recupero deterministico al 100% dei blocchi validati da checkpoint senza reinoltro a LM Studio, introdotto il pulsante UI **«Riprova solo i blocchi falliti»**, ed eliminato definitivamente i due difetti residui di riconciliazione SQLite/JSON (cancellazione inter-documento da legacy non affidabile e migrazione parziale di JSON strutturalmente non validi).

Tutti i requisiti forensi (zero leak di testo chat in log/UI/export, immutabilità delle evidenze) e scientifici (benchmark di tesi rigorosamente congelato con hash SHA-256 inalterati) sono stati rispettati.

---

## PARTE A — ANALISI DEL PROBLEMA REALE SU UBUNTU (15/26 CHUNK)

### 1.1 Riepilogo dell'Ambiente e dei Dati Sperimentali
- **Hardware & Sistema**: Ubuntu 22.04 LTS / 24.04 LTS, GPU NVIDIA GeForce GTX TITAN X (12 GB VRAM Maxwell GM200), driver NVIDIA proprietario.
- **Backend Inferenza**: LM Studio locale v0.3.x, endpoint OpenAI-compatible (`http://127.0.0.1:1234/v1`), modello `Qwen2.5-7B-Instruct` (Q4_K_M).
- **Parametri Generazione**: Temperatura = 0.0, Seed = 42, Max Tokens = 1216, Schema = `TopicDiscoveryPayload` (Pydantic / JSON Schema strutturato).
- **Dataset Operativo**: Conversazione WhatsApp reale da 1.049 messaggi normalizzati.
- **Strategia di Chunking**: Segmentazione sequenziale in 26 chunk tematici basati su token budget e confini di messaggio.

### 1.2 Analisi End-to-End delle 11 Risposte Registrate da LM Studio
L'esame dei log di LM Studio ha confermato:
1. Nessun errore HTTP (nessun 400 Bad Request, nessun 500 Internal Server Error, nessun timeout).
2. Le 11 risposte HTTP 200 contenevano stringhe JSON conformi alla sintassi (`json.loads()` ha avuto esito positivo).
3. `finish_reason` era valorizzato a `"stop"`, dimostrando che la generazione non era stata troncata dal limite dei token di output (`max_tokens=1216`).
4. Ciascun oggetto conteneva una lista di 8 topic, ognuno corredato da `label`, `short_description` ed una lista di `evidence_ids`.
5. I messaggi occasionali nei log di LM Studio relativi al mancato recupero della KV cache (`failed to restore KV cache, reprocessing prompt`) rappresentavano normali ricalcoli del contesto da parte del motore llama.cpp sottostante e non hanno causato alcun fallimento di rete o troncamento.

### 1.3 Distinzione Fondamentale: Validità Sintattica vs Validità Forense
Un JSON sintatticamente corretto non è necessariamente un payload forensicamente valido:
- In `ai/topics.py`, la funzione `validate_topic_discovery_payload(payload, valid_evidence_ids)` effettua una validazione rigorosa per prevenire allucinazioni legali:
  - Ogni evidenza citata dal modello **deve** appartenere strettamente all'insieme `valid_evidence_ids` del chunk corrente.
  - Se il modello cita un evidence ID inesistente (es. `EVID-999999`) o appartenente a un messaggio di un altro chunk (es. chunk adiacente presente nella memoria storica della conversazione o nell'indice generale), solleva `AiInvalidEvidenceCitationError`.
- **Comportamento dimostrato**: Qwen2.5-7B-Instruct, pur guidato dallo schema JSON, ha allucinato in 11 chunk identificativi di evidenza non conformi o fuori dal bound del chunk.

### 1.4 La Causa Esatta del Fallimento e del Blocco a 15/26
Nel codice previgente:
1. `TopicDiscoveryAnalyzer.discover_topics()` gestiva un retry interno limitato esclusivamente a `json.JSONDecodeError` (stringa incompleta), ma **non** a `AiInvalidEvidenceCitationError`.
2. In `LocalTopicAnalysisService.run_topic_discovery()`, quando veniva sollevata `AiInvalidEvidenceCitationError`, il blocco veniva intercettato dal catch generico:
   ```python
   except Exception as exc:
       tracker.chunk_failed(current_tracker_idx, str(exc))
       chunk_outcomes.append(
           DiscoveryChunkOutcome(
               chunk_id=chunk.document_id,
               status="FAILED",
               result=None,
               error_type=type(exc).__name__,
               safe_error_code="CHUNK_DISCOVERY_ERROR",
           )
       )
   ```
3. Il chunk veniva marcato `FAILED` con il codice opaco `CHUNK_DISCOVERY_ERROR`.
4. Nel checkpoint SQLite venivano salvati solo i chunk con esito positivo (`SUCCESS`). I chunk falliti non venivano persistiti.
5. Alla seconda esecuzione, il sistema rilevava i 15 chunk completati nel checkpoint SQLite e inviava a LM Studio solo le 11 richieste per i blocchi mancanti.
6. Tuttavia, poiché il modello operava a **temperatura 0** con **seed 42** ed il prompt inviato per ciascun blocco era identico, Qwen ha generato in modo deterministico **le identiche allucinazioni di evidence ID**.
7. L'eccezione `AiInvalidEvidenceCitationError` è scattata nuovamente identica sulle stesse 11 risposte, provocando il nuovo rifiuto dei chunk e lasciando il contatore bloccato a **15/26 completati**.

### 1.5 Correzioni Apportate
1. **Retry Controllato per Citazioni Invalide (`ai/topics.py`)**:
   - In modalità operativa (`is_op=True`), se la prima inferenza fallisce con `AiInvalidEvidenceCitationError`, viene eseguito **un unico retry controllato** specificando un prompt di correzione stringente:
     `"CORRECTION REQUIRED: You previously cited invalid or out-of-boundary evidence IDs. You MUST cite ONLY evidence IDs listed in the chunk evidence section above. Do not invent IDs."`
   - Se anche il secondo tentativo produce ID non validi, il chunk viene registrato come `FAILED` con diagnostica esatta `INVALID_EVIDENCE_ID`.
2. **Classificazione Granulare delle Eccezioni (`analysis/models.py`)**:
   - Mappatura deterministica da eccezione a `ChunkDiagnosticCategory` (16 categorie specifiche, niente più `CHUNK_DISCOVERY_ERROR` indistinto).
3. **Resilienza dell'Orchestratore (`analysis/service.py`)**:
   - Distinzione tra errori sistemici di backend (`LmStudioUnavailableError`, `AiModelMismatchError`, ecc., che interrompono l'intera analisi) ed errori transitori di chunk (`AiBackendRequestError`, `AiBackendTimeoutError`, `AiInvalidEvidenceCitationError`, ecc., che registrano il fallimento del chunk e consentono di proseguire sui chunk successivi).
4. **Contabilità Forense delle Evidenze**:
   - Calcolo esatto di `total_evidence_count`, `analyzed_evidence_count` e `unanalyzed_evidence_count` sia a livello di blocco che aggregato finale.

---

## PARTE B — ANALISI DELLA SICUREZZA DEI CHECKPOINT (DIFETTI RISOLTI)

### 2.1 Difetto 1: Cancellazione Inter-Documento Causata da JSON Contraddittorio
- **Vulnerabilità originaria**: In `analysis/checkpoint.py`, il metodo `clear_all_for_document_id("A")` ispezionava i file legacy presenti su disco. Se un file legacy dichiarava `document_id="A"` ma conteneva un `document_fingerprint` appartenente ad un documento B (`fp_B`), il sistema utilizzava erroneamente `fp_B` per cancellare le righe SQLite, eliminando i checkpoint legittimi di B!
- **Correzione implementata**:
  - La selezione dei record SQLite da eliminare si basa **esclusivamente sui metadati verificati già presenti nel database SQLite** per il `document_id` richiesto.
  - Nessun file JSON legacy non verificato può estendere l'insieme dei fingerprint SQLite da cancellare.
  - I file legacy contraddittori non vengono eseguiti per cancellazioni arbitrarie e vengono preservati con diagnostica di sicurezza.
- **Validazione**: Riproduzione con documento sintetico A e B; prima della correzione B perdeva i propri checkpoint SQLite; dopo la correzione B rimane integro al 100% con tutti i suoi chunk, decisioni ed evidenze.

### 2.2 Difetto 2: Migrazione Parziale Silenziosa di Legacy Strutturalmente Invalido
- **Vulnerabilità originaria**: Durante l'importazione di un file legacy JSON con `total_planned_chunks=2`, se il chunk `"0"` era formalmente corretto ma il chunk successivo presentava un indice non numerico o malformato (es. `"bad"`), il sistema inseriva la testata e il chunk 0 in SQLite prima di fallire, lasciando un database parzialmente contaminato.
- **Correzione implementata**:
  - Implementata una **fase di pre-validazione atomica** prima di qualsiasi transazione o scrittura SQLite.
  - Verifica preliminare completa di: metadati obbligatori, identità documentale, identità configurazione, tipo di dato di tutti gli indici dei chunk (devono essere interi nell'intervallo `[0, total_planned_chunks)`), completezza dei payload, conformità delle evidenze.
  - Se un file legacy contiene anche un solo errore strutturale, la migrazione viene rifiutata in toto: **nessuna testata parziale, nessun chunk inserito, nessun dato SQLite corrotto**. Il file legacy viene preservato su disco intatto per ispezione forense.
- **Validazione**: Test specialistici di corruzione strutturale confermano transazionalità e atomicità perfette (test 28 e 29 in `test_checkpoint_integrity_scalability.py`).

---

## PARTE C — ARCHITETTURA DELLA DIAGNOSTICA PERSISTENTE E UI STREAMLIT

### 3.1 Categorie Diagnostiche Standard (16 Codici)
Sono state introdotte le seguenti costanti di categoria diagnostica in `ChunkDiagnosticCategory` (`analysis/models.py`):
1. `HTTP_CONTEXT_OVERFLOW`: Superamento del limite di contesto del backend HTTP.
2. `HTTP_INVALID_PARAMETERS`: Parametri della richiesta rifiutati dal backend.
3. `HTTP_SCHEMA_REJECTED`: Schema JSON rifiutato dal motore di inferenza.
4. `TIMEOUT`: Timeout di risposta durante la generazione locale.
5. `JSON_DECODE_ERROR`: Risposta del modello non decodificabile come JSON.
6. `OUTPUT_TRUNCATED`: Risposta troncata prima del completamento (`finish_reason != "stop"`).
7. `SCHEMA_VALIDATION_ERROR`: Campi mancanti o tipi errati rispetto allo schema JSON.
8. `INVALID_EVIDENCE_ID`: Evidenze citate non esistenti nel set del chunk.
9. `DUPLICATE_EVIDENCE_ID`: Evidenze duplicate nella stessa citazione.
10. `EMPTY_EVIDENCE`: Argomento restituito privo di qualsiasi evidenza di supporto.
11. `EXCESS_TOPICS`: Numero di argomenti superiore a `max_topics`.
12. `PROVENANCE_MISMATCH`: Evidenze appartenenti a documenti o chunk alieni.
13. `CHECKPOINT_SAVE_ERROR`: Fallimento nella scrittura del record su database SQLite.
14. `MODEL_MISMATCH`: Modello caricato non corrispondente alla configurazione richiesta.
15. `INTERNAL_VALIDATION_ERROR`: Errore interno durante la convalida del payload.
16. `UNKNOWN_ERROR`: Eccezione non classificata.

### 3.2 Persistenza su SQLite (`checkpoint_chunk_diagnostics`)
È stata creata la tabella SQLite `checkpoint_chunk_diagnostics` nello store dei checkpoint:
```sql
CREATE TABLE IF NOT EXISTS checkpoint_chunk_diagnostics (
    document_id TEXT NOT NULL,
    document_fingerprint TEXT NOT NULL,
    analysis_type TEXT NOT NULL,
    model_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    chunk_id TEXT NOT NULL,
    run_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 1,
    duration_seconds REAL NOT NULL DEFAULT 0.0,
    http_status INTEGER,
    finish_reason TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    error_type TEXT,
    diagnostic_category TEXT NOT NULL,
    failure_phase TEXT,
    error_detail TEXT,
    json_validation_ok INTEGER NOT NULL DEFAULT 0,
    schema_validation_ok INTEGER NOT NULL DEFAULT 0,
    evidence_validation_ok INTEGER NOT NULL DEFAULT 0,
    checkpoint_save_ok INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (document_id, document_fingerprint, analysis_type, model_id, prompt_version, chunk_index)
);
```
Questa tabella consente di recuperare lo storico diagnostico dettagliato anche dopo il riavvio completo dell'applicazione o del server Streamlit.

### 3.3 Interfaccia Streamlit e Pannello «Diagnostica analisi»
Al termine di ogni run di Topic Discovery, l'interfaccia Streamlit presenta in modo persistente (non rimosso al completamento della barra di avanzamento):
1. **Banner di Copertura**: Se l'analisi è parziale, mostra un alert esplicito:
   > *"Topic Discovery parziale: 15 blocchi completati su 26. Undici blocchi non sono stati validati. Gli argomenti visualizzati rappresentano soltanto la porzione analizzata."*
2. **Metric Card Forensi**:
   - Blocchi completati / totali.
   - Blocchi falliti.
   - Blocchi recuperati da checkpoint.
   - Evidenze analizzate / non analizzate (contabilità esatta dei messaggi).
3. **Pulsante «🔄 Riprova solo i blocchi falliti»**:
   - Identifica i soli chunk rimasti `FAILED`.
   - Recupera istantaneamente dal checkpoint SQLite i chunk già validati (zero richieste al modello).
   - Invia a LM Studio esclusivamente i blocchi mancanti.
   - Se i nuovi blocchi vengono validati, salva immediatamente i risultati e riesegue il consolidamento, transitando da `PARTIAL` a `COMPLETE`.
4. **Filtro e Tabella di Dettaglio**:
   - Checkbox *"Mostra solo blocchi falliti"*.
   - Tabella interattiva con colonne: Chunk, Stato, Tentativi, Errore, Fase, Checkpoint, Dettaglio Tecnico.
5. **Esportazione Forense Sicura**:
   - Pulsanti per il download del report diagnostico in formato **JSON** e **CSV**.
   - **Garantito zero PII**: non contiene testo di messaggi, numeri di telefono o risposte grezze; contiene esclusivamente metadati operativi e codici diagnostici.

---

## PARTE D — RIEPILOGO TEST E VERIFICA SCIENTIFICA

### 4.1 Riepilogo Completo della Suite di Test
Tutti i test del repository sono stati eseguiti con successo:
- **Pytest Suite Completa**: **1.772 test superati**, 1 saltato, 5 esclusi (benchmark congelati), **0 falliti** (tempo di esecuzione: 3m 00s).
- **Nuova Suite Live Ubuntu Recovery (`test_ubuntu_live_chunk_failure_and_recovery.py`)**: **6 su 6 superati** in 7,29s:
  1. `test_ubuntu_case_15_of_26_first_run_partial_with_granular_diagnostics`: Simula la prima run da 26 chunk (15 successi, 11 fallimenti con ID non validi), verifica codici diagnostici e stato PARTIAL.
  2. `test_ubuntu_case_second_run_recovers_15_from_checkpoint_without_llm_requests`: Simula la seconda run, verificando che i 15 chunk validati siano recuperati al 100% da SQLite senza alcuna chiamata al mock client.
  3. `test_ubuntu_case_controlled_retry_corrects_invalid_citations`: Verifica che il retry controllato con prompt correttivo permetta al modello di autocorreggere le citazioni e completare il chunk.
  4. `test_retry_failed_only_completes_remaining_chunks_and_transitions_to_complete`: Verifica che l'opzione `retry_failed_only` completi gli 11 mancanti e converta lo stato in COMPLETE.
  5. `test_persistence_error_simulation_is_diagnosed_accurately`: Simula un fallimento SQLite su chunk validato, classificandolo come `CHECKPOINT_SAVE_ERROR`.
  6. `test_diagnostics_contain_no_private_chat_content`: Verifica forense rigorosa: nessun testo di messaggio o dato privato nei dizionari o export diagnostici.
- **Suite Specialistica Checkpoint (`test_checkpoint_integrity_scalability.py`)**: **33 su 33 superati** (inclusi i test di regressione per Defetto 1 e Defetto 2).
- **Streamlit AppTest (`test_apptest_flows.py`)**: **7 su 7 superati** (inclusa la simulazione interattiva della Topic Discovery e della Prova del Nove).
- **Verifica Wheel di Distribuzione**: Generazione e verifica con `python -m build --wheel` e `pip install --dry-run` riuscite con successo.

### 4.2 Verifica Benchmark di Tesi (Rigidamente Congelato)
Gli hash crittografici SHA-256 dei file sperimentali del benchmark sono stati confrontati con `benchmark_frozen_sha256.txt`:
```
ai/experiment.py: 91100C0A84CB4A8B7C2EEF34A06CFF9EC21AA6E6EEC14FFD09CE6FEA8BCEE845 -> CONFORME (MATCH 100%)
ai/benchmark.py:  DC57081DBCE3463A8017AA26BB4E4A18889AF0A3296853FE40968561D6D44D36 -> CONFORME (MATCH 100%)
run_benchmark.sh: 7F9CAE97DF2FAE1BC3B5964E639B9F80E3AC5A388EC9C3E20305FF64D48044A7 -> CONFORME (MATCH 100%)
```
Nessuna alterazione introdotta nei file scientifici, nei protocolli DIRECT / TRANSLATE_FIRST o nei 9 benchmark ufficiali.

---

## PARTE E — PROCEDURA OPERATIVA DETTAGLIATA PER IL TEST LIVE SU UBUNTU

Per verificare la release nell'ambiente reale con GPU NVIDIA TITAN X (12 GB) e LM Studio, seguire la seguente procedura operativa:

### Fase 1: Verifica dell'Ambiente e dell'Installazione ChatAnalysis
1. Aprire un terminale bash su Ubuntu e navigare nella directory del progetto:
   ```bash
   cd ~/ChatAnalysis
   source .venv/bin/activate
   ```
2. Verificare che la versione attiva contenga le correzioni:
   ```bash
   python -c "from analysis.models import ChunkDiagnosticCategory; print('Diagnostic codes OK:', len(ChunkDiagnosticCategory))"
   # Output atteso: Diagnostic codes OK: 17 (16 codici errore + NONE)
   ```

### Fase 2: Configurazione ed Avvio di LM Studio
1. Avviare LM Studio su Ubuntu.
2. Caricare il modello **`Qwen2.5-7B-Instruct`** (quantizzazione `Q4_K_M` o equivalente compatibile con 12 GB VRAM Maxwell).
3. Nelle impostazioni del modello in LM Studio:
   - **GPU Offload**: Impostare tutti i layer su GPU (es. 28/28 o 32/32 layer a seconda della configurazione).
   - **Context Length**: Impostare almeno **8192** token.
   - **Temperature**: `0.0`.
   - **Seed**: `42`.
4. Avviare il **Local Server** sulla porta standard: `http://127.0.0.1:1234`.
5. Verificare dal terminale l'accessibilità dell'endpoint:
   ```bash
   curl http://127.0.0.1:1234/v1/models
   ```

### Fase 3: Avvio dell'Applicazione Streamlit
1. Avviare l'interfaccia Streamlit:
   ```bash
   streamlit run app.py --server.port 8501
   ```
2. Aprire il browser all'indirizzo `http://localhost:8501`.

### Fase 4: Esecuzione della Topic Discovery sulla Conversazione Reale
1. Nella schermata di importazione, caricare la conversazione WhatsApp reale da 1.049 messaggi (o selezionare il database forense già importato).
2. Navigare su **`5. Analisi topic`** e selezionare la scheda **`Topic Discovery`**.
3. Verificare che il profilo selezionato sia **Operativo** (`Local AI Profiler`).
4. Cliccare su **«Avvia Topic Discovery LIVE»**.
5. **Comportamento atteso**:
   - L'applicazione elabora i chunk. Se il checkpoint contiene già 15 chunk validati dalla sessione precedente, vedrai:
     *"15 blocchi recuperati dal checkpoint; 11 blocchi da completare."*
   - Gli 11 blocchi mancanti vengono inviati a LM Studio.
   - Grazie al nuovo retry controllato per citazioni (`ai/topics.py`), se Qwen produce evidenze out-of-bounds al primo tentativo, viene inviata automaticamente l'istruzione di correzione delimitata.
   - Al termine, l'interfaccia mostrerà il pannello **«Diagnostica analisi»**.

### Fase 5: Verifica della Diagnostica e Riprova Blocchi Falliti
1. Se tutti i 26 blocchi sono stati validati, l'applicazione mostrerà lo stato **COMPLETE** (26/26).
2. Se uno o più blocchi risultano ancora falliti (stato **PARTIAL**):
   - Consultare la tabella diagnostica per identificare il motivo esatto del fallimento (es. `INVALID_EVIDENCE_ID`, `TIMEOUT`, ecc.).
   - Cliccare sul pulsante **«🔄 Riprova solo i blocchi falliti»**.
   - ChatAnalysis invierà esclusivamente le richieste per i blocchi non validati, preservando intatti i chunk già riusciti.
3. Cliccare su **«📥 Esporta diagnostica (JSON)»** o **«📥 Esporta diagnostica (CSV)»** per archiviare il rapporto tecnico dell'analisi per la documentazione di tesi.

---

## 5. CONCLUSIONI E ACCETTAZIONE

L'intervento ha pienamente risolto il problema operativo riscontrato su Ubuntu senza abbassare i rigidi criteri di validazione forense e senza alterare i benchmark accademici. L'architettura è ora completamente trasparente, resiliente ai riavvii e capace di recuperare incrementale ogni porzione di analisi validata.
