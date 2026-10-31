# CHATANALYSIS — ANALISI TECNICA PER IL CAPITOLO 3 DELLA TESI

**Titolo della Tesi:** ChatAnalysis: Architettura modulare per l'analisi tematica forense e la verifica semantica di flussi conversazionali  
**Capitolo di Riferimento:** Capitolo 3 — *Analisi tematica e verifica della consistenza semantica*  
**Autore:** Luca Sorbi  
**Corso di Laurea:** Tesi Triennale in Informatica  
**Data Documento:** 21 Ottobre 2026  
**Stato di Verifica del Codice:** AUDITED & TEST_VERIFIED (Python 3.12 / 3.14 x64 Windows 11 & Linux)  

---

## 1. INTRODUZIONE E OBIETTIVI METODOLOGICI

Il presente documento costituisce la mappatura tecnica, scientifica e architetturale esaustiva a supporto della stesura del **Capitolo 3** della tesi. Ogni affermazione tecnica, struttura dati, algoritmo e procedura descritta corrisponde fedelmente al codice sorgente effettivamente implementato nel repository ChatAnalysis.

### Tassonomia dello Stato di Verifica

Per garantire l'aderenza scientifica ed evitare affermazioni non verificate, ogni componente viene classificato secondo i seguenti livelli formali:
- **IMPLEMENTED**: Codice implementato e presente nella codebase, con interfacce definite e tipi statici validati.
- **TEST_VERIFIED**: Verificato mediante suite automatizzata di test unitari e di integrazione (Pytest) in ambiente controllato.
- **MOCK_VERIFIED**: Esercitato mediante client inferenziali simulati (`FakeControlledLlmClient`, `ControlledMockClient`) con determinismo al 100% su token, latenze e percorsi di errore.
- **LIVE_VERIFIED**: Validato attraverso inferenza reale verso endpoint locale LM Studio (`http://127.0.0.1:1234/v1`) con modelli quantizzati GGUF (es. Qwen2.5-7B-Instruct, Llama-3.1-8B-Instruct).
- **NOT_VERIFIED**: Funzionalità o percorso non completamente validato da test automatizzati o dipendente da condizioni hardware/runtime non riprodotte.

---

## 2. MODELLI DATI E PIPELINE DELLE EVIDENZE

L'architettura di ChatAnalysis si fonda su una pipeline a stadi progressivi che converte record investigativi eterogenei in documenti canonici ad alta densità probatoria, garantendo l'immutabilità della sorgente e la tracciabilità delle evidenze.

### 2.1 Strutture Dati Fondamentali

```
[RawRecord] (importer/models.py)
      │
      ▼
[NormalizedRecord] (normalization/models.py)
      │
      ▼
[UnifiedMessage] (unified/models.py)
      │
      ▼
[MessageEvidenceBundle] (ai/models.py)
      │
      ▼
[ConversationEvidenceDocument] (ai/models.py)
```

1. **`RawRecord`** (`importer/models.py`)
   - *Stato*: `TEST_VERIFIED`
   - *Responsabilità*: Rappresentazione immutabile del record grezzo estratto dall'origine (Cellebrite XML, WhatsApp txt/zip).
   - *Campi chiave*: `source_name` (str), `source_path` (str), `source_record_id` (str), `record_type` (str), `raw_fields` (dict), `media_reference` (Optional[MediaReference]).
2. **`NormalizedRecord`** (`normalization/models.py`)
   - *Stato*: `TEST_VERIFIED`
   - *Responsabilità*: Rappresentazione normalizzata con timestamp UTC accertato (`TimestampTzStatus`), testo decodificato UTF-8 e tipizzazione canonica (`CanonicalMessageType`).
   - *Campi chiave*: `timestamp` (NormalizedTimestamp), `message_type` (CanonicalMessageType), `text_content` (str), `validation_result` (ValidationResult).
