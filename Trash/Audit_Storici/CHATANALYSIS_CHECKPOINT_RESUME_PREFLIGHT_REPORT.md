# CHATANALYSIS — RAPPORTO TECNICO E FORENSE SULL'AUDIT FINALE DI PRE-RIPRESA CHECKPOINT
## SAFETY CLOSURE & COMPREHENSIVE INDEPENDENT AUDIT (24/26 CHUNKS)

**Data Documento:** 20 Ottobre 2026  
**Ambiente Sperimentale di Riferimento:** Ubuntu 22.04.5 LTS / Linux generico / LM Studio 0.3.x / Python 3.10+  
**Target Analitico:** Procedura di Topic Discovery — Validazione pre-ripresa 24 checkpoint su 26  
**Verdetto Storico Preservato:** `STRUCTURAL_RESUMPTION_FEASIBLE_MISSING_10_AND_19`  
**Stato Procedurale Attuale:** `RUNTIME_VALIDATION_REQUIRED` (in attesa di esecuzione con binding runtime attivo sul nodo Ubuntu)  
**Esito Suite di Test:** **81/81 TEST SUPERATI (100% PASS)** — Exit Code 0  

---

## 1. RISPOSTA SISTEMATICA AI RILIEVI B1–B6 DELL'AUDIT INDIPENDENTE

### RILIEVO B1: Verifica del Binding LM Studio e Distinzione Caricato vs Disponibile
- **Qual era la causa?**  
  In `scripts/preflight_checkpoint_resume.py`, il metodo `_query_lmstudio_models_readonly()` considerava verificato il binding con il solo esito `MODEL_FOUND` restituito da `GET /v1/models`. Poiché l'endpoint compatibile OpenAI elenca tutti i modelli scaricati su disco anche se non caricati in RAM (`loaded: false`), un modello presente ma inattivo veniva erroneamente promosso a binding verificato. Inoltre, un oggetto `ModelIdentityBinding(is_verified=True)` iniettato nei test non costituiva prova indipendente e veniva confuso con una verifica live.
- **Quale modifica è stata apportata?**  
  1. È stata implementata l'interrogazione prioritaria dell'API REST nativa di LM Studio (`GET /api/v1/models`), con fallback read-only su `GET /v1/models`.
  2. È stata introdotta la distinzione rigorosa dello stato di memoria: il modello è considerato attivo solo se `loaded: true`, `state/status in ('loaded', 'active', 'ready', 'running')` oppure se `loaded_instances` contiene istanze attive. Se `loaded: false` o stato non caricato, viene emesso `MODEL_AVAILABLE_NOT_LOADED`. Se l'endpoint OpenAI non fornisce prove dello stato di caricamento, viene restituito `MODEL_AVAILABLE_UNCONFIRMED_LOADED`.
  3. Quando disponibili, vengono validate quantizzazione effettiva (`extract_quantization_name`) e dimensione del contesto allocata (`extract_instance_context_length`).
  4. Nessuna chat completion, prompt né inferenza viene mai eseguita.
  5. Classificazione tassativa: un binding manuale con `is_verified=True` viene etichettato come `MOCK_VERIFIED`; un'interrogazione reale del demone LM Studio con istanza in RAM viene etichettata come `LIVE_VERIFIED`. In assenza di binding live attivo verificato, il preflight mantiene il verdetto `RUNTIME_VALIDATION_REQUIRED`.
- **Quale test la dimostra?**  
  - `test_28_lmstudio_synthetic_http_loaded_false_fails_safe_resume`: dimostra che una risposta HTTP sintetica con `loaded: false` produce `MODEL_AVAILABLE_NOT_LOADED` e mantiene `RUNTIME_VALIDATION_REQUIRED`; una risposta con `loaded: true` produce `LOADED_INSTANCE_VERIFIED` e promuove lo stato a `LIVE_VERIFIED`.
  - `test_02_runtime_validation_required_when_binding_unverified_declared_only`: dimostra che la sola chiave modello dichiarata senza binding attivo produce `RUNTIME_VALIDATION_REQUIRED`.
- **Livello di Verifica:** `TEST_VERIFIED` (simulazione HTTP controllata read-only e unit test di routing).
- **Limiti Residui:** L'endpoint standard OpenAI `/v1/models` non espone sempre i metadati proprietari di memoria di LM Studio; in tali contesti bare-OpenAI, il sistema adotta l'approccio cautelativo `MODEL_AVAILABLE_UNCONFIRMED_LOADED` imponendo `RUNTIME_VALIDATION_REQUIRED`.

---

