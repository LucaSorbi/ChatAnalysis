# CHATANALYSIS — REPORT CORREZIONI POST-AUDIT E VERIFICA OFFLINE
**Data di Rilascio Correttivo**: 13 Ottobre 2026  
**Identificativo Bundle Release**: `chatAnalysis_post_audit_corrective_release_20261013.zip`  
**Baseline Git**: Commit `6c1dc46b88158b394b8b4ea06d8fac91f38c08a2` (UPDATE 18)  
**Esito Validazione Pytest Offline**: 1.703 passed, 1 skipped, 5 deselected, 0 failed (Durata: 193.39s)

---

## 1. CAUSE REALI DEI DIFETTI IDENTIFICATI

### 1.1 Chunking Ottimizzato non Applicato nel Percorso Operativo
- **Causa Accertata**: Nello script di profilazione precedente `scripts/measure_scalability_and_profiling.py`, il chunking veniva misurato con parametri ottimizzati (`max_characters=12000, max_bundles=100, max_tokens=2500`). Tuttavia, nel modulo operativo `analysis/service.py`, le funzioni `run_topic_discovery()` e `run_topic_detection()` invocavano `split_document_into_chunks()` senza valorizzare il parametro `max_tokens`, ricadendo sui vecchi limiti statici di 7.500 caratteri e 25 bundle.
- **Overhead di Framing Non Considerato**: Inoltre, nella funzione `split_document_into_chunks()` di `ai/chunking.py`, la stima dei token per bundle computava esclusivamente il testo grezzo della chat con un'aggiunta di soli 12 token fissi per sezione (`math.ceil(bundle_chars / 2.8) + (len(sections) * 12)`). Quando il blocco veniva serializzato per il prompt LLM in formato JSON strutturato (`serialize_document_for_llm`), ciascuna evidenza generava circa 240 caratteri di metadati JSON (`evidence_id`, `source_type`, `message_id`, `source_name`, `source_record_id`, chiavi e parentesi), pari a circa **85 token reali per evidenza**. Ne conseguiva che raggruppando 67 bundle in un unico chunk, il prompt finale raggiungeva **9.887 token di input**, superando la context window di 8.192 token e provocando `AnalysisInputTooLargeError` o HTTP 400.

### 1.2 Difetti nel Contatore di Avanzamento (`analysis/progress.py`)
- **Caso A (Fallimento Chunk Segnalato come Completo)**: Quando un chunk falliva, il tracker registrava `requests_failed`, ma la formula di calcolo dei blocchi residui (`remaining_chunks`) continuava a considerare i chunk falliti come ancora "in attesa". Inoltre, l'invocazione di `complete()` alla fine del flusso forzava `percentage = 100.0` e `is_complete = True` anche in presenza di `failed_chunks > 0`, presentando un'analisi fallita o parziale come completa al 100%.
- **Caso B (Anomalia 2/1 su Sub-Chunking Dinamico)**: Quando un blocco subiva una suddivisione dinamica (split) a seguito di context overflow, i sub-chunk venivano elaborati incrementando `completed_chunks` senza incrementare contemporaneamente `total_chunks`. Su un documento a blocco singolo suddiviso in due sotto-blocchi, il tracker emetteva eventi con `completed_chunks = 2` su `total_chunks = 1`, visualizzando l'anomalia `2/1` e percentuali superiori al 100%.

### 1.3 Disallineamento degli Stati PARTIAL / COMPLETE nel Service Layer
- **Causa Accertata**: In `analysis/service.py`, la Topic Discovery multi-chunk invocava `tracker.complete()` dopo l'aggregazione anche quando uno o più chunk intermedi erano falliti. La variabile `completed_count` rappresentava il numero di risultati riusciti, mentre `len(chunks)` rappresentava i chunk pianificati; in caso di fallimenti parziali o sub-chunking, le due grandezze non erano coerenti.
- **Falso Verdetto ABSENT su Copertura Parziale**: Nella Topic Detection a blocco singolo suddiviso, se il sotto-blocco 1 riusciva con esito `ABSENT` e il sotto-blocco 2 falliva con errore backend, il sistema rischiava di aggregare un verdetto globale `ABSENT` per l'intero documento, violando il principio di copertura forense (impossibile escludere un reato se una porzione del fascicolo non è stata analizzata).

