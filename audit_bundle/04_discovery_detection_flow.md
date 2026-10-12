# AUDIT FLUSSO DISCOVERY → DETECTION

Analisi approfondita del flusso tra la scoperta degli argomenti (Topic Discovery) e la verifica mirata (Topic Detection), rispondendo punto per punto con citazioni del codice sorgente.

---

### A. Come viene avviata Topic Discovery?
* **NEL CODICE**: In `ui/presentation.py` (riga 826), Topic Discovery **non viene avviata come inferenza live**, ma interrogata tramite `disc_results = search_topic_discoveries(search_service=active_service, query_text=query_disc)`.
* **MOTORE SOTTOSTANTE**: `TopicDiscoveryAnalyzer.discover_topics()` esiste in `ai/topics.py` (riga 200), ma **non è importato né invocato in nessun punto di `ui/`**.

### B. Esistono più percorsi Discovery?
* **SÌ**:
  1. **Percorso Benchmark / Batch**: In `ai/benchmark.py` e `ai/cloud_experiment.py`, dove `TopicDiscoveryAnalyzer.discover_topics()` viene chiamato per estrarre argomenti con prompt `topic_discovery_v1`.
  2. **Percorso UI**: Interroga unicamente i topic già indicizzati nel `SearchService`.

### C. DEMO usa: precalcolato? live? entrambi?
* **DEMO USA ESCLUSIVAMENTE PRECALCOLATO (HARDCODED)**:
  In `ui/demo.py` (righe 329–346), `build_synthetic_demo_dataset()` istanzia manualmente una `frozen dataclass` `TopicDiscoveryResult` con 2 argomenti predefiniti:
  1. `"Logistica e Ispezione Depositi"` (evidence: `msg::5::VISION_DESCRIPTION`)
  2. `"Gestione Contratti e Bozze"` (evidence: `msg::2::STT_TRANSCRIPTION`, `msg::8::ORIGINAL_TEXT`).
  Nessuna chiamata live LLM viene mai eseguita per generare il Discovery in DEMO.

### D. FILE usa: live? nessuno? percorso differente?
* **FILE NON USA NESSUN PERCORSO (COMPLETAMENTE ASSENTE)**:
  In `ui/ingestion.py` (riga 470) e `ui/state.py` (riga 292), all'importazione di un file reale `state.set_discovery_results(())` viene inizializzato con una tupla vuota. Non esistendo alcun pulsante o handler per eseguire `TopicDiscoveryAnalyzer`, la schermata mostra sempre 0 risultati.

### E. Dove vengono memorizzati `TopicDiscoveryResult`?
* Vengono memorizzati nella chiave Streamlit `KEY_DISCOVERY_RESULTS = "discovery_results"` gestita in `ui/state.py` (riga 142). In DEMO contiene il risultato sintetico, in FILE una tupla vuota.

### F. Cosa succede ESATTAMENTE quando si preme "Verifica con Topic Detection"?
1. **Verifica Documento e Provenance** (`ui/presentation.py:867-880`):
   Controlla che `doc` sia presente, abbia evidenze e che `hit.metadata["provenance_document_id"] == doc.document_id` tramite `validate_discovery_provenance()`.
2. **Costruzione TopicQuery** (`ui/presentation.py:884-887`):
   Invoca `disc_query = build_topic_query(label=hit.label, description=hit.description)`.
3. **Pre-flight & Model Resolution** (`ui/presentation.py:902-908`):
   Risolve il modello su LM Studio locale tramite `prepare_operational_model()`.
4. **Nuova Inferenza LLM Reale** (`ui/presentation.py:911-918`):
   Esegue `execute_manual_topic_detection(document=doc, topic=disc_query, ...)`.
5. **Persistenza e Aggiornamento Stato** (`ui/presentation.py:920-925`):
   Aggiunge il nuovo `TopicDetectionResult` alla sessione (`add_detection_result`) e aggiorna `last_manual_detection`.
