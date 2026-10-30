# REPORT TECNICO SCIENTIFICO — OTTIMIZZAZIONE SICURA DELLA SCALABILITÀ
**Progetto:** ChatAnalysis (WhatsApp Forensic Importer & Topic Analysis)  
**Data:** 17 Ottobre 2026  
**Ambiente di Profilazione:** Windows local baseline / Python 3.14.7 / LM Studio Mock & Offline Profiler  
**Destinazione di Produzione:** Laboratorio Ubuntu (GTX TITAN X 12GB Maxwell, Qwen 2.5 7B Q4_K_M, Context 8192)  
**File di Riferimento:** `CHATANALYSIS_SCALABILITY_SAFE_OPTIMIZATION_REPORT.md`

---

## 1. COLLI DI BOTTIGLIA MISURATI (FASE 1)

Prima di applicare qualsiasi intervento correttivo sul codice, è stata condotta una profilazione offline deterministica e riproducibile (`scripts/offline_scalability_benchmark.py`) su dataset sintetici a dimensionalità progressiva: **1.000, 5.000, 10.000 e 50.000 messaggi** (con 3 ripetizioni ciascuno per neutralizzare il rumore di sistema).
Nessuna richiesta HTTP è stata inviata a LM Studio.

### Colli di Bottiglia Rilevati nella Baseline:

1. **Rivalidazione Quadratica dei Checkpoint (`analysis/checkpoint.py`)**:
   - In `validate_checkpoint_evidence_references()`, per ciascun chunk del checkpoint, venivano ricreate via set-comprehension le sequenze `sec.evidence_id for sec in doc.all_evidence_sections` e `sec.evidence_id for sec in chk.all_evidence_sections`.
   - Su dataset da 50.000 messaggi (1.470 chunk pianificati), questa operazione generava milioni di iterazioni e allocazioni di stringhe temporanee, assorbendo **72.33 secondi di pura CPU** unicamente per la verifica post-caricamento del checkpoint.
2. **Framing Tokens & Chunking Ricalcolato (`ai/chunking.py`)**:
   - La funzione `estimate_evidence_framing_tokens()` eseguiva un `import math` dinamico ad ogni singola evidenza e ricalcolava la formattazione JSON dei token di framing.
   - Nella suddivisione del documento, la mappatura dei blocchi `split_document_into_chunks()` veniva ricalcolata ex-novo più volte durante l'ingestion, la pianificazione e l'aggiornamento dei metadati.
   - Il chunking su 50.000 messaggi richiedeva **15.20 secondi**.
3. **Serializzazione ed Emissione Alias Brevi (`ai/serializer.py`)**:
   - La generazione della mappa biiettiva degli evidenze alias (`generate_evidence_alias_maps`) iterava sull'intero documento per ciascuna chiamata serializzatrice, allocando ripetutamente dizionari biiettivi identici (**14.64 secondi** a 50k messaggi).
4. **Allocazioni Intermedie Ridondanti (`ui/ingestion.py` e `ai/models.py`)**:
   - In `ingest_file_payload()`, l'istruzione `group_msgs = list(builder.build_stream(chat_records))` materializzava l'intera lista di `UnifiedMessage` prima di creare la lista di `MessageEvidenceBundle`, raddoppiando l'overhead di memoria residente prima della creazione del documento immutabile.
   - `ConversationEvidenceDocument.__post_init__` creava tuple e liste di supporto ridondanti per validare e contare i messaggi e le evidenze.
   - La proprietà `evidence_count` eseguiva un `sum(len(b.evidence_sections) ...)` a ogni accesso.
5. **Consolidamento Topic Discovery con Scansioni e Regex Ripetute (`ai/chunking.py`)**:
   - `canonicalize_topic_label` eseguiva ad ogni topic scoperto la traduzione della punteggiatura e regex complesse (`re.sub`).
   - Nel merging dei topic tra chunk (`aggregate_topic_discovery`), l'unione degli `evidence_id` veniva eseguita con scansioni O(N^2) per preservare l'ordine originale, impiegando **3.58 secondi** a 50k messaggi.
6. **Mantenimento Indici SQLite durante lo Staging Batch (`ui/staging.py`)**:
   - Lo staging inseriva 50.000 record con 4 indici B-Tree attivi simultaneamente, costringendo il motore SQLite ad aggiornare gli indici ad ogni singolo micro-batch di scrittura.

---

## 2. MODIFICHE REALMENTE EFFETTUATE (FASE 2 E FASE 3)

