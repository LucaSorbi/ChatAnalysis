# CHATANALYSIS — SPECIFICA DELLO SCHEMA JSON DEI RAPPORTI SPERIMENTALI

**Versione Schema:** `1.1.0`  
**Software:** `ChatAnalysis`  
**Data di Rilascio:** 21 Ottobre 2026  
**Autore:** Luca Sorbi  
**Classificazione di Sicurezza:** Safe by Default / Zero Private Content Leakage / Veridicità Scientifica Certificata  

---

## 1. INTRODUZIONE E PRINCIPI GUIDA

Il presente documento definisce la specifica formale e deterministica dello schema JSON (versione `1.1.0`) impiegato da ChatAnalysis per l'esportazione dei rapporti sperimentali relativi alle analisi di **Topic Detection** e **Topic Discovery**.

A seguito dell'audit indipendente del 21 ottobre 2026, lo schema è governato dai seguenti requisiti vincolanti:

1. **Schema Comune, Deterministico e Versionato ("1.1.0")**:
   Entrambe le analisi (Detection e Discovery) condividono la medesima architettura a sezioni logiche: `schema_version`, `software`, `experiment`, `document`, `model`, `configuration`, `execution`, `coverage`, `result`, `chunks`, `checkpoints`, `validation`, `provenance` e `privacy`.
2. **Veridicità Scientifica e Rifiuto dei Default Fittizi**:
   - I valori assenti, non misurati o non registrati all'atto dell'esecuzione sono serializzati rigorosamente come `null` ("NOT_RECORDED").
   - È vietato l'uso di valori di ripiego spacciati per osservati: nessun modello nominale forzato (`qwen2.5-7b-instruct`), context (`8192`), max_tokens (`512`), timeout (`240.0`), temperature (`0.0`), profilo (`BALANCED`) o contatori 1/1 fittizi.
   - Una durata non misurata deve essere `null`, non `0.0` (il valore `0.0` denota una misurazione reale pari a zero secondi).
3. **Provenienza Fail-Closed e Autenticazione delle Run**:
   - Se manca il metadato `origin`, il report registra `NOT_RECORDED` e `attestation_level = UNATTESTED`.
   - È vietato promuovere una run a `LIVE_AUTHORITATIVE` o dedurla dal nome del modello o dall'assenza di flag mock: l'attestazione `LIVE_AUTHORITATIVE` richiede autenticazione verificata dell'inferenza live.
   - Per recuperi da checkpoint locale, la modalità di caricamento (`loading_mode = CHECKPOINT_RESTORE`) è distinta dall'origine dell'inferenza originaria.
4. **Validazione delle Invarianti di Copertura**:
   - I contatori di blocchi devono rispettare l'invariante: `total_chunks_planned == chunks_completed + chunks_failed + chunks_skipped`.
   - In presenza di anomalie contabili (es. 1 pianificato, 1 completato, 2 falliti), il report registra `counter_invariants_valid = false`, `coverage_status = "INCONSISTENT_COUNTERS"` e non dichiara 100% di completamento né `COMPLETE`.
5. **Privacy Assoluta (*Safe by Default*)**:
   - La modalità sicura pubblicabile (`SAFE_REDACTED`) è il **default applicativo per tutte le esportazioni** (run correnti, storiche e ricerca).
   - Oscuramento di label, descrizioni, motivazioni (`rationale`), `topic_id`, `consistency_rationale` e frammenti di errore.
   - Pseudonimizzazione di tutti gli `evidence_id` e degli identificatori documentali/dataset (anche nei nomi file).
   - Dichiarazione onesta dell'algoritmo di pseudonimizzazione: `SHA256_UNSALTED_TRUNCATED` con avvertenza esplicita sulla pre-immagine dizionariale.