3. **`UnifiedMessage`** (`unified/models.py`)
   - *Stato*: `TEST_VERIFIED`
   - *Responsabilità*: Schema comune di interscambio per tutti i messaggi della chat, collegato bidirezionalmente alla provenance originaria.
   - *Campi chiave*: `message_id` (str, es. `msg_0001`), `source_path` (str), `timestamp` (NormalizedTimestamp), `text_content` (str), `provenance_record` (NormalizedRecord).
4. **`MessageEvidenceBundle`** (`ai/models.py`)
   - *Stato*: `TEST_VERIFIED`
   - *Responsabilità*: Inviluppo probatorio che accorpa un messaggio unificato e le relative sezioni di evidenza testuale e multimodale (`TextEvidenceSection`, `VisionDescriptionSection`, `OcrTextSection`).
   - *Campi chiave*: `message` (UnifiedMessage), `sections` (tuple[EvidenceSection, ...]), `bundle_hash` (str SHA-256).
5. **`ConversationEvidenceDocument`** (`ai/models.py`)
   - *Stato*: `TEST_VERIFIED`
   - *Responsabilità*: Documento forense consolidato, immutabile, passato ai moduli inferenziali e al partizionamento in chunk.
   - *Campi chiave*: `document_id` (str), `bundles` (tuple[MessageEvidenceBundle, ...]), `all_evidence_sections` (property calcolata).
   - *Invariante crittografica*: `compute_document_fingerprint(doc)` produce un digest canonico SHA-256 su tutti i messaggi nell'esatto ordine sequenziale.

---

## 3. CHUNKING E BUDGETING DEI TOKEN

La gestione di conversazioni di grandi dimensioni (oltre i limiti di contesto di modelli linguistici 8k/16k) è demandata al sottosistema di partizionamento (`ai/chunking.py`) e calcolo del budget (`ai/token_budget.py`).

### 3.1 Algoritmo di Suddivisione in Chunk

- **Modulo sorgente**: `ai/chunking.py`
- **Funzione**: `split_document_into_chunks(document, max_chunk_bundles, max_character_budget, max_tokens, tokenizer)`
- **Stato**: `TEST_VERIFIED`
- **Responsabilità**:
  1. Suddivide deterministicamente `ConversationEvidenceDocument` in una sequenza ordinata di sub-documenti (`chunks`).
  2. Rispetta rigorosamente i limiti di budget di caratteri, bundle massimi e token calcolati.
  3. Assegna a ciascun chunk un ID deterministico: `{parent_document_id}::chunk_{idx}`.
  4. Garantisce che nessun bundle o evidenza venga omesso o duplicato tra chunk adiacenti.
  5. *Assenza di overlap automatico*: l'algoritmo non introduce sovrapposizioni (overlap) di messaggi o evidenze tra blocchi adiacenti; la partizione è strettamente disgiunta e contigua, preservando la relazione 1:1 tra evidenze e chunk.
  6. Calcola la partizione e il fingerprint matematico: `compute_chunk_partition_fingerprint(chunks)`.

### 3.2 Dynamic Output Budgeting

- **Modulo sorgente**: `ai/token_budget.py`
- **Funzione**: `calculate_dynamic_output_budget(context_length, input_tokens, max_topics, schema_tokens, safety_margin)`
- **Stato**: `TEST_VERIFIED`
- **Algoritmo**:
  $$\text{Budget Disponibile} = \text{context\_length} - (\text{input\_tokens} + \text{schema\_tokens} + \text{safety\_margin})$$
  $$\text{Max Output} = \min(\text{max\_configured\_tokens}, \max(\text{min\_tokens}, \text{Budget Disponibile}))$$
  Previene errori di troncamento JSON (`response_parse_error`) garantendo un margine di sicurezza statico (`DEFAULT_SAFETY_MARGIN_TOKENS = 256`).

---

## 4. TOPIC DISCOVERY