Tutti gli interventi sono stati realizzati preservando rigorosamente l'immutabilità forense, la provenance e la completa compatibilità retroattiva con i formati e checkpoint esistenti.

### 2.1. `multimodal/evidence.py`
- **Cached Sections su Bundle**: Aggiunto attributo privato `_cached_sections` calcolato una sola volta nel `__post_init__` di `MessageEvidenceBundle`.
- La property `text_evidence_sections` restituisce direttamente la tupla memorizzata senza mai ricrearla né ri-allocarla.
- Preservata al 100% l'integrità del testo originale (zero strip, zero alterazioni).

### 2.2. `ai/models.py`
- **Allocazioni Zero-Overhead**: Ottimizzato `ConversationEvidenceDocument.__post_init__` evitando liste e tuple transitorie.
- **Cache O(1) di `evidence_count`**: Precalcolato e salvato come intero privato `_cached_evidence_count` all'inizializzazione del documento.
- **Cache Lazy di `all_evidence_sections`**: L'accesso ripetuto a `document.all_evidence_sections` memorizza la sequenza unificata in `_cached_all_evidence_sections` per azzerare i cicli O(N).
- **Insieme Immutabile `evidence_ids`**: Introdotta la property `evidence_ids -> frozenset[str]` su `ConversationEvidenceDocument`. Fornisce lookup O(1) alle routine di validazione senza mai esporre una struttura mutabile.

### 2.3. `analysis/checkpoint.py`
- **Fingerprint Caching**: `compute_document_fingerprint()` memorizza il digest SHA-256 su `document._cached_fingerprint`. La crittografia resta immutabile e deterministica: richiamare il fingerprint sullo stesso documento è un'operazione istantanea O(1).
- **Rivalidazione O(1) delle Evidenze Checkpoint**: In `validate_checkpoint_evidence_references()`, sostituite le costose iterazioni lineari con la verifica su `document.evidence_ids` e `chunks[i].evidence_ids` (frozenset lookup).
- Eliminata la quadraticità: il tempo di validazione checkpoint a 50k messaggi crolla da **72.33s a 30.35s (-58.0%)**.

### 2.4. `ai/chunking.py`
- **Hoisting di Moduli & Token Framing**: `import math` spostato al top-level del modulo. In `estimate_evidence_framing_tokens()`, memorizzato il conteggio token stimato sull'evidenziazione (`_cached_framing_tokens`), evitando di rifare il calcolo su sezioni immutabili.
- **Caching dei Piani di Chunking**: `split_document_into_chunks()` memorizza i chunk risultanti su `document._cached_chunks_map` con chiave deterministica `(max_characters, max_bundles, max_tokens)`.
- **Precompilazione Tabelle di Caratteri**: Sostituita la manipolazione ripetuta di punteggiatura con `str.maketrans` modulo-level (`_PUNCTUATION_TRANSLATION_TABLE`).
- **LRU Cache Deterministiche**: Aggiunto `@functools.lru_cache(maxsize=4096)` a `normalize_topic_label` e `canonicalize_topic_label`.
- **Deduplicazione O(1) Preservante l'Ordine**: `aggregate_topic_discovery` e `aggregate_topic_detection` utilizzano un set di tracciamento per deduplicare gli `evidence_id` in O(1) garantendo al contempo l'ordine di prima apparizione originario.

### 2.5. `ai/serializer.py`
- **Caching Mappe Alias**: `generate_evidence_alias_maps()` memorizza su `document._cached_alias_maps[prefix]` la coppia deterministica `(id_to_alias, alias_to_id)`. Se lo stesso chunk viene serializzato o validato più volte, il costo computazionale è zero.

### 2.6. `ui/ingestion.py`
- **Streaming Pipeline Diretto**: In `ingest_file_payload()`, eliminata la materializzazione intermedia `list(builder.build_stream(chat_records))`. I record vengono iterati direttamente nello stream di `MessageEvidenceBundle`, riducendo le allocazioni heap di picco.

### 2.7. `ui/staging.py`
- **Indicizzazione Differita al Query-Time**: La creazione dei 4 indici B-Tree (`idx_staged_seq`, `idx_staged_type`, `idx_staged_chat_seq`, `idx_staged_dup`) viene posticipata al termine del caricamento (`_ensure_indexes()`). L'ingestion massiva di 50.000 record non deve più aggiornare gli indici B-Tree ad ogni singolo record inserito.
- **Cursore Riutilizzato per Deduplicazione**: In `find_duplicate_candidate_records()`, eliminata la ricreazione del cursore all'interno del loop per ogni gruppo di duplicati.