### RILIEVO B2: Limite delle Chunk Boundaries e Natura dei Fingerprint Storici
- **Qual era la causa?**  
  In `analysis/checkpoint.py`, `compute_run_config_hash()` include nella composizione dell'hash gli identificativi dei blocchi (`{document_id}::chunk::{i}`), ma non il testo o l'elenco dei singoli bundle per blocco. Di conseguenza, due partizionamenti con lo stesso numero di blocchi e gli stessi identificativi sintetici producono lo stesso `config_fingerprint`. Sebbene `document_fingerprint` garantisca l'invarianza del corpus originario ed `evidence_id` provi il confinamento intra-chunk per i blocchi con topic estratti, SQLite non memorizza le message boundaries per i blocchi a zero topic.
- **Quale modifica è stata apportata?**  
  1. È stata documentata e formalizzata la natura esatta dei fingerprint storici: retrocompatibilità garantita al 100% senza alterare gli hash dei 24 checkpoint esistenti.
  2. È stata aggiunta la funzione pura `compute_chunk_partition_fingerprint(chunks)` per il fingerprinting crittografico completo e versionato del piano di partizionamento per i checkpoint futuri.
  3. Nel preflight (Controllo 5) è stata introdotta una nota tecnica trasparente che separa formalmente la compatibilità configurazionale (budget e parametri) dall'identità dimostrabile della partizione dei blocchi senza topic.
- **Quale test la dimostra?**  
  - `test_15_chunk_boundaries_two_plans_with_same_chunk_count_differ`: dimostra che piani con parametri differenti generano hash discordanti.
  - Verifica statica e test unitari in `tests/unit/test_checkpoint_preflight_resume.py`.
- **Livello di Verifica:** `STATICALLY_VERIFIED` e `TEST_VERIFIED`.
- **Limiti Residui:** Per i 24 checkpoint storici memorizzati su Ubuntu, i blocchi senza evidenze non hanno una prova crittografica indipendente delle delimitazioni interne memorizzata in SQLite; l'invarianza complessiva è garantita dal determinismo dell'ingestion e dalla validazione del documento.

---

### RILIEVO B3: Sanitizzazione End-to-End del Report e Allowlist
- **Qual era la causa?**  
  In `PreflightAuditReport.to_dict()`, mentre `database_path` veniva sanificato, campi complessi come `configuration_details`, `observed_value`, `expected_value`, `reasons` e `resumption_plan` serializzavano direttamente stringhe o dizionari, consentendo l'eventuale transito di percorsi personali, document ID, model key contenenti path locali o canary token. Inoltre, il fallimento della CLI con `--document-id` non trovato stampava in chiaro la stringa utente su stdout.
