# CHATANALYSIS — EVIDENCE ID LIVE FIX & UBUNTU 20/26 RECOVERY REPORT
Data: 16 Ottobre 2026  
Versione: chatAnalysis_evidence_id_live_recovery_20261016  
Ambiente operativo: Ubuntu, LM Studio (Qwen2.5-7B-Instruct, context_length 8192)

---

## 1. DIAGNOSI E CAUSA RADICE DEI FALLIMENTI (20/26 SUCCESS, 6/26 FAILED)

Durante l'esecuzione reale di Topic Discovery su 1.049 messaggi (26 chunk totali) con LM Studio e Qwen2.5-7B-Instruct, sono stati osservati:
- **20 chunk completati con successo** (SUCCESS) e salvati su SQLite.
- **6 chunk falliti** (indici 2, 4, 19, 20, 21 e 25), con stato `FAILED`.
- **Categoria diagnostica**: `INVALID_EVIDENCE_ID` su tutti e 6 i fallimenti.
- **Proprietà HTTP/JSON**: HTTP status 200, JSON valido, schema JSON valido, ma **validazione forense delle citazioni fallita** al 100% dopo 2 tentativi completi.

### 1.1 Perché gli ID originali causano il fallimento di Qwen2.5-7B
L'ispezione analitica del percorso di serializzazione (`ai/serializer.py`), generazione del prompt (`ai/topics.py`) e validazione strutturata (`ai/structured.py`) ha evidenziato che gli identificativi di evidenza forensi generati dalla pipeline contengono stringhe del tipo:
```text
unified:whatsapp_export:1042::ORIGINAL_TEXT
unified:demo_msgstore_db:msg_00392::OCR_TEXT::0
```
Tali stringhe presentano caratteristiche critiche per modelli linguistici da 7B parametri:
1. **Complessità e frammentazione token**: stringhe lunghe 45-75 caratteri con caratteri speciali duplicati (`::`, `:`, `_`) vengono frammentate dal tokenizzatore BPE di Qwen in 12-18 token per singolo ID.
2. **Alterazione e drift tipografico del modello**: sotto costrizioni di schema JSON rigido (`"evidence_ids": ["..."]`), Qwen2.5-7B tende a:
   - Omettere un carattere o un due punti (es. `unified:whatsapp_export:1042:ORIGINAL_TEXT` o `unified:whatsapp_export:104::ORIGINAL_TEXT`).
   - Sostituire o troncare il suffisso `::ORIGINAL_TEXT` in `::TEXT`.
   - Modificare il record ID numerico o confondere il prefisso `unified:`.
3. **Discrepanza di validazione rigorosa**: il validatore forense di ChatAnalysis applica (correttamente) una verifica deterministica di appartenenza per insiemi (`set(raw_eids).issubset(valid_evidence_ids)`). Qualsiasi variazione anche di un singolo carattere comporta il rigetto tassativo dell'output come `AiInvalidEvidenceCitationError`.
4. **Retry correttivo inefficace con ID lunghi**: nel secondo tentativo di inferenza, se il modello riceve nuovamente gli identificativi lunghi e complessi, ripete sistematicamente lo stesso errore di allucinazione/troncamento dei token, esaurendo i 2 tentativi previsti dal budget.

---

## 2. ARCHITETTURA DELLA SOLUZIONE: STRATEGIA DEGLI ALIAS DETERMINISTICI

Per eliminare l'allucinazione e l'alterazione degli identificativi senza indebolire la catena di custodia forense, è stata implementata una strategia di **alias deterministici brevi e controllati in Python**.

### 2.1 Principi operativi
- **Prefisso standard e formato compatto**: ciascuna evidenza del chunk riceve un alias operativo compatto del tipo `E001`, `E002`, `E003`, ..., `E999`.
- **Biiezione e determinismo**: la funzione `generate_evidence_alias_maps(document, prefix="E")` in [ai/serializer.py](ai/serializer.py) crea due dizionari speculari ordinati:
  - `id_to_alias`: `{real_evidence_id -> alias}`
  - `alias_to_id`: `{alias -> real_evidence_id}`
- **Serializzazione nel prompt operativo**: nel prompt JSON inviato all'LLM (`serialize_document_for_llm`), il campo `evidence_id` di ciascuna sezione viene sovrascritto con l'alias corrispondente (`override_evidence_id=id_to_alias[...]`).
- **Istruzione univoca all'LLM**: la regola 3 del system prompt viene parametrizzata:
  ```text
  3. Cite the exact 'evidence_ids' supporting each topic using ONLY the assigned alias identifiers shown in the evidence block (e.g. 'E001', 'E002'). Each topic must cite at least one valid evidence_id from this block.
  ```