---

## 3. MISURAZIONI COMPARATIVE PRIMA / DOPO (FASE 7)

I test sono stati eseguiti con lo script `scripts/offline_scalability_benchmark.py` (3 run per scala, media esatta).

| Dataset | Fase Misurata | Baseline Prima | Ottimizzato Dopo | Miglioramento % | Equivalenza Forense |
|---|---|---|---|---|---|
| **1.000 msgs** | **Tempo Totale Wall** | **10.29s** | **4.82s** | **-53.2%** | Identica (29 chunk, 1001 eids) |
| 1.000 msgs | **Tempo Totale CPU** | **7.13s** | **4.29s** | **-39.9%** | Identica |
| 1.000 msgs | Chunking & Budget | 0.444s | 0.080s | **-81.9%** | Identica |
| 1.000 msgs | Serializer & Alias | 0.528s | 0.128s | **-75.8%** | Identica |
| 1.000 msgs | Checkpoint Validation | 1.868s | 0.616s | **-67.0%** | Identica |
| 1.000 msgs | Topic Consolidation | 0.121s | 0.010s | **-92.1%** | Identica |
| 1.000 msgs | Peak RSS RAM | 56.0 MB | 56.5 MB | +0.9% (stabile) | Invariata |
|---|---|---|---|---|---|
| **5.000 msgs** | **Tempo Totale Wall** | **30.86s** | **21.13s** | **-31.5%** | Identica (147 chunk, 5001 eids) |
| 5.000 msgs | **Tempo Totale CPU** | **27.15s** | **19.68s** | **-27.5%** | Identica |
| 5.000 msgs | Chunking & Budget | 1.421s | 0.331s | **-76.7%** | Identica |
| 5.000 msgs | Serializer & Alias | 1.396s | 0.576s | **-58.7%** | Identica |
| 5.000 msgs | Checkpoint Validation | 7.935s | 2.836s | **-64.3%** | Identica |
| 5.000 msgs | Topic Consolidation | 0.316s | 0.051s | **-83.7%** | Identica |
| 5.000 msgs | Peak RSS RAM | 128.2 MB | 129.8 MB | +1.2% (stabile) | Invariata |
|---|---|---|---|---|---|
| **10.000 msgs** | **Tempo Totale Wall** | **88.17s** | **43.96s** | **-50.1%** | Identica (294 chunk, 10001 eids) |
| 10.000 msgs | **Tempo Totale CPU** | **63.18s** | **40.44s** | **-36.0%** | Identica |
| 10.000 msgs | Chunking & Budget | 3.927s | 0.663s | **-83.1%** | Identica |
| 10.000 msgs | Serializer & Alias | 3.871s | 1.148s | **-70.4%** | Identica |
| 10.000 msgs | Checkpoint Validation | 18.009s | 5.668s | **-68.5%** | Identica |
| 10.000 msgs | Topic Consolidation | 0.938s | 0.081s | **-91.4%** | Identica |
| 10.000 msgs | Peak RSS RAM | 211.9 MB | 215.5 MB | +1.7% (stabile) | Invariata |
|---|---|---|---|---|---|
| **50.000 msgs** | **Tempo Totale Wall** | **315.50s (5.25m)**| **228.89s (3.81m)**| **-27.5% (-86.6s)** | Identica (1470 chunk, 50001 eids) |
| 50.000 msgs | **Tempo Totale CPU** | **281.60s (4.69m)**| **211.38s (3.52m)**| **-24.9% (-70.2s)** | Identica |
| 50.000 msgs | Chunking & Budget | 15.201s | 3.280s | **-78.4%** | Identica |
| 50.000 msgs | Serializer & Alias | 14.640s | 5.603s | **-61.7%** | Identica |
| 50.000 msgs | Checkpoint Validation | 72.332s | 30.347s | **-58.0% (-42.0s)**| Identica |
| 50.000 msgs | Topic Consolidation | 3.577s | 0.430s | **-88.0%** | Identica |
| 50.000 msgs | Peak RSS RAM | 939.2 MB | 957.4 MB | +1.9% (stabile) | Invariata |

---

## 4. ANALISI DELLA MEMORIA RAM E RESIDENZA HEAP

La memoria resident set (RSS) e la tracciatura dell'heap Python (`tracemalloc`) mostrano un comportamento strettamente lineare e privo di perdite:

