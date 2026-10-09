"""
ui/state.py
-----------
Gestione tipizzata, centralizzata e deterministica dello stato di sessione Streamlit.

Principi:
- CHIAVI CENTRALIZZATE: Nessuna string key sparsa nel codice della UI.
- ADAPTER TIPIZZATO: Tutte le letture e scritture avvengono tramite funzioni tipizzate.
- DETERMINISMO: Reset e caricamento ripristinano lo stato senza causare eccezioni o effetti collaterali.
- TESTABILITÀ: Tutte le funzioni accettano un parametro opzionale `state` (che fa fallback su `st.session_state`),
  permettendo test unitari con semplici dizionari senza richiedere il server Streamlit.
"""
from __future__ import annotations

from typing import Any, Mapping, MutableMapping, Optional

import streamlit as st

from ai.models import (
    ConversationEvidenceDocument,
    TopicDetectionResult,
    TopicDiscoveryResult,
)
from search.service import SearchService
from ui.application import build_demo_session
from ui.models import (
    DatasetMode,
    IngestionResult,
    IngestionStatus,
    IngestionSummary,
)

# Costanti centralizzate per le chiavi di sessione
KEY_DATASET_LOADED = "dataset_loaded"
KEY_DATASET_MODE = "dataset_mode"
KEY_CONVERSATION_DOCUMENT = "conversation_document"
KEY_DETECTION_RESULTS = "detection_results"
KEY_DISCOVERY_RESULTS = "discovery_results"
KEY_SEARCH_SERVICE = "search_service"
KEY_SELECTED_EVIDENCE_ID = "selected_evidence_id"
KEY_ERROR_MESSAGE = "error_message"
KEY_REAL_INGESTION_RESULT = "real_ingestion_result"
KEY_AVAILABLE_DOCUMENTS = "available_documents"
KEY_SELECTED_DOCUMENT_ID = "selected_document_id"
KEY_INGESTION_SUMMARY = "ingestion_summary"
KEY_CUSTOM_TOPIC_LABEL = "custom_topic_label"
KEY_CUSTOM_TOPIC_DESCRIPTION = "custom_topic_description"
KEY_LAST_MANUAL_DETECTION = "last_manual_detection"
KEY_LM_STUDIO_CLIENT = "lm_studio_client"
KEY_LM_STUDIO_MODEL = "lm_studio_model"


def _get_target_state(state: MutableMapping[str, Any] | None = None) -> MutableMapping[str, Any]:
    return state if state is not None else st.session_state


def init_session_state(state: MutableMapping[str, Any] | None = None) -> None:
    """Inizializza le chiavi di stato con i valori predefiniti se non già presenti."""
    target = _get_target_state(state)
    if KEY_DATASET_LOADED not in target:
        target[KEY_DATASET_LOADED] = False
    if KEY_DATASET_MODE not in target:
        target[KEY_DATASET_MODE] = DatasetMode.NONE
    if KEY_CONVERSATION_DOCUMENT not in target:
        target[KEY_CONVERSATION_DOCUMENT] = None
    if KEY_DETECTION_RESULTS not in target:
        target[KEY_DETECTION_RESULTS] = ()
    if KEY_DISCOVERY_RESULTS not in target:
        target[KEY_DISCOVERY_RESULTS] = ()
    if KEY_SEARCH_SERVICE not in target:
        target[KEY_SEARCH_SERVICE] = None
    if KEY_SELECTED_EVIDENCE_ID not in target:
        target[KEY_SELECTED_EVIDENCE_ID] = None
    if KEY_ERROR_MESSAGE not in target:
        target[KEY_ERROR_MESSAGE] = None
    if KEY_REAL_INGESTION_RESULT not in target:
        target[KEY_REAL_INGESTION_RESULT] = None
    if KEY_AVAILABLE_DOCUMENTS not in target:
        target[KEY_AVAILABLE_DOCUMENTS] = {}
    if KEY_SELECTED_DOCUMENT_ID not in target:
        target[KEY_SELECTED_DOCUMENT_ID] = None
    if KEY_INGESTION_SUMMARY not in target:
        target[KEY_INGESTION_SUMMARY] = None
    if KEY_CUSTOM_TOPIC_LABEL not in target:
        target[KEY_CUSTOM_TOPIC_LABEL] = ""
    if KEY_CUSTOM_TOPIC_DESCRIPTION not in target:
        target[KEY_CUSTOM_TOPIC_DESCRIPTION] = ""
    if KEY_LAST_MANUAL_DETECTION not in target:
        target[KEY_LAST_MANUAL_DETECTION] = None
    if KEY_LM_STUDIO_CLIENT not in target:
        target[KEY_LM_STUDIO_CLIENT] = None
    if KEY_LM_STUDIO_MODEL not in target:
        target[KEY_LM_STUDIO_MODEL] = None