6. **Separazione dell'Identità Software di Esecuzione ed Esportazione**:
   - Distinzione tra `execution_software_commit` (registrato durante l'inferenza storica) ed `export_software_commit` (rilevato da HEAD al momento del download).

---

## 2. STRUTTURA DELLE SEZIONI DELLO SCHEMA

### 2.1 `schema_version` e `software`
- `schema_version` (str): `"1.1.0"`.
- `software`:
  - `name`: `"ChatAnalysis"`
  - `version`: Versione applicativa (es. `"0.1.0"`).
  - `execution_software_commit`: Hash del commit Git al tempo dell'esecuzione o `"NOT_RECORDED"`.
  - `export_software_commit`: Hash del commit Git al momento della generazione dell'export o `"NOT_RECORDED"`.
  - `offline_mode`: Booleano `true` (sistema operante al 100% in locale offline).
  - `environment`: Dizionario con `os`, `os_release`, `python_version`, `architecture` e `mode`.

### 2.2 `experiment`
- `analysis_type` (str): `"TOPIC_DETECTION"` oppure `"TOPIC_DISCOVERY"`.
- `run_id` (str): Identificativo univoco della run o `"NOT_RECORDED"` (nessuna fabbricazione `run_ + id(obj)`).
- `export_id` (str): Identificativo univoco deterministico della specifica operazione di esportazione.
- `origin_flow` (str): Flusso di origine (`"MANUAL_DETECTION"`, `"DISCOVERY_VERIFICATION"`, `"LIVE_DISCOVERY"`).
- `mode` (str): `"DEMO"` oppure `"FILE"`.
- `conversation_pseudonym` (str): Pseudonimo deterministico della conversazione (es. `chat_a1b2c3d4e5f6`).
- `attestation_level` (str): Livello di attestazione forense:
  - `LIVE_AUTHORITATIVE`: Inferenza live verificata ed autenticata su LM Studio locale.
  - `MOCK_VERIFIED`: Esecuzione simulata con client di test (`FakeLocalLlmClient`, mock controllato).
  - `UNATTESTED`: Run priva di prova autenticata di provenienza (fail-closed).
- `loading_mode` (str): `"IN_MEMORY_EXECUTION"` oppure `"CHECKPOINT_RESTORE"`.
- `start_time` / `end_time` (str ISO-8601 o null): Timestamp temporali standardizzati.
- `duration_seconds` (float o null): Latenza totale di esecuzione (null se non misurata).

### 2.3 `document`
- `document_pseudonym` (str): Pseudonimo deterministico stabile del documento (`doc_...`).
- `document_fingerprint` (str): Impronta crittografica del corpus documentale.
- `message_count` (int): Conteggio totale dei messaggi sorgente.
- `total_evidence_count` (int): Totale sezioni di evidenza generate.
- `analyzed_evidence_count` (int o null): Evidenze effettivamente elaborate.
- `unanalyzed_evidence_count` (int o null): Evidenze non analizzate per fallimenti o interruzioni.
- `source_name` (str o null): Identificativo della sorgente (pseudonimizzato in modalità safe, es. `src_...`).

### 2.4 `model`
- `requested_model_id` (str o null): Modello richiesto (null se non registrato).
- `effective_model_id` (str o null): Modello effettivo restituito dal backend.
- `family` (str o null): Famiglia architetturale (`QWEN`, `LLAMA`, `MISTRAL`, `DEEPSEEK`, `UNKNOWN`).
- `declared_quantization` / `observed_quantization` (str o null): Quantizzazione dichiarata e osservata.
- `declared_context_length` / `effective_context_length` (int o null): Dimensione del contesto in token.
- `local_endpoint` (str o null): Endpoint loopback locale (`"http://127.0.0.1:1234"`).

### 2.5 `configuration`
- `temperature` (float o null): Temperatura di campionamento registrata (null se non registrata).
- `max_tokens` (int o null): Token massimi riservati (dinamico per Discovery; null se non registrato).
- `timeout_seconds` (float o null): Timeout di inferenza per chunk.
- `seed` (int o null): Seme deterministico (se supportato dal backend).
- `operational_profile_requested` (str o null): Profilo operativo richiesto (`PRUDENT`, `BALANCED`, `PERFORMANCE`).
- `operational_profile_applied` (str o null): Profilo effettivo applicato.
- `profile_downgraded` (bool): `true` se il runtime ha subito un downgrade automatico per limiti di memoria.
- `reduction_strategy` (str o null): Strategia di riduzione documentale.
- `query_mode` (str, solo Detection): `"LABEL_ONLY"` oppure `"LABEL_WITH_DESCRIPTION"`.
- `max_topics` (int o null, solo Discovery): Numero massimo di topic richiesti per chunk.

### 2.6 `execution`
- `start_timestamp` / `end_timestamp` / `duration_seconds`: Metriche temporali standardizzate (null se assenti).
- `origin` (str): Origine inferenziale registrata o `"NOT_RECORDED"`.
- `global_status` (str): `"COMPLETE"`, `"PARTIAL"`, `"FAILED"`.
- `error_count` (int): Numero di chunk falliti.
- `retry_count` (int): Numero di tentativi addizionali effettuati.
- `subchunks_used` (bool): Indicatore di eventuale suddivisione dinamica dei chunk.

### 2.7 `coverage`
- `total_chunks_planned` (int o null): Numero totale di chunk pianificati.
- `chunks_completed` (int o null): Chunk completati con successo.
- `chunks_failed` (int o null): Chunk falliti.
- `chunks_recovered` (int o null): Chunk recuperati da checkpoint locale.
- `chunks_skipped` (int o null): Chunk non avviati o saltati.
- `counter_invariants_valid` (bool o null): `true` se i contatori sommano coerentemente al piano; `false` se contraddittori.
- `completion_percentage` (float o null): Percentuale completamento blocchi (`0.0 - 100.0`, null se contatori invalidi).
- `is_complete` (bool o null): `true` se e solo se `completed == planned` e `failed == 0` con invarianti valide.
- `coverage_status` (str): `"COMPLETE"`, `"PARTIAL"`, `"FAILED"`, `"INCONSISTENT_COUNTERS"` o `"NOT_RECORDED"`.
- `evidence_coverage_percentage` (float o null): Percentuale evidenze analizzate.

### 2.8 `result`
- **In Topic Detection**:
  - `decision`: `"PRESENT"`, `"ABSENT"`, `"UNCERTAIN"`.
  - `decision_status`: Esito di completezza associato alla decisione (`"COMPLETE"`, `"PARTIAL"`).
  - `decision_completeness_distinction`: Spiegazione esplicita del disaccoppiamento concettuale.
  - `topic`: Oggetto con `topic_id`, `label`, `description`, `is_label_only` (oscurati in modalità safe).
  - `rationale`: Motivazione logica dell'inferenza (oscurata in modalità safe).
  - `evidence_ids`: Lista degli identificativi di evidenza (pseudonimizzati deterministici in modalità safe).
  - `evidence_count`: Conteggio delle evidenze a supporto.
  - `linked_discovery_run_id`: Run ID di discovery correlata.
  - `semantic_consistency`: Dati di correlazione con Topic Discovery (Prova del Nove) selezionati **esclusivamente per detection_run_id esatto**:
    - `origin_flow`, `source_discovery_run_id`, `target_detection_run_id`, `consistency_status`, `consistency_rationale`, `overlap_evidence_ids`, `overlap_count`, `inferential_independence_verified`.
- **In Topic Discovery**:
  - `total_topics_discovered`: Conteggio argomenti unici emersi.
  - `topics`: Lista degli argomenti scoperti con evidenze e descrizioni protette in modalità safe.
  - `aggregation`: Stato di aggregazione multi-chunk, deduplicazione ed errori di fusione.

### 2.9 `chunks`
Lista ordinata di tutti i chunk pianificati (da indice `0` a `N-1`):
- `chunk_index` (int): Indice 0-based.
- `chunk_display_number` (int): Indice 1-based per visualizzazione investigativa.
- `chunk_id` (str): Pseudonimo stabile del blocco.
- `status` (str): `"SUCCESS"`, `"FAILED"`, `"UNATTEMPTED"`, `"RECOVERED_FROM_CHECKPOINT"`.
- `attempts` (int o null): Numero di tentativi (0 se non avviato).
- `duration_seconds` (float o null): Durata dell'elaborazione (null se non misurata o non avviata).
- `model_id` (str o null): Modello impiegato per il chunk.
- `http_status` / `finish_reason` / `prompt_tokens` / `completion_tokens` / `total_tokens`: Telemetria (null se non osservati).
- `json_valid` / `schema_valid` / `evidence_valid` (bool o null): Esiti dei singoli stadi di validazione.
- `error_type` (str o null): Classe dell'eccezione riscontrata.
- `diagnostic_category` (str): Categoria granulare (`NONE`, `TIMEOUT`, `HTTP_ERROR`, `JSON_SCHEMA_ERROR`, `INVALID_EVIDENCE_ID`, ecc.).
- `failure_phase` (str): Stadio del fallimento (`not_started`, `inference`, `structured_parsing`, `evidence_validation`, `completed`).
- `error_detail` (str o null): Dettaglio diagnostico rigorosamente sanitizzato da messaggi privati o percorsi.
- `checkpoint_saved` (bool o null): `true` se il blocco è stato persistito nel database locale SQLite.
- `valid_evidence_count` (int o null): Evidenze valide citate nel blocco.
- `subchunks_used` (bool): Flag di splitting dinamico.

### 2.10 `checkpoints`, `validation`, `provenance`, `privacy`
- `checkpoints`: Stato di abilitazione (`enabled`), `recovered_count`, `saved_count`, pseudonimo store SQLite.
- `validation`: Indicatori di enforcement delle citazioni reali (`evidence_id_validation_applied`, `strict_alias_matching`, `hallucinated_citations_rejected`, `schema_validation_applied`).
- `provenance`: Pseudonimo del documento sorgente, `chain_of_custody_intact` (true solo se verificato), protocollo di evidenza (`deterministic_alias_v1`).
- `privacy`:
  - `privacy_mode`: `"SAFE_REDACTED"` (default vincolante) oppure `"LOCAL_UNREDACTED_CAUTION"`.
  - `text_redaction`: Dichiarazione formale di esclusione messaggi (`messages_included: false`), prompt integrali (`raw_prompts_included: false`), risposte grezze (`raw_llm_responses_included: false`), credenziali e percorsi locali.
  - `pseudonymization_algorithm`: `"SHA256_UNSALTED_TRUNCATED"`.
  - `pseudonymization_notes`: Chiarimento formale sulla stabilità interna e sui limiti contro attacchi a dizionario su domini a bassa entropia.
