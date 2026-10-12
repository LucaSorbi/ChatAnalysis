# AUDIT DEL FLUSSO FILE REALE (`DatasetMode.FILE`)

Ricostruzione rigorosa, end-to-end e verificata sul codice sorgente della catena di elaborazione per i file reali caricati dall'utente.

---

## 1. Tracciamento Step-by-Step della Catena Operativa

### Passo 1: Upload File e Dispatch Formato
* **FILE**: `ui/presentation.py` (righe 234–270)
* **FUNZIONE/COMPONENTE**: `render_import()` e `st.file_uploader(..., key="primary_file_uploader")`
* **INPUT**: Stream del file caricato dall'utente (`UploadedFile`) e formato selezionato nel dropdown (`SourceFormat`).
* **OUTPUT**: Oggetto `IngestionRequest(source_format=source_format, primary_file=uploaded_file, ...)`
* **STATO SESSIONE MODIFICATO**: Nessuno fino al click sul pulsante "Avvia Importazione Forense".
* **AI CHIAMATA?**: **NO** (100% offline).

### Passo 2: Validazione Sandbox, Hashing Streaming e Dispatch Importer
* **FILE**: `ui/ingestion.py` (righe 140–206, 260–328)
* **FUNZIONE/COMPONENTE**: `ingest_file_payload()` e `_compute_sha256_and_write()`
* **INPUT**: `IngestionRequest`
* **OUTPUT**: File temporaneo sicuro in `tempfile.TemporaryDirectory()`, impronta SHA-256 e dimensione in byte.
* **STATO SESSIONE MODIFICATO**: Nessuno (elaborazione in sandbox volatile).
* **AI CHIAMATA?**: **NO** (100% offline).

### Passo 3: Parsing Sorgente Grezza (Importer Specializzato)
* **FILE**: `importer/whatsapp_export.py`, `importer/whatsapp_msgstore.py`, `importer/cellebrite_*.py`
* **FUNZIONE/COMPONENTE**: `importer.import_records(safe_primary_path)`
* **INPUT**: Percorso del file temporaneo in sandbox locale.
* **OUTPUT**: Generatore di `RawRecord` (immutabili, con metadati e campi sorgente preservati).
* **STATO SESSIONE MODIFICATO**: Nessuno.
* **AI CHIAMATA?**: **NO** (100% deterministico).

### Passo 4: Validazione Forense
* **FILE**: `validation/validator.py`
* **FUNZIONE/COMPONENTE**: `RecordValidator.validate_stream(raw_records)`
* **INPUT**: Stream di `RawRecord`.
* **OUTPUT**: Stream di `ValidationResult` con tracking degli issue senza alterazione dei dati.
* **STATO SESSIONE MODIFICATO**: Nessuno.
* **AI CHIAMATA?**: **NO** (100% deterministico).

### Passo 5: Normalizzazione Canonica
* **FILE**: `normalization/normalizer.py`
* **FUNZIONE/COMPONENTE**: `RecordNormalizer.normalize_stream(val_results)`
* **INPUT**: Stream di `ValidationResult`.
* **OUTPUT**: Stream di `NormalizedRecord` con timestamp UTC normalizzato e mapping canonico.
* **STATO SESSIONE MODIFICATO**: Nessuno.
* **AI CHIAMATA?**: **NO** (100% deterministico).

### Passo 6: Risoluzione Entità e Costruzione Messaggi Unificati
* **FILE**: `entity_resolution/resolver.py` e `entity_resolution/builder.py`
* **FUNZIONE/COMPONENTE**: `DeterministicEntityResolver.resolve()` e `UnifiedModelBuilder.build_all()`
* **INPUT**: Stream di `NormalizedRecord`.
* **OUTPUT**: `tuple[UnifiedMessage, ...]`.
* **STATO SESSIONE MODIFICATO**: Nessuno.
* **AI CHIAMATA?**: **NO** (100% deterministico).

