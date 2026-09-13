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