6. **Rendering Riscontro** (`ui/presentation.py:966-1018`):
   Mostra la card di verifica con confronto tra evidenze e decisioni.

### G. Come viene costruito `TopicQuery`?
* Viene costruito deterministicamente da `ui/application.py:build_topic_query(label, description)`:
  - Genera `topic_id = f"custom_{slug}_{hash_suffix}"`
  - Assegna `label` e `description`.

### H. Usa `DiscoveredTopic.label` e `DiscoveredTopic.short_description`?
* **SÌ**: `build_topic_query` riceve esattamente `label=hit.label` e `description=hit.description` (dove `hit.description` mappa `short_description`).

### I. Quale `ConversationEvidenceDocument` viene passato?
* Viene passato il documento attivo corrente memorizzato in `state.get_conversation_document()`.

### J. Verifica di Provenance: `discovery.provenance_document_id == active_document.document_id`?
* **SÌ, BLOCCANTE**: Implementata in `validate_discovery_provenance()` (`ui/application.py:429-444`). Se non concordano, solleva `TopicProvenanceMismatchError` e interrompe immediatamente il flusso prima di qualsiasi chiamata AI.

### K. Gli `evidence_ids` del Discovery vengono passati alla Detection?
* **NO**: Gli `evidence_ids` del Discovery **NON** vengono passati alla Topic Detection né a `TopicQuery`. La Topic Detection riceve unicamente il `ConversationEvidenceDocument` integrale e la query con `label` e `description`. La Detection ricerca le prove in modo totalmente cieco e indipendente.

### L. Viene effettuata una NUOVA chiamata LLM indipendente?
* **SÌ**: Viene eseguita una nuova richiesta REST HTTP `POST /v1/chat/completions` a LM Studio con lo schema `TOPIC_DETECTION_SCHEMA`.

### M. Viene riutilizzato un risultato precedente?
* **NO**: Non viene effettuata alcuna ricerca o cache hit su risultati precedenti.

### N. Viene fatto un semplice search?
* **NO**: Viene invocato il motore LLM per una vera inferenza generativa controllata da schema JSON.

### O. Esiste una conversione artificiale da `DiscoveredTopic` a `TopicDetectionResult`?
* **NO**: Non vi è alcun type casting o coercizione. `DiscoveredTopic` e `TopicDetectionResult` sono tipi distinti e rimangono tali.

### P. Come viene calcolata la consistency?
* **CALCOLATA AL VOLO NELLA UI** (`ui/presentation.py:973-982`):
  - Se `dec_val == "PRESENT"` -> `COERENTE (Discovery e Detection concordano)` (`✅ COERENTE`)
  - Se `dec_val == "ABSENT"` -> `⚠️ DISCREPANZA DISCOVERY → DETECTION`
  - Se `dec_val == "UNCERTAIN"` -> `INCERTO (Evidenza parziale o ambigua)` (`🟡 INCERTO`)
  Non esiste un `ConsistencyService` separato o una classe di dominio.

### Q. Come viene trattato ABSENT?
* Se la Detection restituisce validamente `ABSENT`:
  - `evidence_ids` deve essere rigorosamente vuoto `[]`.
  - La UI mostra un badge grigio `⚪ ABSENT` e un avviso arancione di discrepanza `⚠️ DISCREPANZA DISCOVERY → DETECTION`.

### R. Un errore può diventare ABSENT?
* **NO, IN NESSUN CASO**:
  Tutti i possibili errori sollevano eccezioni specifiche che vengono catturate in blocchi separati:
  - JSON non valido -> `AiStructuredOutputError`
  - Citazione ID non valida/allucinata -> `AiInvalidEvidenceCitationError`
  - Modello disallineato -> `AiModelMismatchError`
  - Timeout -> `AiBackendTimeoutError`
  - Server offline -> `LmStudioUnavailableError`
  Ciascuna eccezione interrompe l'esecuzione e mostra un messaggio di errore rosso `st.error(...)`. Nessun errore produce un risultato con `decision = TopicDecision.ABSENT`.