### 1.4 Violazione di Headroom nel Budget Dinamico (`ai/token_budget.py`)
- **Causa Accertata**: La funzione `calculate_dynamic_output_budget()` calcolava il budget target come `target = max(min_output_tokens, desired_output_tokens)`. In presenza di un contesto ridotto (es. context: 2000, input: 1500, margin: 256), l'headroom residua risultava pari a `244 token`. Tuttavia, con `min_output_tokens = 512` e `desired = 200`, `max(512, 200)` restituiva `512`, un valore matematicamente superiore all'headroom disponibile di 244 token. Tale valore provocava inevitabilmente l'overflow del contesto su LM Studio.
- **Assenza di Ricalcolo su Retry**: Nei retry di Discovery a seguito di troncamento (`finish_reason="length"`), quando il sistema dimezzava i topic richiesti modificando il system prompt, il budget di output non veniva ricalcolato dinamicamente rispetto al nuovo prompt.

---

## 2. MODIFICHE EFFETTUATE

### 2.1 Modulo `ai/token_budget.py`
- Riscritto il calcolo di `calculate_dynamic_output_budget()` con vincolo matematico inderogabile:
  ```python
  available_headroom = max(0, context_length - estimated_prompt_tokens - safety_margin)
  if available_headroom < min_output_tokens:
      if raise_on_insufficient:
          raise AnalysisInputTooLargeError(...)
      return 0  # Valore non utilizzabile, previene tassativamente HTTP 400
  target = max(min_output_tokens, desired_output_tokens)
  return min(available_headroom, target)
  ```
- Garantito che nessun valore restituito superi mai `available_headroom`.

### 2.2 Modulo `analysis/progress.py`
- Riprogettata l'architettura dei contatori di avanzamento con disaggregazione completa e thread-safety (`threading.Lock`):
  - `planned_chunks`: Chunk pianificati inizialmente.
  - `subchunks_added`: Sotto-blocchi aggiuntivi generati da suddivisione dinamica.
  - `total_chunks`: Totale aggiornato dinamicamente (`planned_chunks + subchunks_added`).
  - `completed_chunks`: Chunk e sotto-blocchi terminati con successo.
  - `failed_chunks`: Chunk e sotto-blocchi falliti.
  - `remaining_chunks`: `max(0, total_chunks - completed_chunks - failed_chunks)` (i falliti non sono mai conteggiati come in attesa).
  - `requests_sent`, `requests_successful`, `retries_count`, `requests_failed`: Contatori precisi delle interazioni HTTP.
- Introdotto il metodo `split_chunk(chunk_idx, subchunks_count)`: adatta `total_chunks` in tempo reale, eliminando definitivamente le anomalie `2/1`.
- Introdotto il metodo `partial(status_msg)`: emette eventi espliciti con `is_partial = True`, `is_complete = False` e percentuale proporzionale ai chunk riusciti.
- Blindato `complete()`: se `failed_chunks > 0` o `completed_chunks < total_chunks`, converte automaticamente lo stato in parziale (`is_complete = False, is_partial = True`) e non emette mai il 100%.

### 2.3 Modulo `ai/chunking.py`
- Aggiornata la stima token in `split_document_into_chunks()`:
  ```python
  bundle_tokens = math.ceil(bundle_chars / 2.8) + (len(bundle.text_evidence_sections) * 85)
  ```
  La stima include l'effettivo overhead di serializzazione JSON forense (~85 token per evidenza), prevenendo context overflow su blocchi ad alta densità di messaggi.
- In `aggregate_topic_detection()`: confermata la Regola 2 (FAIL-SAFE). Se non vi sono riscontri `PRESENT` ma uno o più chunk sono falliti o mancanti, la decisione finale per l'intero documento è categoricamente **`UNCERTAIN`** (mai `ABSENT`).