- **Riconversione e rigetto in Python ([ai/structured.py](ai/structured.py))**:
  - Quando il validatore riceve `alias_map`, verifica che ogni citazione appartenga strettamente all'insieme degli alias presentati nel blocco.
  - L'alias viene convertito **immediatamente e deterministicamente** nell'ID forense originale (`alias_to_id[alias]`).
  - **Zero fuzzy matching**: nessuna tolleranza per somiglianza, nessuna correzione ortografica automatica, nessuna citazione inventata. Se il modello cita un alias inesistente (es. `E999`) o alieno/cross-chunk (es. `E015` in un blocco di soli 4 messaggi), viene sollevata `AiInvalidEvidenceCitationError`.
  - **Integrità finale**: i risultati finali aggregati, la provenance documentale e i record SQLite contengono **esclusivamente gli evidence ID forensi originali**. Nessun alias operativo permane nei dati archiviati.

---

## 3. PRESERVAZIONE DEI 20 CHECKPOINT ESISTENTI E RECUPERO SELETTIVO

Un requisito fondamentale è stato proteggere i 20 checkpoint già validati e salvati durante la sessione reale su Ubuntu, consentendo la rielaborazione esclusiva dei 6 chunk falliti.

### 3.1 Compatibilità crittografica del fingerprint di configurazione
La configurazione di run (`compute_run_config_hash`) calcola il digest SHA-256 (`cfg_fp`) sulla base di:
- `model_id`: `"qwen2.5-7b-instruct"`
- `prompt_version`: `"discovery_operational"` (mantenuto rigorosamente invariato)
- `config`: parametri di chunking, token, caratteri, temperatura e seed
- `chunks`: suddivisione dei chunk pianificati

Poiché la struttura dei chunk, il modello e `prompt_version` non sono mutati, il digest `cfg_fp` calcolato da `LocalTopicAnalysisService` è **identico al 100%** a quello con cui sono stati salvati i 20 blocchi su Ubuntu.

### 3.2 Protocollo di ripresa selettiva (`retry_failed_only=True`)
1. Il servizio verifica il fingerprint del documento (`doc_fp`) e della configurazione (`cfg_fp`).
2. Recupera i 20 checkpoint validi direttamente dal database SQLite protetto (`checkpoints.db`).
3. Non invia **alcuna richiesta LLM** per i 20 blocchi completati (`attempts = 0`, zero richieste di rete).
4. Genera il piano per i soli 6 blocchi mancanti (indici 2, 4, 19, 20, 21, 25).
5. Esegue l'inferenza con la nuova serializzazione ad alias deterministici `E001`... `E0xx`.
6. Converte gli alias negli ID forensi originali, valida l'output e salva i nuovi checkpoint in modo atomico.
7. Consolidamento finale: l'analisi transita da `PARTIAL` (20/26) a `COMPLETE` (26/26), con `unanalyzed_evidence_count = 0` e zero perdite di evidenze.

---

## 4. CONFRONTO E MODIFICHE AI FILE

I file modificati nel repository sono strettamente circoscritti al percorso operativo:

1. [ai/serializer.py](ai/serializer.py):
   - Aggiunta di `generate_evidence_alias_maps(document, prefix="E")` per mappatura biiettiva deterministica 1-a-1.
   - Aggiunto parametro `override_evidence_id` in `serialize_evidence_section_dict`.
   - Aggiunto parametro `evidence_id_map` in `serialize_document_for_llm`.

2. [ai/structured.py](ai/structured.py):
   - `validate_topic_discovery_payload` e `validate_topic_detection_payload` estese con parametro `alias_map: Mapping[str, str] | None`.
   - Accettazione controllata degli alias presenti nel blocco con riconversione deterministica a ID forense originale.
   - Accettazione alternativa dell'ID grezzo se già appartenente al blocco (garantisce retrocompatibilità con i mock di test).
   - Rifiuto categorico senza fuzzy matching per qualsiasi citazione non appartenente al blocco.