def is_dataset_loaded(state: MutableMapping[str, Any] | None = None) -> bool:
    """Restituisce True se un dataset (demo o reale) è attualmente caricato."""
    target = _get_target_state(state)
    return bool(target.get(KEY_DATASET_LOADED, False))


def get_dataset_mode(state: MutableMapping[str, Any] | None = None) -> DatasetMode:
    """Restituisce la modalità del dataset corrente."""
    target = _get_target_state(state)
    val = target.get(KEY_DATASET_MODE, DatasetMode.NONE)
    if isinstance(val, DatasetMode):
        return val
    try:
        return DatasetMode(val)
    except ValueError:
        return DatasetMode.NONE


def get_conversation_document(
    state: MutableMapping[str, Any] | None = None,
) -> Optional[ConversationEvidenceDocument]:
    """Restituisce il documento di conversazione attualmente caricato, o None."""
    target = _get_target_state(state)
    return target.get(KEY_CONVERSATION_DOCUMENT, None)


def get_detection_results(
    state: MutableMapping[str, Any] | None = None,
) -> tuple[TopicDetectionResult, ...]:
    """Restituisce i risultati di Topic Detection caricati."""
    target = _get_target_state(state)
    res = target.get(KEY_DETECTION_RESULTS, ())
    return tuple(res) if isinstance(res, (tuple, list)) else ()


def get_discovery_results(
    state: MutableMapping[str, Any] | None = None,
) -> tuple[TopicDiscoveryResult, ...]:
    """Restituisce i risultati di Topic Discovery caricati."""
    target = _get_target_state(state)
    res = target.get(KEY_DISCOVERY_RESULTS, ())
    return tuple(res) if isinstance(res, (tuple, list)) else ()


def get_search_service(
    state: MutableMapping[str, Any] | None = None,
) -> Optional[SearchService]:
    """Restituisce l'istanza autoritativa di SearchService configurata, o None."""
    target = _get_target_state(state)
    return target.get(KEY_SEARCH_SERVICE, None)


def get_selected_evidence_id(state: MutableMapping[str, Any] | None = None) -> Optional[str]:
    """Restituisce l'evidence_id correntemente selezionato per il dettaglio, se presente."""
    target = _get_target_state(state)
    return target.get(KEY_SELECTED_EVIDENCE_ID, None)


def set_selected_evidence_id(
    evidence_id: Optional[str],
    state: MutableMapping[str, Any] | None = None,
) -> None:
    """Imposta l'evidence_id selezionato."""
    target = _get_target_state(state)
    target[KEY_SELECTED_EVIDENCE_ID] = evidence_id


def get_error_message(state: MutableMapping[str, Any] | None = None) -> Optional[str]:
    """Restituisce l'ultimo messaggio d'errore applicativo registrato."""
    target = _get_target_state(state)
    return target.get(KEY_ERROR_MESSAGE, None)


def set_error_message(
    msg: Optional[str],
    state: MutableMapping[str, Any] | None = None,
) -> None:
    """Registra un errore applicativo."""
    target = _get_target_state(state)
    target[KEY_ERROR_MESSAGE] = msg


def clear_error_message(state: MutableMapping[str, Any] | None = None) -> None:
    """Azzera il messaggio d'errore."""
    target = _get_target_state(state)
    target[KEY_ERROR_MESSAGE] = None


def load_demo_dataset(state: MutableMapping[str, Any] | None = None) -> None:
    """
    Costruisce e carica il dataset dimostrativo sintetico nello stato di sessione,
    ricreando contestualmente il SearchService in modo deterministico.
    """
    doc, detections, discoveries, service = build_demo_session()
    target = _get_target_state(state)
    target[KEY_DATASET_LOADED] = True
    target[KEY_DATASET_MODE] = DatasetMode.DEMO
    target[KEY_CONVERSATION_DOCUMENT] = doc
    target[KEY_DETECTION_RESULTS] = detections
    target[KEY_DISCOVERY_RESULTS] = discoveries
    target[KEY_SEARCH_SERVICE] = service
    target[KEY_SELECTED_EVIDENCE_ID] = None
    target[KEY_ERROR_MESSAGE] = None
    target[KEY_REAL_INGESTION_RESULT] = None
    target[KEY_AVAILABLE_DOCUMENTS] = {}
    target[KEY_SELECTED_DOCUMENT_ID] = None
    target[KEY_INGESTION_SUMMARY] = None


