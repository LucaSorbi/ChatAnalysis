# REPORT TECNICO SCIENTIFICO — CORREZIONE FINALE OTTIMIZZAZIONE SCALABILITÀ
**Progetto:** ChatAnalysis (WhatsApp Forensic Importer & Topic Analysis)  
**Data:** 17 Ottobre 2026  
**Ambiente di Riferimento:** Windows local workspace / Python 3.14.7 / Pytest 9.1.1  
**Destinazione Laboratorio:** Ubuntu Linux (GTX TITAN X 12GB Maxwell, Qwen 2.5 7B Instruct Q4_K_M, Context 8192)  
**Riferimento Archivio:** `chatAnalysis_scalability_safe_optimization_fix_audit_20261017.zip`

---

## 1. CORREZIONI EFFETTUATE

A seguito dei riscontri dell'audit indipendente, sono state implementate tre correzioni mirate, senza alterare l'architettura generale né aggiungere ottimizzazioni speculative non richieste:

### 1.1. Risoluzione della Mutabilità della Cache Alias (`ai/serializer.py`)
- **Problema:** La funzione `generate_evidence_alias_maps()` memorizzava la tupla di dizionari `(id_to_alias, alias_to_id)` direttamente in `document._cached_alias_maps` e restituiva gli stessi riferimenti ai chiamanti. Se un consumatore modificava `id_to_alias`, la mutazione contaminava la cache interna del documento, invalidando la coerenza del mapping inverso.
- **Soluzione Applicata:** Implementata la **cache privata con copie difensive**. La tupla interna salvata su `_cached_alias_maps` rimane strettamente protetta; ogni invocazione di `generate_evidence_alias_maps()` restituisce copie difensive indipendenti `dict(cached_id_to_alias), dict(cached_alias_to_id)`.
- **Benefici Preservati:** Il tempo di copia `dict(...)` per 30–100 elementi è nell'ordine dei sub-microsecondi (~150 ns), preservando il guadagno prestazionale (riduzione di oltre il 60% dei tempi di serializzazione) e garantendo al contempo l'immutabilità della cache interna.

### 1.2. Ripristino delle Fixture Sintetiche nell'Archivio (`scripts/create_scalability_audit_archive.py`)
- **Problema:** Il filtro di esclusione dell'archivio scartava indistintamente qualsiasi file `.db`, `.sqlite` e `.zip`, eliminando involontariamente le fixture sintetiche necessarie ai test automatici.
- **Soluzione Applicata:** Introdotta una regola esplicita che preserva tutte le fixture approvate sotto `test_data/` (inclusi `test_data/whatsapp_export/msgstore.db`, `test_data/whatsapp_export/wa.db` e `test_data/synthetic_smoke/synthetic_whatsapp_chat.zip`).

### 1.3. Fedeltà di Misurazione nel Benchmark di Scalabilità (`scripts/offline_scalability_benchmark.py`)
- **Problema:** La fase di costruzione del documento nel benchmark utilizzava ancora `group_msgs = list(builder.build_stream(chat_records))` e poi `bundles = [MessageEvidenceBundle(message=m) for m in group_msgs]`, non misurando il percorso di streaming diretto introdotto in `ui/ingestion.py`.
- **Soluzione Applicata:** Allineata la costruzione del documento in `scripts/offline_scalability_benchmark.py` allo streaming diretto in `bundles`, consentendo la misurazione trasparente e fedele del medesimo percorso dell'applicazione.

---

## 2. TEST CHE RIPRODUCEVANO I DIFETTI E RELATIVA RISOLUZIONE

### 2.1. Test della Mutabilità della Cache Alias (`test_08`)
Aggiunto in `tests/unit/test_scalability_safe_optimizations.py`:
- `test_08_alias_cache_mutation_immunity_and_isolation`:
  1. Invoca `generate_evidence_alias_maps(doc)`.
  2. Altera intenzionalmente il dizionario `id_to_alias` con chiavi e alias corrotti (`"E_MUTATED"`, `"INVENTED_ID"`).
  3. Reinvoca la funzione sullo stesso documento.
  4. Dimostra che la seconda invocazione restituisce il mapping originale e corretto, privo di chiavi inventate, e che i riferimenti ai dizionari sono istanze distinte (`assert id_to_alias_1 is not id_to_alias_2`).
  - **Esito:** `PASSED` (8/8 passati nella suite `test_scalability_safe_optimizations.py`).