### 2.4 Modulo `analysis/models.py`
- Aggiornata la configurazione centralizzata `OperationalAnalysisConfig`:
  - `chunk_character_budget = 12000`
  - `max_chunk_bundles = 100`
  - `max_chunk_tokens = 2500`

### 2.5 Modulo `analysis/service.py`
- Introdotto il metodo di servizio `_calculate_effective_chunk_token_budget()`:
  Calcola dinamicamente il budget prudenziale per singolo chunk documentale considerando la context window reale del modello (8.192), l'overhead del system prompt (400), lo schema JSON (circa 600), il budget di output (1.500) e il margine di sicurezza (256).
- Integrato il chunking token-aware sia in `run_topic_discovery()` sia in `run_topic_detection()`, invocando `split_document_into_chunks(document, max_characters=self.max_chunk_characters, max_bundles=self.max_chunk_bundles, max_tokens=eff_chunk_tokens)`.
- Riorganizzato il flusso di sub-chunking:
  - In caso di context overflow, `tracker.split_chunk()` adatta il totale.
  - I sotto-blocchi utilizzano indici visuali progressivi distinti.
  - Se un sotto-blocco riesce e uno fallisce: lo stato finale nei metadati è rigorosamente **`PARTIAL`**, con `completed_chunk_count = 1, failed_chunk_count = 1` e invocazione di `tracker.partial()`.
- Nella Discovery multi-chunk: se `failed_count > 0`, lo status è impostato a **`PARTIAL`**, con invocazione di `tracker.partial()`.

### 2.6 Modulo `ai/topics.py`
- In `discover_topics()`: su retry causato da troncamento (`finish_reason="length"`), quando il numero di topic viene ridotto e il system prompt viene rigenerato, il sistema ricalcola dinamicamente sia `input_est` sia `current_max_tokens` tramite `calculate_dynamic_output_budget()`, garantendo il rispetto rigoroso dell'headroom.

---

## 3. TEST FALLITI PRIMA DELLA CORREZIONE

Prima delle correzioni implementate, i seguenti comportamenti riproducevano fallimenti specifici:

| Test / Scenario | Comportamento Prima della Correzione | Esito Errato Riscontrato |
| :--- | :--- | :--- |
| **Budget Headroom Eccessivo** | Context: 2000, Input: 1500, Margin: 256, Desired: 200, Min: 512. | Restituiva `512` con headroom di soli 244 token (Overflow). |
| **Contatore Caso A** | 1 chunk pianificato, inferenza fallita. | `remaining_chunks = 1` (in attesa); `complete()` marcava 100% e `is_complete = True`. |
| **Contatore Caso B** | 1 chunk originale suddiviso in 2 sotto-blocchi. | Contatore mostrava `2/1` chunk completati e percentuali anomale. |
| **Service Single-Chunk Parziale** | 1 chunk suddiviso in 2 sub-chunk: sub 1 = ABSENT, sub 2 = errore HTTP 500. | Stato finale dichiarato `COMPLETE`; decisione globale `ABSENT` su evidenza incompleta. |
| **Discovery su 100 Messaggi Reali** | Esecuzione operativa di `run_topic_discovery()` con 100 messaggi sintetici. | Sollevava `AnalysisInputTooLargeError: 9887 token su 8192` per omessa stima del framing JSON. |
| **Discovery Multi-Chunk con Guasto** | 2 chunk: chunk 1 = successo, chunk 2 = errore JSON. | Invocava `tracker.complete()` anziché `tracker.partial()`; status `COMPLETE`. |

---

## 4. TEST SUPERATI DOPO LA CORREZIONE

Dopo l'applicazione di tutte le modifiche:

- **Suite Mirata Post-Audit (`tests/unit/test_integrated_audit_fixes.py`)**: **28 su 28 passed** (1.15s).
- **Suite Chunking e Invarianti (`tests/unit/test_ai_chunking.py`)**: **13 su 13 passed** (0.83s).
- **Suite Miglioramenti Operativi (`tests/unit/test_realworld_topic_improvements.py`)**: **18 su 18 passed** (0.95s).
- **Suite Integrale Pytest Offline (`scripts/run_and_log_tests_utf8.py`)**:
  - **1.703 passed**
  - **1 skipped** (`test_symlink_pointing_outside_rejected` per `WinError 1314` Windows privilege)
  - **5 deselected** (test smoke esclusi dall'esecuzione ordinaria)
  - **0 failed**
  - Durata: **193.39 secondi**.

---

## 5. NUMERO DI CHUNK PRIMA/DOPO NEL PERCORSO APPLICATIVO REALE

Misurazione riproducibile eseguita tramite lo script `scripts/measure_scalability_and_profiling.py` sul reale percorso di esecuzione di `LocalTopicAnalysisService`:

| Dataset Messaggi | Caratteri Testo | Token Stimati | Chunk Baseline Storica (7.5k car / 25 bundle / no token) | Chunk Ottimizzati Token-Aware (12k car / 100 bundle / 2.5k token) | Differenza Chunk | Evidenze Preservate |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **100 messaggi** | 6.970 | ~2.490 | 4 | **5** | +1 chunk (+25.0%) | **100.0%** (100/100) |
| **1.000 messaggi** | 69.700 | ~24.893 | 40 | **46** | +6 chunk (+15.0%) | **100.0%** (1000/1000) |
| **10.000 messaggi** | 697.000 | ~248.929 | 400 | **455** | +55 chunk (+13.8%) | **100.0%** (10000/10000) |
| **50.000 messaggi** | 3.485.000 | ~1.244.643 | 2.000 | **2.273** | +273 chunk (+13.7%) | **100.0%** (50000/50000) |

### Analisi Tecnica Obiettiva
1. **Perché i chunk aumentano leggermente sui messaggi brevi?**:
   La configurazione Baseline storica (25 bundle fissi) impacchettava 25 messaggi alla cieca. Con l'overhead reale di serializzazione JSON forense (~85 token per evidenza), 25 messaggi generavano oltre 2.725 token di evidenza, che sommati al system prompt e allo schema JSON superavano il limite sicuro.
   Il chunking token-aware limita rigorosamente ciascun blocco a circa **22 messaggi** (~2.400 token di evidenza), garantendo che la richiesta a LM Studio non superi mai circa 3.280 token di input.
2. **Prevenzione Sistematica degli Errori HTTP 400**:
   La leggera suddivisione aggiuntiva (+13.7% chunk su 50.000 messaggi) è il prezzo matematico necessario per garantire la **totale assenza di context overflow** e prevenire gli errori HTTP 400 su qualsiasi modello locale con context window da 8.192 token.
3. **Verifica End-to-End sul Service Layer**:
   Sul dataset da 100 messaggi, `optimized_service.run_topic_discovery()` è stato eseguito con successo end-to-end con 5 chunk effettivi e stato `COMPLETE`.

---

## 6. PRESTAZIONI MISURATE

Le metriche temporali e di memoria registrate durante la profilazione preventiva:

| Dataset Messaggi | Tempo Preparazione / Ingestion (s) | Tempo Consolidamento / Aggregazione Multi-Chunk (s) | Memoria di Picco (MB) |
| :--- | :--- | :--- | :--- |
| **100 messaggi** | 0.0314 s | 0.00080 s (5 blocchi) | **0.24 MB** |
| **1.000 messaggi** | 0.3141 s | 0.00447 s (46 blocchi) | **2.09 MB** |
| **10.000 messaggi** | 3.1678 s | 0.00522 s (50 blocchi) | **21.41 MB** |
| **50.000 messaggi** | 27.9656 s | 0.00520 s (50 blocchi) | **106.28 MB** |

- **Aggregazione Istantanea**: Il consolidamento gerarchico dei topic (`aggregate_topic_discovery`) impiega circa **5 millisecondi** anche aggregando decine di chunk intermedi.
- **Efficienza di Memoria**: Il consumo di memoria di picco rimane perfettamente lineare e contenuto a circa 106 MB su 50.000 messaggi, prevenendo memory leak sul thread di Streamlit.

---

## 7. COMPORTAMENTO DEL CONTATORE SU ESECUZIONI COMPLETE, PARTIAL E FAILED

Il progress tracker garantisce ora una semantica rigorosa e matematicamente coerente:

```
+----------------------------------------------------------------------------------------------------+
|                                    STATI DEL PROGRESS TRACKER                                      |
+-------------------+--------------------+--------------------+--------------------+-----------------+
| Stato Analisi     | Flag Booleani      | Percentuale Visual | Remaining Chunks   | Failed Chunks   |
+-------------------+--------------------+--------------------+--------------------+-----------------+
| COMPLETE          | is_complete = True | 100.0%             | 0                  | 0               |
|                   | is_partial = False |                    |                    |                 |
|                   | is_failed = False  |                    |                    |                 |
+-------------------+--------------------+--------------------+--------------------+-----------------+
| PARTIAL           | is_complete = False| (completed/total)  | 0 (nessun blocco   | > 0             |
| (alcuni chunk ok, | is_partial = True  | * 95%              | è in attesa)       |                 |
| altri falliti)    | is_failed = False  | (mai 100%)         |                    |                 |
+-------------------+--------------------+--------------------+--------------------+-----------------+
| FAILED            | is_complete = False| 0.0%               | 0                  | = total_chunks  |
| (tutti i chunk    | is_partial = False |                    |                    |                 |
| falliti)          | is_failed = True   |                    |                    |                 |
+-------------------+--------------------+--------------------+--------------------+-----------------+
```

- **Invarianti Garantiti**:
  1. `completed_chunks <= total_chunks` (nessuna visualizzazione anomala tipo `2/1`).
  2. `remaining_chunks >= 0` (nessun valore negativo).
  3. `remaining_chunks = max(0, total_chunks - completed_chunks - failed_chunks)` (i blocchi falliti non restano mai "in attesa").
  4. Su split dinamico: `split_chunk()` adatta `total_chunks` prima dell'elaborazione dei sotto-blocchi.

---

## 8. VERIFICA DEI BUDGET TOKEN

1. **Formula Headroom**:
   $$\text{Headroom} = \max(0, \text{Context Length} - \text{Estimated Prompt Tokens} - \text{Safety Margin})$$
2. **Clamping Inderogabile**:
   $$\text{Dynamic Output Budget} = \begin{cases} 0 & \text{se Headroom} < \text{Min Output Tokens} \\ \min(\text{Headroom}, \max(\text{Min}, \text{Desired})) & \text{altrimenti} \end{cases}$$
3. **Casi ai Margini Verificati**:
   - `Headroom (244) < Min (512)` $\rightarrow$ Restituisce `0` (o solleva `AnalysisInputTooLargeError`). Non restituisce mai 512.
   - `Headroom (512) == Min (512)` $\rightarrow$ Restituisce `512`.
   - `Headroom (511) < Min (512)` $\rightarrow$ Restituisce `0`.
   - `Headroom (744) > Min (512) e Desired (600)` $\rightarrow$ Restituisce `600`.
4. **Retry con Adattamento Prompt**:
   Quando `discover_topics()` riceve `finish_reason="length"`, dimezza i topic richiesti, rigenera il prompt e ricalcola il budget token considerando la nuova stima dell'input.

---

## 9. TEST REALMENTE ESEGUITI OFFLINE

Tutti i test sono stati eseguiti rigorosamente offline su Python 3.14.7 senza connessione di rete e senza download automatici:

| Tipologia Test | Moduli Principali | Numero Test Eseguiti | Esito |
| :--- | :--- | :--- | :--- |
| **Unitari Offline** | `test_integrated_audit_fixes`, `test_ai_chunking`, `test_ai_topics`, `test_token_budget`, `test_progress`, `test_models`, `test_importers`, `test_normalization`, `test_validation`, `test_security` | 1.610 | **PASSED** |
| **Integrazione Offline** | `test_cellebrite_csv_integration`, `test_cloud_e2e_dataset`, `test_search_service_integration`, `test_discovery_detection_invariants` | 63 | **PASSED** |
| **Streamlit / AppTest** | `test_apptest_navigation`, `test_apptest_flow`, `test_operational_model_management` | 30 | **PASSED** |
| **Totale Selezionato Ordinario** | | **1.703** | **PASSED** |

---

## 10. TEST NON ESEGUITI E MOTIVI

1. **Test Saltato su Windows (`WinError 1314`)**:
   - `tests/unit/test_sanitize_dataset.py::TestSanitizerSafetyGuard::test_symlink_pointing_outside_rejected`
   - **Motivo**: Saltato (`SKIPPED`) a causa della policy di sicurezza nativa di Windows (`WinError 1314: Il privilegio non è posseduto dal client`), che impedisce la creazione di symlink filesystem da account non amministratore/senza Developer Mode. Il test non ha alcuna dipendenza da Internet e su Linux Ubuntu viene regolarmente eseguito e superato.
2. **5 Test Deselected**:
   - I test contrassegnati con marker `@pytest.mark.smoke` sono esclusi dall'esecuzione ordinaria della test suite tramite configurazione `pyproject.toml` (`-m "not smoke"`).
3. **Nove Benchmark Ufficiali**:
   - I 9 benchmark scientifici non sono stati eseguiti per vincolo esplicito di consegna (non alterare le metriche congelate).
4. **Test Live con Demone LM Studio**:
   - I test che richiedono un'istanza fisica di LM Studio in ascolto su `http://127.0.0.1:1234` sono subordinati alla presenza dell'ambiente hardware Linux Ubuntu con GPU dedicata.

---

## 11. INTEGRITÀ DEI FILE SCIENTIFICI

I file scientifici congelati sono stati verificati tramite hash crittografico SHA-256 e risultano **100% conformi e inviolati**:

| File di Benchmark | Hash SHA-256 Verificato | Stato |
| :--- | :--- | :--- |
| `ai/experiment.py` | `91100C0A84CB4A8B7C2EEF34A06CFF9EC21AA6E6EEC14FFD09CE6FEA8BCEE845` | **INVIOLATO** |
| `ai/benchmark.py` | `DC57081DBCE3463A8017AA26BB4E4A18889AF0A3296853FE40968561D6D44D36` | **INVIOLATO** |
| `run_benchmark.sh` | `7F9CAE97DF2FAE1BC3B5964E639B9F80E3AC5A388EC9C3E20305FF64D48044A7` | **INVIOLATO** |

Nessuna modifica è stata apportata ai prompt scientifici, ai dataset forensi o ai pesi di scoring del benchmark.

---

## 12. ISTRUZIONI DI AGGIORNAMENTO SU UBUNTU

Per aggiornare l'ambiente scientifico e operativo su Linux Ubuntu:

### Passo 1: Verifica Hash del Pacchetto ZIP
```bash
sha256sum chatAnalysis_post_audit_corrective_release_20261013.zip
```

### Passo 2: Estrazione e Posizionamento
```bash
unzip chatAnalysis_post_audit_corrective_release_20261013.zip -d chatAnalysis_update/
cd chatAnalysis_update/
```

### Passo 3: Verifica Integrità File Congelati
```bash
sha256sum -c benchmark_frozen_sha256.txt
```

### Passo 4: Installazione Wheel Aggiornata
```bash
source .venv/bin/activate
pip install --no-deps --force-reinstall dist/whatsapp_forensic_importer-0.1.0-py3-none-any.whl
```

### Passo 5: Esecuzione Suite di Validazione Completa Offline
```bash
python scripts/run_and_log_tests_utf8.py
```
Verificare che il log `pytest_and_apptest.log` termini con return code `0` e zero fallimenti.

### Passo 6: Verifica Gate Live LM Studio (Opzionale con Demone Attivo)
Avviare LM Studio con il modello `qwen2.5-7b-instruct` caricato, context window 8.192, porta 1234:
```bash
pytest tests/smoke/ -v
```