def reset_dataset(state: MutableMapping[str, Any] | None = None) -> None:
    """
    Azzera il dataset e ripristina la sessione allo stato iniziale vuoto.
    """
    target = _get_target_state(state)
    target[KEY_DATASET_LOADED] = False
    target[KEY_DATASET_MODE] = DatasetMode.NONE
    target[KEY_CONVERSATION_DOCUMENT] = None
    target[KEY_DETECTION_RESULTS] = ()
    target[KEY_DISCOVERY_RESULTS] = ()
    target[KEY_SEARCH_SERVICE] = None
    target[KEY_SELECTED_EVIDENCE_ID] = None
    target[KEY_ERROR_MESSAGE] = None
    target[KEY_REAL_INGESTION_RESULT] = None
    target[KEY_AVAILABLE_DOCUMENTS] = {}
    target[KEY_SELECTED_DOCUMENT_ID] = None
    target[KEY_INGESTION_SUMMARY] = None
    target[KEY_CUSTOM_TOPIC_LABEL] = ""
    target[KEY_CUSTOM_TOPIC_DESCRIPTION] = ""
    target[KEY_LAST_MANUAL_DETECTION] = None


def get_real_ingestion_result(
    state: MutableMapping[str, Any] | None = None,
) -> Optional[IngestionResult]:
    """Restituisce il risultato dell'ultima operazione di real ingestion, se presente."""
    target = _get_target_state(state)
    return target.get(KEY_REAL_INGESTION_RESULT, None)


def get_ingestion_summary(
    state: MutableMapping[str, Any] | None = None,
) -> Optional[IngestionSummary]:
    """Restituisce il summary dell'ultima ingestion, se presente."""
    target = _get_target_state(state)
    return target.get(KEY_INGESTION_SUMMARY, None)


def get_available_documents(
    state: MutableMapping[str, Any] | None = None,
) -> dict[str, ConversationEvidenceDocument]:
    """Restituisce la mappatura document_id -> ConversationEvidenceDocument disponibili."""
    target = _get_target_state(state)
    docs = target.get(KEY_AVAILABLE_DOCUMENTS, {})
    return dict(docs) if isinstance(docs, dict) else {}


def get_selected_document_id(
    state: MutableMapping[str, Any] | None = None,
) -> Optional[str]:
    """Restituisce il document_id attualmente selezionato."""
    target = _get_target_state(state)
    return target.get(KEY_SELECTED_DOCUMENT_ID, None)


def set_real_ingestion_result(
    result: IngestionResult,
    state: MutableMapping[str, Any] | None = None,
) -> None:
    """
    Registra il risultato di un'ingestion reale, impostando la modalità FILE,
    azzerando i risultati AI e configurando il documento e SearchService selezionati.
    """
    target = _get_target_state(state)
    target[KEY_REAL_INGESTION_RESULT] = result
    target[KEY_INGESTION_SUMMARY] = result.summary
    target[KEY_DATASET_LOADED] = True
    target[KEY_DATASET_MODE] = DatasetMode.FILE
    target[KEY_DETECTION_RESULTS] = ()
    target[KEY_DISCOVERY_RESULTS] = ()
    target[KEY_SELECTED_EVIDENCE_ID] = None
    target[KEY_ERROR_MESSAGE] = None

    if result.documents:
        if isinstance(result.documents, Mapping):
            docs_map = dict(result.documents)
            first_doc = next(iter(result.documents.values()))
        else:
            docs_map = {doc.document_id: doc for doc in result.documents}
            first_doc = result.documents[0]

        target[KEY_AVAILABLE_DOCUMENTS] = docs_map
        target[KEY_SELECTED_DOCUMENT_ID] = first_doc.document_id
        target[KEY_CONVERSATION_DOCUMENT] = first_doc
        target[KEY_SEARCH_SERVICE] = SearchService(
            document=first_doc,
            detection_results=(),
            discovery_results=(),
        )
    else:
        target[KEY_AVAILABLE_DOCUMENTS] = {}
        target[KEY_SELECTED_DOCUMENT_ID] = None
        target[KEY_CONVERSATION_DOCUMENT] = None
        target[KEY_SEARCH_SERVICE] = None