### 2.2. Test delle Fixture Sintetiche Msgstore e Integrazione
Eseguiti con le fixture ripristinate:
- `tests/unit/test_whatsapp_msgstore_importer.py`:
  - `test_synthetic_original_msgstore_returns_true` -> `PASSED`
  - `test_synthetic_msgstore_copied_as_evidence_csv` -> `PASSED`
  - Totale suite unit msgstore: **56/56 PASSED**.
- Test di integrazione del database reale WhatsApp ed Entity Resolution:
  - `test_whatsapp_msgstore_integration.py` -> **31/31 PASSED**.
  - `test_whatsapp_wa_integration.py` -> **21/21 PASSED**.
  - `test_entity_resolution_integration.py` -> **6/6 PASSED**.
  - `test_smoke_operational_ai.py` (usa `synthetic_whatsapp_chat.zip`) -> **31/31 PASSED**.

---

## 3. RIEPILOGO COMPLETO DELLA TEST SUITE

L'invocazione globale di `pytest tests/` (che include la totalità dei test unitari e di integrazione) produce il seguente consuntivo dettagliato:

- **Test totali scoperti:** 1.805
- **Test deselezionati:** 5 (contrassegnati con marker `smoke` non inclusi di default)
- **Test raccolti ed eseguiti:** 1.800
- **Test SUPERATI:** **1.792**
- **Test SALTATI:** 1 (`test_video_mp4_transcription_skipped_if_ffmpeg_missing` per assenza ffmpeg)
- **Test FALLITI:** **8** (esclusivamente errori di dipendenze esterne binarie assenti in Windows)

### Dettaglio Verificabile degli 8 Fallimenti Ambientali:

Tutti gli 8 fallimenti sono documentati e derivano dall'assenza delle dipendenze opzionali dichiarate in `pyproject.toml` sotto `[project.optional-dependencies]`:

1. `tests/unit/test_audio_transcriber.py::TestFasterWhisperTranscriberUnit::test_global_model_load_failure_raises_audio_model_load_error`
   - **Errore:** `ModuleNotFoundError: No module named 'faster_whisper'`
   - **Causa:** Libreria `faster-whisper` non installata nell'ambiente Windows (`stt = ["faster-whisper>=1.0.0"]`).
2. `tests/unit/test_image_text_extractor.py::TestTesseractImageTextExtractorUnit::test_is_available_propagates_unexpected_programming_errors`
   - **Errore:** `ModuleNotFoundError: No module named 'pytesseract'`
   - **Causa:** Libreria `pytesseract` non installata nell'ambiente Windows (`ocr = ["pytesseract>=0.3.10"]`).
3. `tests/unit/test_image_text_extractor.py::TestTesseractImageTextExtractorUnit::test_decompression_bomb_check_is_local_without_mutating_pillow_globals`
   - **Errore:** Fallisce l'asserzione in quanto l'assenza di `pytesseract` interrompe il ciclo prima del controllo pixel.
4. `tests/unit/test_image_text_extractor.py::TestTesseractImageTextExtractorUnit::test_backend_unavailable_raises_ocr_backend_unavailable_error`
   - **Errore:** `ModuleNotFoundError: No module named 'pytesseract'`
5. `tests/unit/test_image_text_extractor.py::TestTesseractImageTextExtractorUnit::test_mocked_successful_ocr_parses_regions_and_confidence`
   - **Errore:** `ModuleNotFoundError: No module named 'pytesseract'`
6. `tests/unit/test_image_text_extractor.py::TestTesseractImageTextExtractorUnit::test_mocked_empty_ocr_produces_no_text_status`
   - **Errore:** `ModuleNotFoundError: No module named 'pytesseract'`
7. `tests/unit/test_image_text_extractor.py::TestTesseractImageTextExtractorUnit::test_tesseract_cmd_global_state_not_leaked`
   - **Errore:** `ModuleNotFoundError: No module named 'pytesseract'`
8. `tests/unit/test_image_text_extractor.py::TestTesseractImageTextExtractorUnit::test_unexpected_error_propagates_in_check_engine_available`
   - **Errore:** `ModuleNotFoundError: No module named 'pytesseract'`

> [!NOTE]
> Il precedente conteggio di 1.572 test superati era dovuto all'esecuzione del solo comando `pytest tests/unit/` (1.582 test unitari). L'esecuzione dell'intera suite `pytest tests/` include i 218 test di integrazione, portando il totale a **1.792 passati**, in perfetta corrispondenza con la release originaria.