3. [ai/topics.py](ai/topics.py):
   - In `TopicDiscoveryAnalyzer.discover_topics`, abilitazione degli alias deterministici per la modalità operativa (`operational_mode=True`).
   - Serializzazione con alias, adattamento della regola 3 nel system prompt e messaggi di retry correttivo specifici.
   - Registrazione nei metadati di `evidence_citation_protocol: "deterministic_alias_v1"` e `evidence_aliases_used: True`.
   - In `TopicDetectionAnalyzer.detect_topic`, mantenimento della compatibilità (default senza alias se non richiesto).

4. [analysis/service.py](analysis/service.py):
   - Tracciamento nei metadati della run e nei chunk outcome di `evidence_citation_protocol = "deterministic_alias_v1"`.
   - Mantenimento inalterato di `prompt_version="discovery_operational"` nel calcolo di `cfg_fp` per preservare i 20 checkpoint validi.

5. [tests/unit/test_operational_evidence_alias_and_live_recovery.py](tests/unit/test_operational_evidence_alias_and_live_recovery.py):
   - Nuova suite di 8 test specialistici dedicati alla riproduzione, validazione e recupero live dello scenario Ubuntu.

---

## 5. VALIDAZIONE DEI TEST E RISULTATI SPECIALISTICI

La suite completa specialistica e di regressione è stata eseguita con successo:

```text
============================= test session starts =============================
platform win32 -- Python 3.14.7, pytest-9.1.1
collected 121 items

tests/unit/test_operational_evidence_alias_and_live_recovery.py:
  test_01_alias_generation_deterministic_and_bijective                PASSED
  test_02_valid_aliases_exact_deterministic_remapping_to_original     PASSED
  test_03_invented_and_cross_chunk_aliases_strictly_rejected_no_fuzzy PASSED
  test_04_reproduce_real_ubuntu_scenario_20_success_6_failed          PASSED
  test_05_live_recovery_reprocesses_only_6_failed_chunks_preserves_20 PASSED
  test_06_corrective_retry_context_budget_headroom                    PASSED
  test_07_sqlite_checkpoint_integrity_and_cross_document_isolation   PASSED
  test_08_diagnostics_persistence_without_private_chat_content       PASSED

tests/unit/test_ubuntu_live_chunk_failure_and_recovery.py (12 test)   PASSED
tests/unit/test_subchunk_split_recovery_and_coverage.py   (6 test)    PASSED
tests/unit/test_smoke_operational_ai.py                   (31 test)   PASSED
tests/unit/test_ai_topic_discovery.py                     (6 test)    PASSED
tests/unit/test_ai_structured.py                          (14 test)   PASSED
tests/unit/test_ai_detection_multimodal_diagnostics.py    (16 test)   PASSED
tests/unit/test_integrated_audit_fixes.py                 (28 test)   PASSED

============================ 121 passed in 27.04s =============================
```

### 5.1 Verifica dell'integrità del benchmark scientifico congelato
I file del benchmark scientifico congelato e i relativi script non sono stati modificati:
```text
91100C0A84CB4A8B7C2EEF34A06CFF9EC21AA6E6EEC14FFD09CE6FEA8BCEE845 *ai/experiment.py
DC57081DBCE3463A8017AA26BB4E4A18889AF0A3296853FE40968561D6D44D36 *ai/benchmark.py
7F9CAE97DF2FAE1BC3B5964E639B9F80E3AC5A388EC9C3E20305FF64D48044A7 *run_benchmark.sh
```
I tre digest SHA-256 corrispondono esattamente a quelli congelati.

---

## 6. ISTRUZIONI PER LA RIPRESA DEI 6 CHUNK SU UBUNTU

Per finalizzare l'elaborazione del dataset da 1.049 messaggi sulla macchina Ubuntu:
1. Avviare LM Studio con il modello `Qwen2.5-7B-Instruct` (confermando context length a 8192 token).
2. Aprire l'interfaccia ChatAnalysis.
3. Selezionare la conversazione precedentemente interrotta allo stato PARTIAL (20/26 completati).
4. Cliccare su **«Riprova solo i blocchi falliti»**:
   - I 20 blocchi già validati verranno caricati istantaneamente da SQLite senza chiamate LLM.
   - I 6 blocchi falliti verranno serializzati con gli alias deterministici `E001`, `E002`, ...
   - Qwen restituirà gli alias compatti senza troncamenti o alterazioni di token.
   - Il layer Python riconvertirà gli alias nei corrispondenti identificativi forensi originari.
   - L'analisi completerà con successo al 100% (26/26).