La Topic Discovery è il processo non supervisionato attraverso cui il sistema individua autonomamente gli argomenti salienti all'interno della conversazione.

### 4.1 Ciclo di Esecuzione

```
Interfaccia / CLI
      │
      ▼
LocalTopicAnalysisService.run_topic_discovery()
      │
      ├─► Profiling & Chunking Plan (split_document_into_chunks)
      │
      ├─► Per ogni Chunk [0..N-1]:
      │     ├─► Generazione Prompt Operativo (ai/topics.py)
      │     ├─► Chiamata LM Studio / Mock Client (chat_completion)
      │     ├─► Parsing e Schema Validation (TOPIC_DISCOVERY_SCHEMA)
      │     ├─► Validazione Referenziale Evidence ID (strictly in chunk)
      │     └─► Persistenza Checkpoint SQLite immediata (save_chunk)
      │
      └─► Consolidamento Multi-Chunk (aggregate_topic_discovery)
            │
            ▼
   TopicDiscoveryResult
```

### 4.2 Dettaglio dei Componenti

- **Servizio Applicativo**: `LocalTopicAnalysisService.run_topic_discovery()` (`analysis/service.py`)
- **Stato**: `TEST_VERIFIED` / `MOCK_VERIFIED` (I test LIVE con demone LM Studio attivo non sono stati eseguiti su Windows durante l'audit; validazione al 100% deterministica su fixture e mock controllati)
- **Input**:
  - `document`: `ConversationEvidenceDocument`
  - `client`: `BaseLlmClient` (es. `LmStudioClient` o mock controllato)
  - `model_name`: Identificativo modello (default: `qwen2.5-7b-instruct`)
  - `strategy`: `AnalysisLanguageStrategy` (`DIRECT_MULTILINGUAL`)
  - `progress_callback`: Callback opzionale per eventi Streamlit/CLI
- **Output**:
  - `TopicDiscoveryResult`: contenente la tupla di `DiscoveredTopic`, `provenance_document_id` e metadati completi.
- **Invarianti di Robustezza**:
  1. *Fail-closed*: Se tutti i chunk falliscono, solleva `OperationalAnalysisFailedError`.
  2. *Stato Parziale*: Se almeno un chunk fallisce ma altri completano, la run è marcata `status="PARTIAL"` con `error_classification="PARTIAL_CHUNK_FAILURE"`.
  3. *Validazione Evidence ID*: Ogni evidence ID citato nel JSON del modello deve appartenere strettamente alle evidenze del chunk analizzato. Evidenze aliene o cross-chunk provocano `AiInvalidEvidenceCitationError` e innescano retry.

### 4.3 Aggregazione Multi-Chunk e Ranking Deterministico

- **Modulo sorgente**: `ai/chunking.py`
- **Funzione**: `aggregate_topic_discovery(chunk_results, parent_document_id, max_topics)`
- **Stato**: `TEST_VERIFIED`
- **Algoritmo (Deterministic Support Ranking)**:
  1. *Normalizzazione morfologica e canonizzazione delle label*: tramite NFKC, casefolding, rimozione punteggiatura e stemming radicale (`canonicalize_topic_label`).
  2. *Fusione delle evidenze*: unione ordinata in $O(1)$ degli `evidence_id` preservando l'ordine di prima apparizione.
  3. *Selezione della descrizione*: conserva la descrizione più dettagliata ed estesa tra i chunk fusi.
  4. *Tracciamento del supporto*: registra l'insieme dei chunk indipendenti che hanno confermato il tema (`supporting_chunks`).
  5. *Ordinamento deterministico* se i topic unici superano `max_topics`:
     - Criterio 1: Supporto dei chunk decrescente (`-len(supporting_chunks)`).
     - Criterio 2: Numero di evidenze uniche decrescente (`-len(evidence_ids)`).
     - Criterio 3: Ordine di prima comparsa nel documento (`first_seen_index`).
     - Criterio 4: Tie-breaker alfabetico stabile sulla chiave normalizzata (`norm_key`).

---

## 5. TOPIC DETECTION

La Topic Detection è il processo mirato di verifica della presenza di uno specifico argomento formulato come query.

### 5.1 Struttura e Decisioni Tematiche

- **Struttura Query**: `TopicQuery` (`ai/models.py`), con `label`, `description`, `is_label_only`.
- **Valori di Decisione (`TopicDecision`)**:
  - `PRESENT`: Argomento attestato e supportato da almeno una evidenza valida citata.
  - `ABSENT`: Argomento non presente, con copertura del 100% dei chunk validati con esito negativo.
  - `UNCERTAIN`: Copertura parziale dei chunk senza alcuna presenza accertata (`PARTIAL`), oppure modello con confidenza insufficiente.
- **Regole Fondamentali di Aggregazione (`aggregate_topic_detection`)**:
  - Se anche un solo chunk restituisce `PRESENT` con evidenze valide, la decisione aggregata globale è `PRESENT`.
  - La decisione `ABSENT` richiede categoricamente che tutti i blocchi pianificati siano stati completati con successo e con verdetto negativo (`completed_count == total_chunks`).
  - Se un chunk fallisce e nessun blocco è `PRESENT`, la decisione degrada cautelativamente a `UNCERTAIN` con stato `PARTIAL`.
  - Se tutti i chunk falliscono, l'operazione abortisce con `OperationalAnalysisFailedError`.

---

## 6. VERIFICA DELLA CONSISTENZA SEMANTICA MEDIANTE INFERENZE DISTINTE

Questa procedura rappresenta il cuore scientifico della tesi (denominata informalmente nelle prime bozze "Prova del Nove"), ed è formalizzata con la denominazione accademica obbligatoria:
**Verifica della consistenza semantica mediante inferenze distinte**.

### 6.1 Fondamento Teorico e Isolamento Inferenziale

La consistenza semantica non equivale alla verità fattuale assoluta, ma certifica che due processi inferenziali indipendenti, eseguiti con prompt e schemi differenti sullo stesso documento, convergono sul medesimo significato semantico.

```
Fase 1: Inferenza Topic Discovery (Non supervisionata)
        Prompt Discovery -> DiscoveredTopic (Label: L, Desc: D, Evidenze: E_disc)
                                │
   ┌────────────────────────────┴────────────────────────────┐
   │ ISOLAMENTO INFERENZIALE RIGOROSO                        │
   │ Vengono passati ESCLUSIVAMENTE: Label (L) e Desc (D)    │
   │ Nessun Evidence ID (E_disc) viene suggerito alla Fase 2  │
   └────────────────────────────┬────────────────────────────┘
                                ▼
Fase 2: Inferenza Topic Detection Indipendente (Supervisionata)
        Prompt Detection -> TopicDetectionResult (Decision: Dec, Evidenze: E_det)
                                │
                                ▼
ConsistencyService.evaluate_consistency(discovery_result, detection_result)
                                │
                                ▼
                       ConsistencyResult
```

### 6.2 Matrice di Consistenza Semantica

| Decisione Detection | Evidenze $E_{det} \cap E_{disc}$ | Verdetto di Consistenza | Significato Scientifico |
|---|---|---|---|
| `PRESENT` | $\ge 1$ evidenza comune | `CONSISTENT` | Convergenza semantica e probatoria piena tra le due inferenze. |
| `PRESENT` | $0$ evidenze comuni | `PARTIALLY_CONSISTENT` | Il tema è confermato, ma le evidenze citate dalle due chiamate divergono. |
| `ABSENT` | Qualsiasi | `DISCREPANCY_ABSENT` | Divergenza netta: la prima inferenza ha estratto il tema, la seconda nega la presenza. |
| `UNCERTAIN` | Qualsiasi | `DISCREPANCY_UNCERTAIN` | Incertezza inferenziale o copertura parziale dovuta a chunk falliti. |

### 6.3 Autenticazione di Run Recuperate da SQLite e Topic Multi-Chunk (Fix A3)

Quando la verifica viene eseguita su una sessione recuperata da database SQLite (`load_persisted_discovery_result`):
- Il servizio esegue `_authenticate_recovered_discovery_run_for_verification()`.
- **Risoluzione della Criticità A3**: La funzione non confronta più erroneamente il topic aggregato con un singolo blocco serializzato, ma deserializza tutti i blocchi validati nel checkpoint e **ricostruisce deterministicamente l'aggregato multi-chunk** tramite `aggregate_topic_discovery()`.
- Verifica:
  1. Corrispondenza biunivoca del document fingerprint (`100% match`).
  2. Identità referenziale di ogni chunk (`validate_checkpoint_evidence_references`).
  3. Corrispondenza delle label, descrizioni e unione deterministica degli `evidence_id`.
  4. Rifiuto categorico (*fail-closed*) se la run o il checkpoint risultano alterati, mancanti o contenenti evidenze aliene.

---

## 7. PERSISTENZA FORENSE, CHECKPOINT E STABILIZZAZIONE F1

### 7.1 Schema Relazionale SQLite dei Checkpoint

Il database locale SQLite memorizza lo stato delle analisi con garanzie ACID:
- `checkpoints`: Testata della sessione (`document_fingerprint`, `config_fingerprint`, `document_id`, `analysis_type`, `model_id`, `prompt_version`, `total_planned_chunks`, `origin`, `run_id`).
- `checkpoint_chunks`: Risultati serializzati per chunk (`chunk_index`, `result_payload`, `status`).
- `checkpoint_chunk_diagnostics`: Diagnostica dettagliata dei tentativi, latenza, token ed errori (`diagnostic_category`).
- `checkpoint_subchunks`: Sotto-blocchi gestiti durante partizionamenti dinamici di emergenza.

### 7.2 Stabilizzazione dell'Ownership dei File Temporanei (Chiusura Definitiva F1)

Nello script di preflight forense `scripts/preflight_checkpoint_resume.py`, la gestione dello snapshot consolidato e del relativo ciclo di vita è stata interamente riprogettata per eliminare ogni vulnerabilità TOCTOU e garantire la preservazione assoluta dei dati concorrenti:
- **Criticità originaria**: La dipendenza da controlli `lstat` pre-unlink e dal solo percorso o prefisso di directory esponeva alla sostituzione concorrente (TOCTOU) e alla rimozione indebita di directory o file sostituiti da terzi allo stesso percorso.
- **Design Definitivo e Garanzie Implementate**:
  1. *Tracking dinamico dell'identità reale del filesystem*: La classe `_SnapshotOwnershipTracker` non si fida del solo percorso, ma registra e verifica dinamicamente la coppia `(st_dev, st_ino / file_index)`. Se una directory o un file viene rinominato o sostituito da un concorrente nello stesso percorso, l'identità decade e la risorsa viene rigettata fail-closed (`is_owned_dir() == False`).
  2. *Pattern di quarantena atomica con tombstone effimero e rollback*: Invece di eseguire `unlink()` diretto sul percorso pubblico, il file viene spostato atomicamente tramite `os.replace()` in un tombstone privato casuale. Se l'identità del file isolato non corrisponde a quella originale (sostituzione concorrente), il file viene IMMEDIATAMENTE ripristinato al percorso originario (rollback), garantendo la preservazione al 100% dei dati del concorrente e restituendo `False` (fail-closed).
  3. *Distinzione tra file temporanei propri e destinazioni esplicite*: Le destinazioni esplicite preesistenti vengono preservate intatte (`FileExistsError`), mentre le directory temporanee private (`0700`) vengono rimosse con `shutil.rmtree` SOLO previa verifica dinamica della concordanza dell'inode originario.
  4. *Suite di test avversari esaustiva*: Verificata mediante 15 regressioni formali dedicate (`test_146`..`test_160`), tutte superate al 100%.

---

## 8. MATRICE DI TRACCIABILITÀ REQUISITI → CODICE → TEST

| Requisito Scientifico / Tecnico | Modulo e Simbolo Sorgente | Test Pytest di Riferimento | Stato di Verifica |
|---|---|---|---|
| Partizionamento Documento in Chunk | `ai/chunking.py::split_document_into_chunks` | `tests/unit/test_ai_chunking.py` | `TEST_VERIFIED` |
| Dynamic Output Budgeting | `ai/token_budget.py::calculate_dynamic_output_budget` | `tests/unit/test_token_budget.py` | `TEST_VERIFIED` |
| Esecuzione Topic Discovery Multi-Chunk | `analysis/service.py::run_topic_discovery` | `tests/unit/test_topic_discovery_detection_stabilization.py` | `TEST_VERIFIED` |
| Aggregazione Deterministica Support Ranking | `ai/chunking.py::aggregate_topic_discovery` | `tests/unit/test_discovery_detection_invariants.py` | `TEST_VERIFIED` |
| Decisioni Topic Detection (Present/Absent/Uncertain) | `analysis/service.py::run_topic_detection` | `tests/unit/test_topic_discovery_detection_stabilization.py` | `TEST_VERIFIED` |
| Isolamento Inferenziale Prova del Nove | `analysis/service.py::verify_discovered_topic` | `tests/unit/test_discovery_provenance_hardening.py` | `TEST_VERIFIED` |
| Autenticazione Topic Aggregato da Più Chunk | `analysis/service.py::_authenticate_recovered_discovery_run_for_verification` | `tests/unit/test_priority1_recovered_discovery_prova_del_nove.py::test_11` | `TEST_VERIFIED` |
| Gestione Discovery Parziale (25/26) | `analysis/service.py::load_persisted_discovery_result` | `tests/unit/test_priority1_recovered_discovery_prova_del_nove.py::test_12` | `TEST_VERIFIED` |
| Prevenzione TOCTOU e Chiusura Ownership F1 | `scripts/preflight_checkpoint_resume.py::_perform_atomic_safe_unlink` | `tests/unit/test_checkpoint_preflight_resume.py::test_146-160` | `TEST_VERIFIED` |
| Immutabilità Database Sorgente, WAL e SHM | `scripts/preflight_checkpoint_resume.py::acquire_forensic_readonly_copy` | `tests/unit/test_checkpoint_preflight_resume.py::test_150,160` | `TEST_VERIFIED` |
| Benchmark Scientifico Congelato (0 diff) | `ai/experiment.py`, `ai/benchmark.py` | `ai/experiment.py`, `ai/benchmark.py` | `STATICALLY_VERIFIED` |

---

## 9. LIMITI METODOLOGICI DELL'IMPLEMENTAZIONE

1. **Assenza di Primitive Kernel Atomiche Universali per Unlink su Inode**:
   Sui filesystem NTFS e POSIX convenzionali, non esiste un'istruzione atomica di sistema operativo per cancellare un file subordinatamente al suo inode senza finestre TOCTOU. La mitigazione si basa interamente sulla segregazione in directory temporanee ad ownership esclusiva (permessi 0700).
2. **Consistenza Semantica vs Correttezza Fattuale**:
   La convergenza tra due inferenze distinte (`CONSISTENT`) prova la robustezza della rappresentazione del modello linguistico, ma non garantisce di per sé che l'argomento discusso dagli interlocutori della chat sia storicamente veritiero.
3. **Dipendenza dalle Prestazioni dell'Hardware Locale**:
   L'esecuzione con modelli 7B/8B quantizzati Q4_K_M richiede almeno 8-12 GB di memoria unificata o VRAM per garantire tempi di risposta operativi (< 5s per chunk).