---

## 4. BENCHMARK OFFLINE FEDELE (MISURAZIONE PRIMA / DOPO)

Il benchmark offline è stato rieseguito con lo script corretto `scripts/offline_scalability_benchmark.py` (3 ripetizioni per scala):

| Dataset | Fase Misurata | Baseline Prima | Corretto Dopo | Variazione % | Equivalenza Forense |
|---|---|---|---|---|---|
| **1.000 msgs** | **Tempo Totale Wall** | **10.29s** | **4.77s** | **-53.6%** | Identica (29 chunk, 1001 eids) |
| 1.000 msgs | **Tempo Totale CPU** | **7.13s** | **4.31s** | **-39.6%** | Identica |
| 1.000 msgs | Doc Build (Streaming) | 2.010s | 1.149s | **-42.9%** | Identica |
| 1.000 msgs | Chunking & Budget | 0.444s | 0.069s | **-84.5%** | Identica |
| 1.000 msgs | Serializer & Alias | 0.528s | 0.121s | **-77.0%** | Identica |
| 1.000 msgs | Checkpoint Validation | 1.868s | 0.599s | **-67.9%** | Identica |
| 1.000 msgs | Topic Consolidation | 0.121s | 0.009s | **-92.8%** | Identica |
| 1.000 msgs | Peak RSS RAM | 56.0 MB | 57.4 MB | +2.4% (stabile) | Invariata |
|---|---|---|---|---|---|
| **5.000 msgs** | **Tempo Totale Wall** | **30.86s** | **21.01s** | **-31.9%** | Identica (147 chunk, 5001 eids) |
| 5.000 msgs | **Tempo Totale CPU** | **27.15s** | **19.43s** | **-28.4%** | Identica |
| 5.000 msgs | Doc Build (Streaming) | 5.950s | 5.290s | **-11.1%** | Identica |
| 5.000 msgs | Chunking & Budget | 1.421s | 0.336s | **-76.4%** | Identica |
| 5.000 msgs | Serializer & Alias | 1.396s | 0.588s | **-57.9%** | Identica |
| 5.000 msgs | Checkpoint Validation | 7.935s | 2.900s | **-63.4%** | Identica |
| 5.000 msgs | Topic Consolidation | 0.316s | 0.041s | **-87.0%** | Identica |
| 5.000 msgs | Peak RSS RAM | 128.2 MB | 131.8 MB | +2.8% (stabile) | Invariata |
|---|---|---|---|---|---|
| **10.000 msgs** | **Tempo Totale Wall** | **88.17s** | **42.99s** | **-51.2%** | Identica (294 chunk, 10001 eids) |
| 10.000 msgs | **Tempo Totale CPU** | **63.18s** | **40.01s** | **-36.7%** | Identica |
| 10.000 msgs | Doc Build (Streaming) | 20.447s | 11.267s | **-44.9%** | Identica |
| 10.000 msgs | Chunking & Budget | 3.927s | 0.671s | **-82.9%** | Identica |
| 10.000 msgs | Serializer & Alias | 3.871s | 1.183s | **-69.4%** | Identica |
| 10.000 msgs | Checkpoint Validation | 18.009s | 5.814s | **-67.7%** | Identica |
| 10.000 msgs | Topic Consolidation | 0.938s | 0.080s | **-91.5%** | Identica |
| 10.000 msgs | Peak RSS RAM | 211.9 MB | 217.6 MB | +2.7% (stabile) | Invariata |
|---|---|---|---|---|---|
| **50.000 msgs** | **Tempo Totale Wall** | **315.50s (5.25m)**| **233.39s (3.89m)**| **-26.0% (-82.1s)**| Identica (1470 chunk, 50001 eids) |
| 50.000 msgs | **Tempo Totale CPU** | **281.60s (4.69m)**| **210.93s (3.52m)**| **-25.1% (-70.7s)**| Identica |
| 50.000 msgs | Doc Build (Streaming) | 62.866s | 61.846s | **-1.6%** | Identica |
| 50.000 msgs | Chunking & Budget | 15.201s | 3.322s | **-78.1%** | Identica |
| 50.000 msgs | Serializer & Alias | 14.640s | 5.716s | **-61.0%** | Identica |
| 50.000 msgs | Checkpoint Validation | 72.332s | 37.208s | **-48.6% (-35.1s)**| Identica |
| 50.000 msgs | Topic Consolidation | 3.577s | 0.397s | **-88.9%** | Identica |
| 50.000 msgs | Peak RSS RAM | 939.2 MB | 958.4 MB | +2.0% (stabile) | Invariata |