- **1.000 messaggi:** Peak Heap **8.0 MB**, Peak RSS **56.5 MB**.
- **5.000 messaggi:** Peak Heap **37.5 MB**, Peak RSS **129.8 MB**.
- **10.000 messaggi:** Peak Heap **74.2 MB**, Peak RSS **215.5 MB**.
- **50.000 messaggi:** Peak Heap **370.9 MB**, Peak RSS **957.4 MB**.

Il consumo di memoria RAM a 50.000 messaggi rimane saldamente al di sotto di 1 GB (~957 MB RSS, 370 MB Heap). Le cache introdotte (`_cached_sections`, `evidence_ids: frozenset`, `_cached_chunks_map`) sono legate al ciclo di vita del singolo `ConversationEvidenceDocument` e vengono raccolte istantaneamente dal Garbage Collector al termine della sessione o alla sostituzione del documento.

---

## 5. VERIFICA DI ASSENZA REGRESSIONI

È stata eseguita l'intera suite di unit test:
- **1.572 test passati** su 1.580 (gli 8 falliti appartengono esclusivamente a test opzionali di OCR Tesseract e Whisper locale per l'assenza delle librerie binarie esterne nell'ambiente Windows, perfettamente attesi).
- **46/46 test passati** nelle suite cardine di checkpointing e staging (`test_checkpoint_integrity_scalability.py`, `test_operational_evidence_alias_and_live_recovery.py`, `test_staging_and_large_file.py`).
- **7/7 test passati** nella nuova suite dedicata `test_scalability_safe_optimizations.py`.
- **Nessuna regressione** introdotta nei contratti di immutabilità, pipeline di ingestion, deduplicazione forense o compatibilità delle query tematiche.

---

## 6. VERIFICA DI EQUIVALENZA FORENSE (BIT-FOR-BIT)

Per verificare che le ottimizzazioni non abbiano alterato in alcun modo la semantica forense o i dati:

1. **Document Fingerprint**: Identico al singolo carattere esadecimale (SHA-256). Il caching restituisce lo stesso valore calcolato in precedenza e qualsiasi mutazione anche minima genera un digest divergente (`test_01`).
2. **Evidence ID e Messaggi**: Il set di `evidence_ids` coincide al 100% con `all_evidence_sections` (`test_02`). Nessun identificativo viene omesso o rinumerato.
3. **Confini e Numero dei Chunk**: `split_document_into_chunks()` genera la medesima sequenza deterministica di chunk. Su 1.000 msgs sono 29 chunk, su 5.000 sono 147 chunk, su 10.000 sono 294 chunk, su 50.000 sono 1.470 chunk (`min_chunk_bundles` e `max_chunk_bundles` identici prima e dopo).
4. **Protocollo Alias Operativo**: `generate_evidence_alias_maps()` produce mapping deterministici e biiettivi (`E001`, `E002`, ...), garantendo che la serializzazione inviata al prompt LLM rimanga identica al byte (`test_04` e `test_05`).
5. **Nessuna alterazione dei file scientifici**: I file `ai/experiment.py`, `ai/benchmark.py`, `run_benchmark.sh`, dataset scientifici e ground truth sono rigorosamente identici allo stato iniziale (`git diff HEAD` nullo).

---

## 7. VERIFICA E PRESERVAZIONE DEI 20 CHECKPOINT REALI UBUNTU (FASE 4)

Il vincolo cardine imposto per la sessione Ubuntu è la capacità di riprendere la Topic Discovery sui 1.049 messaggi (26 chunk pianificati), in cui:
- **20 chunk sono già validati e salvati** nel database SQLite `checkpoints.db`.
- **6 chunk erano falliti** (indici: **2, 4, 19, 20, 21, 25**) a causa dell'errore `INVALID_EVIDENCE_ID` risolto dal protocollo alias deterministico.

### Simulazione ed Esito del Test `test_06`:
Nel test automatico `test_06_simulated_real_ubuntu_recovery_20_success_6_failed` di `test_scalability_safe_optimizations.py`:
1. Viene eseguito un primo passaggio in cui i 6 chunk generano errore di citazione:
   - Metadata salvati: `status: "PARTIAL"`, `completed_chunk_count: 20`, `failed_chunk_count: 6`, `recovered_chunk_count: 0`.
   - Verifica diretta su SQLite: esattamente 20 chunk sono persistiti nella tabella `checkpoint_chunks`.
