"""
ui/state.py
-----------
Gestione tipizzata, centralizzata, deterministica e scoped per documento
dello stato di sessione Streamlit.

Principi architetturali:
- CHIAVI CENTRALIZZATE: Nessuna string key sparsa nel codice della UI.
- SCOPING PER DOCUMENT_ID: I risultati di Topic Detection, Topic Discovery, Consistency e
  last_manual_detection sono associati deterministicamente al document_id corrente (KEY_ANALYSIS_BY_DOCUMENT).
- PRESERVAZIONE MULTI-CONVERSAZIONE: Il cambio conversazione (Chat A -> Chat B -> Chat A)
  non cancella né mescola i risultati tra chat distinte.
- SEPARAZIONE DEMO: Risultati di riferimento sintetici separati dai risultati live.
  All'importazione (DEMO o FILE) i risultati live partono puliti.
- TESTABILITÀ: Tutte le funzioni accettano un parametro opzionale `state` (fallback `st.session_state`),
  permettendo test deterministici senza runtime Streamlit.
"""
from __future__ import annotations

from typing import Any, Mapping, MutableMapping, Optional, Sequence

import streamlit as st

from ai.models import (
    ConversationEvidenceDocument,
    TopicDetectionResult,
    TopicDiscoveryResult,
)
from analysis.models import ConsistencyResult, DocumentAnalysisState
from search.service import SearchService
from ui.demo import build_synthetic_demo_dataset
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
KEY_ANALYSIS_BY_DOCUMENT = "analysis_by_document"
KEY_REFERENCE_DETECTION_RESULTS = "reference_detection_results"
KEY_REFERENCE_DISCOVERY_RESULTS = "reference_discovery_results"


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
        target[KEY_LM_STUDIO_MODEL] = "qwen2.5-7b-instruct"
    if KEY_ANALYSIS_BY_DOCUMENT not in target:
        target[KEY_ANALYSIS_BY_DOCUMENT] = {}
    if KEY_REFERENCE_DETECTION_RESULTS not in target:
        target[KEY_REFERENCE_DETECTION_RESULTS] = ()
    if KEY_REFERENCE_DISCOVERY_RESULTS not in target:
        target[KEY_REFERENCE_DISCOVERY_RESULTS] = ()


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
    """Restituisce il documento di conversazione attualmente attivo, o None."""
    target = _get_target_state(state)
    return target.get(KEY_CONVERSATION_DOCUMENT, None)


def get_document_analysis_state(
    document_id: str,
    state: MutableMapping[str, Any] | None = None,
) -> DocumentAnalysisState:
    """Restituisce (o crea se assente) lo stato di analisi tipizzato per lo specifico document_id."""
    target = _get_target_state(state)
    analysis_map = target.setdefault(KEY_ANALYSIS_BY_DOCUMENT, {})
    if document_id not in analysis_map:
        existing_det = target.get(KEY_DETECTION_RESULTS, ())
        existing_disc = target.get(KEY_DISCOVERY_RESULTS, ())
        existing_last = target.get(KEY_LAST_MANUAL_DETECTION, None)
        analysis_map[document_id] = DocumentAnalysisState(
            document_id=document_id,
            detection_results=tuple(existing_det) if isinstance(existing_det, (list, tuple)) else (),
            discovery_results=tuple(existing_disc) if isinstance(existing_disc, (list, tuple)) else (),
            last_detection=existing_last if isinstance(existing_last, TopicDetectionResult) else None,
        )
    return analysis_map[document_id]


def get_active_analysis_state(
    state: MutableMapping[str, Any] | None = None,
) -> Optional[DocumentAnalysisState]:
    """Restituisce lo stato di analisi associato al documento attivo corrente, o None."""
    doc = get_conversation_document(state)
    if doc is None:
        return None
    return get_document_analysis_state(doc.document_id, state=state)


def get_detection_results(
    state: MutableMapping[str, Any] | None = None,
) -> tuple[TopicDetectionResult, ...]:
    """Restituisce i risultati di Topic Detection per la conversazione attiva."""
    active_analysis = get_active_analysis_state(state)
    if active_analysis is not None:
        return active_analysis.detection_results
    target = _get_target_state(state)
    res = target.get(KEY_DETECTION_RESULTS, ())
    return tuple(res) if isinstance(res, (tuple, list)) else ()