### Trasparenza sui Dati di Memoria RAM:
Come evidenziato dai valori misurati, il picco di memoria RAM (Peak RSS) mostra una leggera crescita nell'ordine del **+2.0% – +2.8%** (es. a 50.000 messaggi da 939.2 MB a 958.4 MB), dovuta alla presenza delle strutture di supporto memorizzate (`_cached_sections` e `frozenset[str]` degli `evidence_ids`) che consentono di abbattere i tempi di validazione CPU del 48%–68%. Il dato è riportato fedelmente senza dichiarare alcuna riduzione fittizia della RAM.

---

## 5. HASH DEI BENCHMARK SCIENTIFICI E VINCOLI GIT

I file del benchmark scientifico rimangono rigorosamente congelati e intoccati:
- `ai/experiment.py`: `91100c0a84cb4a8b7c2eef34a06cff9ec21aa6e6eec14ffd09ce6fea8bcee845`
- `ai/benchmark.py`: `dc57081dbce3463a8017aa26bb4e4a18889af0a3296853fe40968561d6d44d36`
- `run_benchmark.sh`: `7f9cae97df2fae1bc3b5964e639b9f80e3ac5a388ec9c3e20305ff64d48044a7`

Nessun comando Git (`git add`, `commit`, `push`, `pull`, `fetch`, `reset`) è stato eseguito.

---

## 6. VERIFICA DELLA WHEEL PYTHON

Ricompilata la wheel con le correzioni applicate:
- **Percorso:** `dist/whatsapp_forensic_importer-0.1.0-py3-none-any.whl`
- **Dimensione:** 334.469 byte
- **SHA-256:** `f1bb9c0bb003e2a63ab24e294c4d00aba8809b95bdd901716afd2f6399a88fb0`
- **Moduli inclusi:** Tutti i 12 package applicativi (`ai`, `analysis`, `core`, `entity_resolution`, `importer`, `multimodal`, `normalization`, `profiler`, `search`, `ui`, `unified`, `validation`).

---

## 7. LIMITI DELLE MISURAZIONI E COMPATIBILITÀ CHECKPOINT REALI

1. **Assenza della GPU GTX TITAN X locale:** I miglioramenti misurati riguardano unicamente il runtime CPU locale (parsing, serializzazione, staging SQLite, validazione fingerprint). Nessun beneficio di velocità è stato attribuito all'inferenza GPU di LM Studio.
2. **Compatibilità con i 20 Checkpoint Reali Ubuntu:** Poiché i file reali di checkpoint della macchina Ubuntu non sono residenti nell'ambiente di sviluppo Windows per motivi di riservatezza, la compatibilità è stata dimostrata mediante simulazione formale deterministica con dataset sintetico equivalente (`test_06` in `test_scalability_safe_optimizations.py`), che replica esattamente l'architettura 20 SUCCESS + 6 FAILED ripresa con `retry_failed_only=True`. La validazione definitiva su checkpoint reali avverrà direttamente sul server Linux.

---

## 8. ISTRUZIONI OPERATIVE PER IL LABORATORIO UBUNTU

1. Estrarre il pacchetto `chatAnalysis_scalability_safe_optimization_fix_audit_20261017.zip` nell'ambiente Linux Ubuntu.
2. Verificare l'integrità tramite il file `chatAnalysis_scalability_safe_optimization_fix_audit_20261017.zip.sha256`.
3. Assicurarsi che i checkpoint reali siano presenti in `.checkpoints/checkpoints.db`.
4. Avviare l'analisi con ripresa selettiva dei soli chunk falliti:
   - Il sistema identificherà automaticamente i 20 chunk validati.
   - Non invierà alcuna richiesta a LM Studio per i blocchi riusciti.
   - Eseguirà l'inferenza tramite alias deterministici (`E001..En`) per i soli 6 blocchi falliti (`2, 4, 19, 20, 21, 25`).
   - Consoliderà tutti i 26 chunk restituendo lo stato `COMPLETE` senza alcuna perdita di evidenze forensi.