def select_conversation(
    document_id: str,
    state: MutableMapping[str, Any] | None = None,
) -> bool:
    """
    Seleziona una conversazione tra quelle disponibili per il dataset reale corrente.
    Ricostruisce in modo deterministico il SearchService per il documento selezionato.
    """
    target = _get_target_state(state)
    available = target.get(KEY_AVAILABLE_DOCUMENTS, {})
    if not isinstance(available, dict) or document_id not in available:
        return False

    doc = available[document_id]
    target[KEY_SELECTED_DOCUMENT_ID] = document_id
    target[KEY_CONVERSATION_DOCUMENT] = doc
    target[KEY_DETECTION_RESULTS] = ()
    target[KEY_DISCOVERY_RESULTS] = ()
    target[KEY_SEARCH_SERVICE] = SearchService(
        document=doc,
        detection_results=(),
        discovery_results=(),
    )
    target[KEY_SELECTED_EVIDENCE_ID] = None
    return True


def get_custom_topic_label(state: MutableMapping[str, Any] | None = None) -> str:
    """Restituisce il valore corrente per il campo argomento libero."""
    target = _get_target_state(state)
    return str(target.get(KEY_CUSTOM_TOPIC_LABEL, ""))


def set_custom_topic_label(
    label: str,
    state: MutableMapping[str, Any] | None = None,
) -> None:
    """Imposta il valore del campo argomento libero."""
    target = _get_target_state(state)
    target[KEY_CUSTOM_TOPIC_LABEL] = label


def get_custom_topic_description(state: MutableMapping[str, Any] | None = None) -> str:
    """Restituisce la descrizione opzionale del topic."""
    target = _get_target_state(state)
    return str(target.get(KEY_CUSTOM_TOPIC_DESCRIPTION, ""))


def set_custom_topic_description(
    description: str,
    state: MutableMapping[str, Any] | None = None,
) -> None:
    """Imposta la descrizione opzionale del topic."""
    target = _get_target_state(state)
    target[KEY_CUSTOM_TOPIC_DESCRIPTION] = description


def get_last_manual_detection(
    state: MutableMapping[str, Any] | None = None,
) -> Optional[TopicDetectionResult]:
    """Restituisce il risultato dell'ultima Topic Detection manuale eseguita."""
    target = _get_target_state(state)
    return target.get(KEY_LAST_MANUAL_DETECTION, None)


def set_last_manual_detection(
    result: Optional[TopicDetectionResult],
    state: MutableMapping[str, Any] | None = None,
) -> None:
    """Memorizza il risultato dell'ultima Topic Detection manuale eseguita."""
    target = _get_target_state(state)
    target[KEY_LAST_MANUAL_DETECTION] = result


def get_lm_studio_client(
    state: MutableMapping[str, Any] | None = None,
) -> Any:
    """Restituisce l'eventuale client LM Studio custom/iniettato per test."""
    target = _get_target_state(state)
    return target.get(KEY_LM_STUDIO_CLIENT, None)


def set_lm_studio_client(
    client: Any,
    state: MutableMapping[str, Any] | None = None,
) -> None:
    """Imposta o inietta un client LM Studio per la sessione (usato nei test o per override)."""
    target = _get_target_state(state)
    target[KEY_LM_STUDIO_CLIENT] = client


def get_lm_studio_model(
    state: MutableMapping[str, Any] | None = None,
) -> Optional[str]:
    """Restituisce il model_id LM Studio configurato per la sessione."""
    target = _get_target_state(state)
    return target.get(KEY_LM_STUDIO_MODEL, None)


def set_lm_studio_model(
    model_id: Optional[str],
    state: MutableMapping[str, Any] | None = None,
) -> None:
    """Imposta il model_id LM Studio configurato per la sessione."""
    target = _get_target_state(state)
    target[KEY_LM_STUDIO_MODEL] = model_id


def add_detection_result(
    result: TopicDetectionResult,
    state: MutableMapping[str, Any] | None = None,
) -> None:
    """
    Aggiunge un risultato di Topic Detection alla sessione corrente,
    sostituendo eventuali risultati preesistenti con lo stesso topic_id
    (o stessa coppia label/provenance) per evitare duplicati identici,
    e sincronizza deterministicamente il SearchService senza alterare
    il ConversationEvidenceDocument originale.
    """
    target = _get_target_state(state)
    current = list(get_detection_results(state))

    existing_idx = None
    for idx, r in enumerate(current):
        if r.topic.topic_id == result.topic.topic_id:
            existing_idx = idx
            break
        if (
            r.topic.label == result.topic.label
            and r.provenance_document_id == result.provenance_document_id
        ):
            existing_idx = idx
            break

    if existing_idx is not None:
        current[existing_idx] = result
    else:
        current.insert(0, result)

    target[KEY_DETECTION_RESULTS] = tuple(current)

    doc = get_conversation_document(state)
    discoveries = get_discovery_results(state)
    if doc is not None:
        target[KEY_SEARCH_SERVICE] = SearchService(
            document=doc,
            detection_results=tuple(current),
            discovery_results=discoveries,
        )