### Passo 7: Costruzione Documenti di Evidenza (`ConversationEvidenceDocument`)
* **FILE**: `ui/ingestion.py` (righe 406–455)
* **FUNZIONE/COMPONENTE**: Raggruppamento chat e instanziazione `ConversationEvidenceDocument`
* **INPUT**: `tuple[UnifiedMessage, ...]` raggruppati per `chat_id`.
* **OUTPUT**: `dict[str, ConversationEvidenceDocument]` contenente `MessageEvidenceBundle(message=m)`.
* **NOTA STRUTTURALE**: In questa fase i bundle contengono unicamente il messaggio unificato (`ORIGINAL_TEXT`); non vengono attivate pipeline multimodali automatiche (STT vocale o OCR/Vision non eseguiti offline all'ingestion).
* **STATO SESSIONE MODIFICATO**:
  - `set_real_ingestion_result(result)`: popola `KEY_REAL_INGESTION_RESULT`.
  - `set_dataset_mode(DatasetMode.FILE)`: imposta la modalità su `FILE`.
  - `set_available_documents(docs_map)`: popola le conversazioni disponibili.
  - `set_selected_document_id(first_doc_id)`: imposta la prima conversazione attiva.
  - `set_conversation_document(first_doc)`: imposta il documento corrente.
  - `set_detection_results(())`: **inizializzato a tupla vuota**.
  - `set_discovery_results(())`: **inizializzato a tupla vuota**.
  - `set_search_service(SearchService(document=first_doc, detection_results=(), discovery_results=()))`.
* **AI CHIAMATA?**: **NO**.

---

## 2. Analisi della Schermata "5. Analisi topic" (`render_topics()`)

### File: `ui/presentation.py` (righe 527–1000)

```python
# Riga 536-547: GUARD OBSOLETO PER REAL FILE MODE
if (
    state.get_dataset_mode() == DatasetMode.FILE
    and not state.get_detection_results()
    and not state.get_discovery_results()
):
    st.info(
        "Il dataset reale è stato importato correttamente.\n\n"
        "L'analisi AI non è stata eseguita in questa fase.\n\n"
        "Il benchmark/inference con modelli locali è differito alla fase sperimentale finale."
    )
    if state.get_conversation_document() is None:
        return
```

### Comportamento Riscontrato sul Codice Reale:
1. **Banner Obsoleto Visibile (PROBLEMA A)**:
   All'apertura della pagina su una chat reale, viene mostrato il banner:
   *"Il benchmark/inference con modelli locali è differito alla fase sperimentale finale."*
   Questo testo inganna l'utente facendo credere che l'analisi AI sia completamente bloccata o disabilitata.
2. **Esecuzione Effettiva di Topic Detection (Area 1 - Argomento Libero)**:
   Poiché `state.get_conversation_document()` non è `None`, il codice prosegue ed esegue il rendering dei due tab:
   - In **Tab 1: Topic Detection**: l'Area 1 permette di digitare un argomento libero e cliccare `"🚀 Esegui Topic Detection"`.
   - L'azione invoca `execute_manual_topic_detection()` che interroga LM Studio locale su `http://127.0.0.1:1234`.
   - Se LM Studio è attivo, la Detection su file reale viene effettivamente eseguita e il risultato salvato in sessione.
3. **Blocco Totale di Topic Discovery su `DatasetMode.FILE`**:
   - In **Tab 2: Topic Discovery**: il codice invoca:
     `disc_results = search_topic_discoveries(search_service=active_service, query_text=query_disc)` (riga 826).
   - In `DatasetMode.FILE`, `state.get_discovery_results()` è sempre `()` (vuoto).
   - Non esiste **alcun pulsante** (`st.button`) o funzione in `ui/presentation.py` o `ui/application.py` per invocare `TopicDiscoveryAnalyzer.discover_topics()`.
   - Di conseguenza, `disc_results.hits` è sempre vuoto (0 argomenti).
   - Viene stampato: *"Visualizzati 0 argomenti emersi. Nessun argomento emerso corrisponde alla ricerca."*
4. **Impossibilità del Workflow Discovery → Detection su `DatasetMode.FILE`**:
   - Poiché nessuna card di DiscoveredTopic viene mai renderizzata su FILE, il pulsante `"🔬 Verifica con Topic Detection"` (riga 862) non compare mai.
   - Il flusso Discovery -> Detection è **fisicamente inaccessibile** su file reali importati.

---

## 3. Censimento di Tutti i Guard, Return e Fallback in `ui/presentation.py`

| Riga | Costrutto | Condizione | Effetto Operativo |
| :--- | :--- | :--- | :--- |
| **531–533** | `if not state.is_dataset_loaded(): return` | Dataset non caricato | Mostra st.info e blocca l'esecuzione |
| **536–547** | `if state.get_dataset_mode() == DatasetMode.FILE and not state.get_detection_results() and not state.get_discovery_results():` | File reale senza risultati AI | Stampa banner fuorviante *"Il benchmark/inference con modelli locali è differito alla fase sperimentale finale."* |
| **549–552** | `if doc is None: return` | Nessuna conversazione attiva | Stampa *"Nessuna conversazione attiva per l'analisi"* e blocca |
| **836–838** | `if not disc_results.hits:` | Zero topic scoperti trovati | Stampa *"Nessun argomento emerso corrisponde alla ricerca"* (perenne su FILE) |
| **867–872** | `if verify_btn: if doc is None ... elif not doc.all_evidence_sections:` | Documento privo di sezioni | Blocca con errore senza avviare Detection |
| **873–880** | `validate_discovery_provenance(...)` | `disc_prov_id != doc.document_id` | Blocca operazione per disallineamento provenance |