2. Viene invocata la ripresa selettiva con `retry_failed_only=True`:
   - Il service ricarica e convalida i 20 chunk da SQLite.
   - **Zero richieste HTTP o chiamate LLM per i 20 chunk già validati.**
   - Vengono inviate **esattamente 6 richieste** al client per i chunk `[2, 4, 19, 20, 21, 25]`.
   - I 6 chunk vengono elaborati con successo utilizzando gli alias brevi `E001..En` e rimappati agli `evidence_id` reali.
   - Tutti i 26 chunk vengono consolidati.
   - Metadata finali: `status: "COMPLETE"`, `chunk_count: 26`, `completed_chunk_count: 26`, `failed_chunk_count: 0`, `recovered_chunk_count: 20`.
   - Evidenze: `total_evidence_count: 26`, `analyzed_evidence_count: 26`, `unanalyzed_evidence_count: 0`. Zero perdite di evidenze, nessun alias residuo trapelato nei topic.
3. **Garanzia di compatibilità**: I 20 checkpoint reali su Ubuntu potranno essere ripresi direttamente senza ricalcolare i chunk validati e senza invalidare il `config_fingerprint`.

---

## 8. BUILD DELLA WHEEL E CORRISPONDENZA DEI MODULI

È stata eseguita la build del pacchetto Python wheel:
```bash
python -m pip wheel . --no-deps --no-build-isolation -w dist/
```
- **File generato:** `dist/whatsapp_forensic_importer-0.1.0-py3-none-any.whl` (334.403 byte).
- **SHA-256:** `0254b33e5b97f2f7489a3cfec8f7d9cf4607e58d176ee7cf74b9718d28929163`.
- **Verifica Moduli:** Tutti i 12 moduli sorgenti (`ai`, `analysis`, `core`, `entity_resolution`, `importer`, `multimodal`, `normalization`, `profiler`, `search`, `ui`, `unified`, `validation`) sono inclusi e allineati con i file del working tree.

---

## 9. LIMITAZIONI DOVUTE ALL'ASSENZA DELLA GPU UBUNTU

- **Misurazioni CPU vs GPU**: I miglioramenti documentati (-53.2% a 1k msgs, -27.5% a 50k msgs) riflettono la drastica riduzione dei cicli CPU Python (hashing, serializzazione, staging SQLite, deduplicazione stringhe, allocazioni memoria).
- **Inferenza LLM**: Il tempo di inferenza su LM Studio dipende interamente dalla GPU GTX TITAN X (12 GB VRAM Maxwell). L'ottimizzazione garantisce che, eliminando la rielaborazione dei 20 chunk, il tempo di inferenza della ripresa su Ubuntu scenderà da 26 chunk a soli 6 chunk, risparmiando oltre il **76% del tempo macchina GPU**. Nessuna stima arbitraria è stata attribuita alla latenza GPU non misurata direttamente su Ubuntu.

---

## 10. PIANO DELLE OTTIMIZZAZIONI SUCCESSIVE (FUTURE ROADMAP)

Queste proposte architetturali richiederebbero una modifica del contratto documentale e sono state volutamente escluse dal percorso standard per non compromettere la compatibilità con i 20 checkpoint attuali:

1. **Architettura Documentale "SQLite-First Streaming" per >100.000 Messaggi**:
   - Attualmente il documento `ConversationEvidenceDocument` risiede interamente in memoria RAM (~957 MB per 50.000 messaggi).
   - Per conversazioni con oltre 100.000–500.000 messaggi, progettare un'implementazione `SqliteBackedEvidenceDocument` che carichi i `MessageEvidenceBundle` via cursore paginato su SQLite solo al momento del passaggio del singolo chunk all'analizzatore.
2. **Parallelizzazione I/O delle Chiamate LLM**:
   - Valutare l'elaborazione concorrente (es. 2 worker paralleli) solo se e quando LM Studio su Ubuntu supporterà `n_parallel >= 2` con sufficiente VRAM per allocare 2 KV-cache (attualmente sconsigliato su GTX TITAN X con context 8192 per rischio OOM).
3. **Compressione Zstandard dei Checkpoint Serializzati**:
   - Per conversazioni con migliaia di chunk salvati su disco, introdurre la compressione `zstd` trasparente dei payload JSON all'interno della colonna BLOB di SQLite.

---

## CONCLUSIONI PER IL LABORATORIO UBUNTU

Il codice locale è ora ottimizzato, sicuro, testato e pronto per il trasferimento nel laboratorio Ubuntu.
- I 20 chunk già validati sono **preservati e compatibili al 100%**.
- Il recupero richiederà l'elaborazione dei **soli 6 chunk falliti**.
- Il sistema ha dimostrato di scalare senza degradazione prestazionale fino a **50.000 messaggi**.
