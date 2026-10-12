# AUDIT DELLO STATO DI SESSIONE STREAMLIT (`ui/state.py`)

Analisi completa e sistematica delle chiavi di stato di sessione, del loro ciclo di vita, dei meccanismi di mutazione e delle fragilità architetturali.

---

## 1. Inventario Completo delle Chiavi di Sessione

| Nome Chiave | Tipo Python | Writers Principali | Readers Principali | Condizione di Reset | Comportamento al Rerun |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `dataset_loaded` | `bool` | `load_demo_dataset()`, `set_real_ingestion_result()`, `reset_session_state()` | `is_dataset_loaded()` in tutte le pagine | `reset_session_state()` | Persistente |
| `dataset_mode` | `DatasetMode \| None` | `load_demo_dataset()` (`DEMO`), `set_real_ingestion_result()` (`FILE`) | `get_dataset_mode()`, routing pagine | `reset_session_state()` | Persistente |
| `conversation_document` | `ConversationEvidenceDocument \| None` | `load_demo_dataset()`, `set_real_ingestion_result()`, `select_conversation()` | Pagine 1, 3, 4, 5 (Esplorazione, Ricerca, Topic) | Cambio chat o reset | Persistente |
| `available_documents` | `dict[str, ConversationEvidenceDocument]` | `set_real_ingestion_result()` | `get_available_documents()`, selettore chat | Nuovo upload o reset | Persistente |
| `selected_document_id` | `str \| None` | `set_real_ingestion_result()`, `select_conversation()` | `get_selected_document_id()` | Cambio chat o reset | Persistente |
| `real_ingestion_result` | `IngestionResult \| None` | `set_real_ingestion_result()` | `render_import()`, riepilogo metrico | Nuovo upload o reset | Persistente |
| `ingestion_summary` | `IngestionSummary \| None` | `set_real_ingestion_result()`, `set_ingestion_summary()` | `render_overview()` | Nuovo upload o reset | Persistente |
| `search_service` | `SearchService \| None` | Ingestion, `load_demo_dataset()`, `select_conversation()`, `add_detection_result()` | Ricerca deterministica e filtri Topic | Ricostruito ad ogni add o cambio chat | Persistente |
| `detection_results` | `tuple[TopicDetectionResult, ...]` | `load_demo_dataset()`, `add_detection_result()`, `select_conversation()` (azzera!) | Area 2 Topic Detection, Ricerca Topic | Azzerato brutalmente al cambio conversazione | Persistente sulla stessa chat |
| `discovery_results` | `tuple[TopicDiscoveryResult, ...]` | `load_demo_dataset()`, `select_conversation()` (azzera!) | Tab Topic Discovery | Azzerato al cambio conversazione; sempre vuoto su FILE | Persistente |
| `last_manual_detection` | `TopicDetectionResult \| None` | `set_last_manual_detection()` (dopo ogni run UI) | Card riscontro verifica Discovery -> Detection | Sovrascritto ad ogni nuova inferenza | Persistente |
| `selected_evidence_id` | `str \| None` | `set_selected_evidence_id()` | Expander evidenze in Esplora Conversazione | Cambio chat o deselezione | Volatile |
| `custom_topic_label` | `str` | `set_custom_topic_label()`, input Area 1 | Input text Area 1 | Reset o sovrascrittura | Persistente |
| `custom_topic_description` | `str` | `set_custom_topic_description()` | Textarea Area 1 | Reset o sovrascrittura | Persistente |
| `lm_studio_client` | `BaseLlmClient \| None` | Inizializzazione o injection di test | `execute_manual_topic_detection()` | Mai (singleton per sessione) | Persistente |
| `lm_studio_model` | `str \| None` | `set_lm_studio_model()` | Selettore modello in Area 1 e Discovery | Sovrascritto dopo risoluzione modello | Persistente |
| `error_message` | `str \| None` | `set_error_message()` | Banner errore | Lettura o reset | Volatile |

---

## 2. Fragilità Architetturali e Duplicazioni Rilevate

1. **Stato Piatto e Non Scoped (Fragilità Alta)**:
   I risultati di detection (`detection_results`) e discovery (`discovery_results`) non sono raggruppati per `document_id`. Per evitare che i risultati della Chat 1 compaiano nella Chat 2, `select_conversation()` è costretto ad azzerarli entrambi (`target[KEY_DETECTION_RESULTS] = ()`), causando la perdita irreversibile dei risultati calcolati.
2. **Duplicazione Dati Ingestion (Ridondanza Media)**:
   Lo stato mantiene sia `KEY_REAL_INGESTION_RESULT` sia `KEY_INGESTION_SUMMARY`. In `set_real_ingestion_result()`, il sommario viene duplicato in entrambe le chiavi.
3. **Stato Epimero di Consistency (Fragilità Media)**:
   Non esiste una chiave o struttura per memorizzare la consistenza Discovery → Detection. L'esito (`COERENTE`, `DISCREPANZA`, `INCERTO`) viene calcolato e renderizzato unicamente "al volo" confrontando `last_manual_detection` con `hit.label`. Se l'utente clicca un'altra verifica o ricarica la pagina, il riscontro scompare.
4. **Accoppiamento Forte con Streamlit**:
   Sebbene `ui/state.py` offra un parametro `state` opzionale per i test, l'intero modello di business logic dipende dalla presenza di queste 17 chiavi anziché appoggiarsi a un'istanza di dominio pulita (es. `AnalysisSession`).
