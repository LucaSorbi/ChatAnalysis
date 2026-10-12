# AUDIT MULTI-CONVERSAZIONE E STATO DI SESSIONE

Analisi tecnica della gestione di database multi-chat (es. `msgstore.db` con centinaia di chat o report Cellebrite multi-utente), scoping dei risultati AI e rischio di perdita dati.

---

## 1. Creazione e Selezione delle Conversazioni

### A. Raggruppamento e Creazione Documenti (`ui/ingestion.py:406-448`)
* I messaggi unificati (`UnifiedMessage`) vengono partizionati per `chat_id`.
* Per ciascun gruppo viene istanziato un `ConversationEvidenceDocument` con `document_id` deterministico derivato da hash SHA-256 e `chat_key`:
  `doc_id = derive_deterministic_document_id(source_name, file_sha256, chat_key)`
* I documenti vengono registrati in `IngestionResult.documents: dict[str, ConversationEvidenceDocument]`.

### B. Selezione della Conversazione Attiva (`ui/presentation.py:314-350`)
* Nella schermata "2. Importazione", se il file contiene più conversazioni, viene esposto un menu a discesa (`st.selectbox`):
  `"Seleziona conversazione attiva per l'esplorazione e l'analisi"`.
* La modifica della selezione scatena `select_conversation(selected_doc_id)` in `ui/state.py:301-326`.

---

## 2. Scoping dei Risultati AI e Bug Architetturale di Reset

### Codice Sorgente Reale (`ui/state.py:309-325`)
```python
def select_conversation(
    document_id: str,
    state: MutableMapping[str, Any] | None = None,
) -> bool:
    target = _get_target_state(state)
    available = target.get(KEY_AVAILABLE_DOCUMENTS, {})
    if not isinstance(available, dict) or document_id not in available:
        return False

    doc = available[document_id]
    target[KEY_SELECTED_DOCUMENT_ID] = document_id
    target[KEY_CONVERSATION_DOCUMENT] = doc
    target[KEY_DETECTION_RESULTS] = ()     # <--- RESET TOTALE RISULTATI DETECTION!
    target[KEY_DISCOVERY_RESULTS] = ()     # <--- RESET TOTALE RISULTATI DISCOVERY!
    target[KEY_SEARCH_SERVICE] = SearchService(
        document=doc,
        detection_results=(),
        discovery_results=(),
    )
    target[KEY_SELECTED_EVIDENCE_ID] = None
    return True
```

### Valutazione dello Scoping:
1. **Risultati NON Scoped per `document_id`**:
   `KEY_DETECTION_RESULTS` e `KEY_DISCOVERY_RESULTS` sono liste globali piatte nello stato di sessione, **non dizionari indicizzati per `document_id`**.
2. **Perdita Totale dei Risultati AI al Cambio Conversazione**:
   Quando l'investigatore seleziona una conversazione differente tramite il selectbox, `select_conversation()` esegue un **reset forzato e distruttivo**:
   `target[KEY_DETECTION_RESULTS] = ()`
   Tutte le analisi di Topic Detection eseguite fino a quel momento (con relativo consumo di calcolo e tempo) vengono **completamente cancellate e perse**.
   Tornando alla conversazione precedente, la cronologia delle decisioni è azzerata.
3. **Causa della Scelta del Brute-Force Reset**:
   Il reset a vuoto è stato introdotto come workaround per evitare il bug di *cross-contamination* (ovvero impedire che le decisioni e le evidenze della Chat A venissero mostrate durante la visualizzazione della Chat B). Poiché non esiste una struttura `dict[document_id, list[TopicDetectionResult]]`, l'unico modo per non mostrare dati non pertinenti è stato cancellarli.
4. **Comportamento su Rerun Streamlit**:
   Finché l'utente non cambia chat dal menu a discesa, i normali rerun di Streamlit preservano `st.session_state`.

---

## 3. Raccomandazione di Refactoring / Rewrite
Lo stato di sessione deve passare da campi piatti globali a una struttura orientata alla sessione o a un repository in-memory strutturato:
```python
session.detection_results: dict[str, list[TopicDetectionResult]]  # key = document_id
session.discovery_results: dict[str, list[TopicDiscoveryResult]]  # key = document_id
session.search_services: dict[str, SearchService]                 # key = document_id
```
In questo modo, il cambio di conversazione attiva diventerà una semplice variazione del puntatore attivo, preservando integralmente le analisi condotte su ogni singola chat.