- **Quale modifica è stata apportata?**  
  1. È stato implementato un motore ricorsivo di deep-sanitization basato su allowlist: `_sanitize_value_deep()` e `_sanitize_string_deep()`.
  2. Vengono mascherati sistematicamente percorsi Windows (`C:\...`), percorsi Unix (`/home/...`, `/tmp/...`), estensioni di file, document ID reali (`[DOC_ID_REDACTED]`), evidence ID reali (`[EVIDENCE_ID_REDACTED]`) e pattern di credenziali/canary (`[CONFIDENTIAL_CANARY_REDACTED]`).
  3. Gli identificativi di modello contenenti percorsi a file `.gguf` o directory personali vengono normalizzati in etichette canoniche o `[MODEL_PATH_REDACTED]`.
  4. Nel ramo CLI relativo a `--document-id`, il messaggio d'errore è stato normalizzato in `Documento richiesto non trovato nell'input. [ERR_DOCUMENT_NOT_FOUND]`, evitando l'eco dell'identificativo.
  5. `CheckResult.to_dict()` e `PreflightAuditReport.to_dict()` passano ogni singolo campo attraverso questo filtro preventivo.
- **Quale test la dimostra?**  
  - `test_24_adversarial_privacy_sanitization_zero_leak`: verifica l'assoluta assenza di percorsi e nomi personali.
  - `test_30_adversarial_canary_across_all_report_fields_and_cli`: inietta token canary in `configuration_details`, `observed_value`, `expected_value`, `reasons`, `resumption_plan` e nell'argomento `--document-id`, provando che nel JSON serializzato, in stdout e in stderr vi sono zero occorrenze dei canary.
- **Livello di Verifica:** `TEST_VERIFIED` (adversarial testing con token canary su tutti i vettori di serializzazione).
- **Limiti Residui:** Nessuno. L'allowlist e il mascheramento regex multilivello coprono tutti i canali di output.

---

### RILIEVO B4: Sanitizzazione dello Script Diagnostico Aggiuntivo
- **Qual era la causa?**  
  Lo script `scripts/diagnose_ubuntu_live_24of26.py` stampava a terminale percorsi assoluti, document_id, chunk_id, stack trace ed error_detail in chiaro, contravvenendo alle policy di forensic privacy.
- **Quale modifica è stata apportata?**  
  1. È stata applicata la sanificazione rigorosa di percorsi con `_sanitize_path`.
  2. I fingerprint SHA-256 non canonici o malformati vengono formattati in modo sicuro con `_format_fp_safe`.
  3. `document_id` reali sono sostituiti da `[DOC_ID_REDACTED]`, i timestamp da `[TIMESTAMP_REDACTED]` e i chunk ID da `chunk_{index}`.
  4. Gli errori grezzi (`error_detail`) sono categorizzati in codici diagnostici stabili `[DIAGNOSTIC_CATEGORY={category}]`.
  5. Sono state preservate integralmente le capacità investigative necessarie a diagnosticare i checkpoint 10 e 19.
- **Quale test la dimostra?**  
  - `tests/unit/test_checkpoint_safe_diagnostics.py` (16 test passati).
  - Test di compilazione ed esecuzione sintetica.
- **Livello di Verifica:** `STATICALLY_VERIFIED` e `TEST_VERIFIED`.
- **Limiti Residui:** Nessuno.

---

### RILIEVO B5: Snapshot Ownership e Protezione delle Risorse
- **Qual era la causa?**  
  In `create_consistent_readonly_snapshot()`, quando veniva passato il parametro opzionale `dest_path`, la funzione assegnava `created_temp_dir = target_file.parent`. Qualora `dest_path` fosse collocato nella medesima cartella del database originale (es. `.checkpoints/snapshot.db`), la cartella genitrice preesistente veniva considerata risorsa posseduta e restituita al chiamante per essere distrutta tramite `shutil.rmtree()`, rischiando la cancellazione del database originale.
- **Quale modifica è stata apportata?**  
  1. Contratto di ownership riscritta e blindata: se `dest_path` è specificato, `created_temp_dir = None`. Nessuna cartella preesistente viene mai restituita come risorsa posseduta.
  2. Le cartelle temporanee private vengono create unicamente se `dest_path` non è specificato (`tempfile.mkdtemp(prefix="chatanalysis_snap_")`).
  3. Anche in caso di eccezione durante il backup (inclusi errori in `sqlite3.connect`), viene cancellato esclusivamente il file `dest_file` creato, mentre la directory e tutti i file fratelli restano intatti.
  4. Nel blocco `finally` della CLI, il cleanup tramite `rmtree` è subordinato alla guardia `if snapshot_cleanup_dir.name.startswith("chatanalysis_snap_")`.
- **Quale test la dimostra?**  
  - `test_29_snapshot_ownership_with_dest_path_never_cleans_parent_or_source`: colloca uno snapshot accanto al database originale; verifica che `created_temp_dir` sia `None`, che il database e i file fratelli rimangano intatti e che, anche simulando un crash durante il backup, la directory genitrice e il DB originale non vengano mai cancellati.
- **Livello di Verifica:** `TEST_VERIFIED`.
- **Limiti Residui:** Nessuno.

---

### RILIEVO B6: Separazione tra Validazione Checkpoint e Dimostrazione del Runtime Skip
- **Qual era la causa?**  
  Il Controllo 9 del preflight validava la presenza e la leggibilità dei record in SQLite (`load_and_validate_checkpoint_readonly`), ma tale controllo statico/read-only non costituiva una dimostrazione empirica che l'algoritmo reale `run_topic_discovery(retry_failed_only=True)` saltasse effettivamente i 24 blocchi inviando unicamente i blocchi 10 e 19 all'inferenza.
- **Quale modifica è stata apportata?**  
  1. Sono state separate concettualmente e formalmente tre proprietà:
     - `CHECKPOINT_READABLE_AND_VALIDATED` (Controllo 9 del preflight in sola lettura);
     - `CHECKPOINT_CONFIG_COMPATIBLE` (Controllo 2 del preflight su hash parametri e binding);
     - `RUNTIME_SKIP_BEHAVIOR_VERIFIED` (dimostrazione con client controllato e tracciamento completo delle chiamate).
  2. Il Controllo 9 è stato rinominato: `Checkpoint Readability & Evidentiary Reconciliation`.
  3. È stata creata una suite di simulazione end-to-end con `ControlledMockClient` che intercetta i prompt generati, verificando che:
     - I 24 blocchi pregressi recuperati da SQLite generano ZERO richieste al modello linguistico;
     - Vengono effettuate esattamente 2 chiamate LLM corrispondenti ai chunk 10 e 19 (messaggi 30–32 e 57–59);
     - Le sessioni estranee e i blocchi consolidati non vengono contaminati;
     - Questo test è tassativamente classificato `MOCK_VERIFIED`, non `LIVE_VERIFIED`.
- **Quale test la dimostra?**  
  - `test_26_mock_selective_resumption_on_synthetic_db`: verifica del flusso di ripresa con 2 chiamate e 24 saltate.
  - `test_32_runtime_skip_behavior_on_resumption_24_chunks_skipped_10_19_inferred`: ispezione granulare dei prompt di inferenza, prova analitica del salto dei blocchi 0–9, 11–18, 20–25 e dell'invio esclusivo dei blocchi 10 e 19.
- **Livello di Verifica:** `MOCK_VERIFIED` (simulazione end-to-end completa con client deterministico).
- **Limiti Residui:** La ripresa reale sul nodo Ubuntu con il demone LM Studio fisico rimane congelata in conformità ai vincoli di release gate.

---

## 2. AUDIT STRUTTURALE SENZA DOCUMENTO (ITEM 7)

Nel percorso in cui il file chat originale non viene fornito (`--db` specificato senza `--chat`):
1. Lo script non si limita a contare le righe in SQLite, ma decodifica ciascun payload JSON con `json.loads` e ne valida la conformità con `deserialize_discovery_result_from_checkpoint()`.
2. Vengono contabilizzati esplicitamente:
   - Chunk totali presenti;
   - Chunk con JSON decodificabile;
   - Chunk con schema TopicDiscoveryResult conforme;
   - Record corrotti o malformati;
   - Subchunk validi e corrotti.
3. Se tutti i payload sono integri, la validazione strutturale è superata, ma la validazione probatoria in memoria (confronto evidenze intra-chunk) viene contrassegnata come `SKIPPED_NO_DOCUMENT`.
4. Il verdetto emesso è tassativamente `RUNTIME_VALIDATION_REQUIRED` (o `STRUCTURAL_ONLY`), impedendo qualsiasi falsa attestazione di idoneità alla ripresa in assenza della chat.

---

## 3. MATRICE COMPLETA DEI 9 CONTROLLI DI PREFLIGHT

| N. | Denominazione Controllo | Proprietà Verificata | Criterio di Accettazione |
|:---:|:---|:---|:---|
| **1** | Document & Document Fingerprint Match | Identità del documento | `document_id` identico e `document_fingerprint` SHA-256 coincidente |
| **2** | Run Configuration Fingerprint & Runtime Binding Match | Compatibilità parametri e binding | `config_fingerprint` coincidente e modello caricato in memoria (non solo disponibile) |
| **3** | Model Identity Match | Modello operativo | `model_id` coincide esattamente con `qwen2.5-7b-instruct` |
| **4** | Prompt Version Match | Versione del prompt | `prompt_version` coincide con `discovery_operational` |
| **5** | Chunking Plan & Token Budget Determinism | Determinismo partizionamento | Budget caratteri, token e messaggi identici (26 blocchi pianificati) |
| **6** | Completed & Missing Chunk Count & Indices | Topologia dei blocchi | Esattamente 24 blocchi completati; indici mancanti zero-based `[10, 19]` |
| **7** | Evidence ID Intra-Chunk Validity | Integrità probatoria | Tutte le evidenze citate appartengono al proprio blocco (zero cross-chunk) |
| **8** | Provenance Document ID Verification | Catena di custodia | Tutti i risultati dichiarano provenance autentica dal documento analizzato |
| **9** | Checkpoint Readability & Evidentiary Reconciliation | Leggibilità read-only e schema | 24 payload validati con successo in memoria senza istanziare lo Store né mutare SQLite |

---

## 4. VERIFICA DEI TEST AUTOMATIZZATI (81/81 PASSED)

L'intera suite di test è stata eseguita sul repository completo:

```
tests/unit/test_checkpoint_preflight_resume.py ...... (32 test) -> PASSED (38.45s)
tests/unit/test_checkpoint_safe_diagnostics.py ....... (16 test) -> PASSED (1.98s)
tests/unit/test_checkpoint_integrity_scalability.py .. (33 test) -> PASSED (15.98s)
---------------------------------------------------------------------------------
TOTALE: 81/81 TEST PASSATI (100% SUCCESSO) — EXIT CODE: 0
```

Tutti i sorgenti necessari alla piena riproducibilità (incluso il package `multimodal`, `analysis`, `ai`, `importer`, `ui`, `core`, `normalization`, `unified`, `validation`) sono inclusi nel bundle di rilascio.

---

## 5. STATO PROCEDURALE E RACCOMANDAZIONI FINALI

1. **RELEASE GATE STATUS:** `READY_FOR_INDEPENDENT_AUDIT_CLOSURE`.
2. **VERDETTO PREFLIGHT SUI DATI REALI:** `RUNTIME_VALIDATION_REQUIRED`.
3. **DIVIETO DI RIPRESA IMMEDIATA:** Nessuna inferenza reale o ripresa dei blocchi 10 e 19 è stata autorizzata né eseguita in questa fase.
4. **INTEGRITÀ DATI UBUNTU:** Il database reale `.checkpoints/checkpoints.db` su Ubuntu è rimasto intatto e non modificato.