def set_detection_results(
    results: Sequence[TopicDetectionResult],
    state: MutableMapping[str, Any] | None = None,
) -> None:
    """Imposta i risultati di Topic Detection per la conversazione attiva."""
    target = _get_target_state(state)
    res_tuple = tuple(results)
    target[KEY_DETECTION_RESULTS] = res_tuple
    active_analysis = get_active_analysis_state(state)
    if active_analysis is not None:
        active_analysis.detection_results = res_tuple
    doc = get_conversation_document(state)
    if doc is not None:
        disc = get_discovery_results(state)
        target[KEY_SEARCH_SERVICE] = SearchService(
            document=doc,
            detection_results=res_tuple,
            discovery_results=disc,
        )


def get_discovery_results(
    state: MutableMapping[str, Any] | None = None,
) -> tuple[TopicDiscoveryResult, ...]:
    """Restituisce i risultati di Topic Discovery per la conversazione attiva."""
    active_analysis = get_active_analysis_state(state)
    if active_analysis is not None:
        return active_analysis.discovery_results
    target = _get_target_state(state)
    res = target.get(KEY_DISCOVERY_RESULTS, ())
    return tuple(res) if isinstance(res, (tuple, list)) else ()


def set_discovery_results(
    results: Sequence[TopicDiscoveryResult] | TopicDiscoveryResult,
    state: MutableMapping[str, Any] | None = None,
) -> None:
    """Imposta i risultati di Topic Discovery per la conversazione attiva."""
    target = _get_target_state(state)
    if isinstance(results, TopicDiscoveryResult):
        res_tuple = (results,)
    else:
        res_tuple = tuple(results)
    target[KEY_DISCOVERY_RESULTS] = res_tuple
    active_analysis = get_active_analysis_state(state)
    if active_analysis is not None:
        active_analysis.discovery_results = res_tuple
        if res_tuple:
            active_analysis.last_discovery = res_tuple[-1]
    doc = get_conversation_document(state)
    if doc is not None:
        det = get_detection_results(state)
        target[KEY_SEARCH_SERVICE] = SearchService(
            document=doc,
            detection_results=det,
            discovery_results=res_tuple,
        )


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
    Costruisce e carica il dataset dimostrativo sintetico nello stato di sessione.
    Conformemente alla FASE 3 dell'architettura:
    - Inizializza il ConversationEvidenceDocument.
    - I risultati sintetici di riferimento sono registrati separatamente in KEY_REFERENCE_*_RESULTS.
    - I risultati LIVE partono inizialmente vuoti (), consentendo l'avvio su richiesta
      di vera Topic Detection e vera Topic Discovery (esattamente come in modalità FILE).
    """
    doc, ref_detections, ref_discoveries = build_synthetic_demo_dataset()
    target = _get_target_state(state)
    target[KEY_DATASET_LOADED] = True
    target[KEY_DATASET_MODE] = DatasetMode.DEMO
    target[KEY_CONVERSATION_DOCUMENT] = doc
    target[KEY_REFERENCE_DETECTION_RESULTS] = ref_detections
    target[KEY_REFERENCE_DISCOVERY_RESULTS] = ref_discoveries
    target[KEY_DETECTION_RESULTS] = ()
    target[KEY_DISCOVERY_RESULTS] = ()
    target[KEY_SEARCH_SERVICE] = SearchService(
        document=doc,
        detection_results=(),
        discovery_results=(),
    )
    target[KEY_SELECTED_EVIDENCE_ID] = None
    target[KEY_ERROR_MESSAGE] = None
    target[KEY_REAL_INGESTION_RESULT] = None
    target[KEY_AVAILABLE_DOCUMENTS] = {doc.document_id: doc}
    target[KEY_SELECTED_DOCUMENT_ID] = doc.document_id
    target[KEY_INGESTION_SUMMARY] = None

    # Inizializza lo stato document-scoped per la demo
    demo_analysis = DocumentAnalysisState(document_id=doc.document_id)
    target[KEY_ANALYSIS_BY_DOCUMENT] = {doc.document_id: demo_analysis}


def get_demo_reference_detection_results(
    state: MutableMapping[str, Any] | None = None,
) -> tuple[TopicDetectionResult, ...]:
    """Restituisce i risultati di Topic Detection di riferimento sintetico (DEMO)."""
    target = _get_target_state(state)
    res = target.get(KEY_REFERENCE_DETECTION_RESULTS, ())
    return tuple(res) if isinstance(res, (tuple, list)) else ()


def get_demo_reference_discovery_results(
    state: MutableMapping[str, Any] | None = None,
) -> tuple[TopicDiscoveryResult, ...]:
    """Restituisce i risultati di Topic Discovery di riferimento sintetico (DEMO)."""
    target = _get_target_state(state)
    res = target.get(KEY_REFERENCE_DISCOVERY_RESULTS, ())
    return tuple(res) if isinstance(res, (tuple, list)) else ()


# Alias per compatibilità di test e documentazione
get_reference_detection_results = get_demo_reference_detection_results
get_reference_discovery_results = get_demo_reference_discovery_results


def reset_dataset(state: MutableMapping[str, Any] | None = None) -> None:
    """Azzera il dataset e ripristina la sessione allo stato iniziale vuoto."""
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
    target[KEY_ANALYSIS_BY_DOCUMENT] = {}
    target[KEY_REFERENCE_DETECTION_RESULTS] = ()
    target[KEY_REFERENCE_DISCOVERY_RESULTS] = ()


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
    azzerando i risultati AI e configurando i documenti disponibili e lo stato scoped.
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
    target[KEY_ANALYSIS_BY_DOCUMENT] = {}

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
        # Inizializza gli stati document-scoped per tutti i documenti importati
        target[KEY_ANALYSIS_BY_DOCUMENT] = {
            doc_id: DocumentAnalysisState(document_id=doc_id) for doc_id in docs_map
        }
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
    Seleziona una conversazione tra quelle disponibili per il dataset corrente.
    Preserva rigorosamente l'isolamento multi-conversazione (FASE 2, FASE 25):
    - Recupera i risultati di Topic Detection e Topic Discovery specifici per document_id.
    - Ripristina il SearchService sincronizzato con i soli risultati della chat selezionata.
    - NON cancella né sovrascrive i risultati della conversazione precedente.
    """
    target = _get_target_state(state)
    available = target.get(KEY_AVAILABLE_DOCUMENTS, {})
    if not isinstance(available, dict) or document_id not in available:
        return False

    doc = available[document_id]
    target[KEY_SELECTED_DOCUMENT_ID] = document_id
    target[KEY_CONVERSATION_DOCUMENT] = doc

    # Recupera o crea lo stato scoped per la conversazione selezionata
    analysis_map = target.setdefault(KEY_ANALYSIS_BY_DOCUMENT, {})
    if document_id not in analysis_map:
        analysis_map[document_id] = DocumentAnalysisState(document_id=document_id)

    doc_analysis: DocumentAnalysisState = analysis_map[document_id]

    # Ripristina i risultati salvati per questa conversazione
    target[KEY_DETECTION_RESULTS] = doc_analysis.detection_results
    target[KEY_DISCOVERY_RESULTS] = doc_analysis.discovery_results
    target[KEY_LAST_MANUAL_DETECTION] = doc_analysis.last_detection
    target[KEY_SEARCH_SERVICE] = SearchService(
        document=doc,
        detection_results=doc_analysis.detection_results,
        discovery_results=doc_analysis.discovery_results,
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
    """Restituisce il risultato dell'ultima Topic Detection manuale eseguita sulla conversazione attiva."""
    active_analysis = get_active_analysis_state(state)
    if active_analysis is not None:
        return active_analysis.last_detection
    target = _get_target_state(state)
    return target.get(KEY_LAST_MANUAL_DETECTION, None)


def set_last_manual_detection(
    result: Optional[TopicDetectionResult],
    state: MutableMapping[str, Any] | None = None,
) -> None:
    """Memorizza il risultato dell'ultima Topic Detection manuale per la conversazione attiva."""
    target = _get_target_state(state)
    target[KEY_LAST_MANUAL_DETECTION] = result
    active_analysis = get_active_analysis_state(state)
    if active_analysis is not None:
        active_analysis.last_detection = result


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
    """Restituisce il model_id LM Studio configurato per la sessione (default: qwen2.5-7b-instruct)."""
    target = _get_target_state(state)
    return target.get(KEY_LM_STUDIO_MODEL) or "qwen2.5-7b-instruct"


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
    Aggiunge un risultato di Topic Detection alla conversazione corretta in modo document-scoped,
    sostituendo eventuali risultati preesistenti con lo stesso topic_id per evitare duplicati,
    e sincronizza deterministicamente il SearchService.
    """
    target = _get_target_state(state)
    doc_id = result.provenance_document_id

    analysis_map = target.setdefault(KEY_ANALYSIS_BY_DOCUMENT, {})
    if doc_id not in analysis_map:
        analysis_map[doc_id] = DocumentAnalysisState(document_id=doc_id)

    doc_analysis: DocumentAnalysisState = analysis_map[doc_id]
    current = list(doc_analysis.detection_results)

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

    doc_analysis.detection_results = tuple(current)
    doc_analysis.last_detection = result

    # Se corrisponde al documento attivo corrente, sincronizza le viste attive
    active_doc = get_conversation_document(state)
    if active_doc is not None and active_doc.document_id == doc_id:
        target[KEY_DETECTION_RESULTS] = tuple(current)
        target[KEY_LAST_MANUAL_DETECTION] = result
        target[KEY_SEARCH_SERVICE] = SearchService(
            document=active_doc,
            detection_results=tuple(current),
            discovery_results=doc_analysis.discovery_results,
        )


def add_discovery_result(
    result: TopicDiscoveryResult,
    state: MutableMapping[str, Any] | None = None,
) -> None:
    """
    Aggiunge o imposta un risultato di Topic Discovery in modo document-scoped
    e sincronizza il SearchService per il documento attivo.
    """
    target = _get_target_state(state)
    doc_id = result.provenance_document_id

    analysis_map = target.setdefault(KEY_ANALYSIS_BY_DOCUMENT, {})
    if doc_id not in analysis_map:
        analysis_map[doc_id] = DocumentAnalysisState(document_id=doc_id)

    doc_analysis: DocumentAnalysisState = analysis_map[doc_id]
    doc_analysis.discovery_results = (result,)
    doc_analysis.last_discovery = result

    active_doc = get_conversation_document(state)
    if active_doc is not None and active_doc.document_id == doc_id:
        target[KEY_DISCOVERY_RESULTS] = (result,)
        target[KEY_SEARCH_SERVICE] = SearchService(
            document=active_doc,
            detection_results=doc_analysis.detection_results,
            discovery_results=(result,),
        )


def add_consistency_result(
    result: ConsistencyResult,
    state: MutableMapping[str, Any] | None = None,
) -> None:
    """Aggiunge un ConsistencyResult allo stato scoped del rispettivo documento."""
    target = _get_target_state(state)
    doc_id = result.document_id

    analysis_map = target.setdefault(KEY_ANALYSIS_BY_DOCUMENT, {})
    if doc_id not in analysis_map:
        analysis_map[doc_id] = DocumentAnalysisState(document_id=doc_id)

    doc_analysis: DocumentAnalysisState = analysis_map[doc_id]
    doc_analysis.consistency_results = doc_analysis.consistency_results + (result,)


def get_consistency_results(
    document_id: str | None = None,
    state: MutableMapping[str, Any] | None = None,
) -> tuple[ConsistencyResult, ...]:
    """Restituisce i risultati di consistency per il documento specificato o per quello attivo."""
    target = _get_target_state(state)
    target_id = document_id or (get_selected_document_id(state))
    if not target_id:
        return ()
    analysis_map = target.get(KEY_ANALYSIS_BY_DOCUMENT, {})
    if target_id in analysis_map:
        return analysis_map[target_id].consistency_results
    return ()
