"""
ui/state.py
-----------
Gestione tipizzata, centralizzata, deterministica e scoped per documento
dello stato di sessione Streamlit.

Principi architetturali:
- UNICA FONTE DI VERITÀ (FASE 2): Tutti i risultati AI (Topic Detection, Topic Discovery, Consistency)
  risiedono ESCLUSIVAMENTE in `DocumentAnalysisState` indicizzato per `document_id`
  (`KEY_ANALYSIS_BY_DOCUMENT`). Nessuna duplicazione di stato o sincronizzazione manuale.
- PURE DERIVED VIEWS: Le funzioni di lettura per la conversazione attiva sono viste pure derivate
  dallo stato scoped del documento attivo.
- CACHING EFFICIENTE DELL'INDICE (FASE 3): L'EvidenceIndex deterministico per ogni documento
  viene costruito UNA SOLA VOLTA e riutilizzato da SearchService senza reindicizzazioni continue.
- ISOLAMENTO MULTI-CONVERSAZIONE (FASE 2, 25): Il cambio conversazione (A -> B -> A)
  preserva integralmente i risultati di ogni chat senza contaminazione o perdita.
- SEPARAZIONE RIGOROSA DEMO: Dati di riferimento sintetici separati dai risultati live.
"""
from __future__ import annotations

from typing import Any, Mapping, MutableMapping, Optional, Sequence

import streamlit as st

from ai.models import (
    ConversationEvidenceDocument,
    TopicDetectionResult,
    TopicDiscoveryResult,
)
from analysis.models import ConsistencyResult, DocumentAnalysisState, OperationalProfile
from search.index import EvidenceIndex
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
KEY_SELECTED_EVIDENCE_ID = "selected_evidence_id"
KEY_ERROR_MESSAGE = "error_message"
KEY_REAL_INGESTION_RESULT = "real_ingestion_result"
KEY_AVAILABLE_DOCUMENTS = "available_documents"
KEY_SELECTED_DOCUMENT_ID = "selected_document_id"
KEY_INGESTION_SUMMARY = "ingestion_summary"
KEY_CUSTOM_TOPIC_LABEL = "custom_topic_label"
KEY_CUSTOM_TOPIC_DESCRIPTION = "custom_topic_description"
KEY_LM_STUDIO_CLIENT = "lm_studio_client"
KEY_LM_STUDIO_MODEL = "lm_studio_model"
KEY_ANALYSIS_BY_DOCUMENT = "analysis_by_document"
KEY_REFERENCE_DETECTION_RESULTS = "reference_detection_results"
KEY_REFERENCE_DISCOVERY_RESULTS = "reference_discovery_results"
KEY_DETECTION_RESULTS = "detection_results"
KEY_DISCOVERY_RESULTS = "discovery_results"
KEY_LAST_MANUAL_DETECTION = "last_manual_detection"
KEY_SEARCH_SERVICE = "search_service"
KEY_MEDIA_REGISTRIES_BY_DOCUMENT = "media_registries_by_document"
KEY_DISCOVERY_RUN_STORE_BY_DOCUMENT = "discovery_run_store_by_document"
KEY_AUTHORITATIVE_LIVE_RUN_IDS = "authoritative_live_run_ids"
KEY_OPERATIONAL_PROFILE = "operational_profile"
KEY_CHECKPOINT_STORE = "checkpoint_store"


class _SessionStateAdapter:
    """Adattatore trasparente per oggetti Streamlit SessionState / SafeSessionState che non implementano get() o setdefault()."""
    def __init__(self, raw: Any) -> None:
        self._raw = raw

    def __getitem__(self, key: str) -> Any:
        return self._raw[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._raw[key] = value

    def __delitem__(self, key: str) -> None:
        del self._raw[key]

    def __contains__(self, key: object) -> bool:
        return key in self._raw

    def get(self, key: str, default: Any = None) -> Any:
        try:
            if key in self._raw:
                return self._raw[key]
        except (KeyError, TypeError, AttributeError):
            pass
        return default

    def setdefault(self, key: str, default: Any = None) -> Any:
        try:
            if key in self._raw:
                return self._raw[key]
        except (KeyError, TypeError, AttributeError):
            pass
        self._raw[key] = default
        return default


def _get_target_state(state: Any = None) -> Any:
    raw = state if state is not None else st.session_state
    if isinstance(raw, dict):
        return raw
    return _SessionStateAdapter(raw)


def init_session_state(state: MutableMapping[str, Any] | None = None) -> None:
    """Inizializza le chiavi di stato con i valori predefiniti se non già presenti."""
    target = _get_target_state(state)
    if KEY_DATASET_LOADED not in target:
        target[KEY_DATASET_LOADED] = False
    if KEY_DATASET_MODE not in target:
        target[KEY_DATASET_MODE] = DatasetMode.NONE
    if KEY_CONVERSATION_DOCUMENT not in target:
        target[KEY_CONVERSATION_DOCUMENT] = None
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
    if KEY_MEDIA_REGISTRIES_BY_DOCUMENT not in target:
        target[KEY_MEDIA_REGISTRIES_BY_DOCUMENT] = {}
    if KEY_DISCOVERY_RUN_STORE_BY_DOCUMENT not in target:
        target[KEY_DISCOVERY_RUN_STORE_BY_DOCUMENT] = {}
    if KEY_AUTHORITATIVE_LIVE_RUN_IDS not in target:
        target[KEY_AUTHORITATIVE_LIVE_RUN_IDS] = set()
    if KEY_OPERATIONAL_PROFILE not in target:
        target[KEY_OPERATIONAL_PROFILE] = OperationalProfile.BALANCED.value
    if KEY_CHECKPOINT_STORE not in target:
        target[KEY_CHECKPOINT_STORE] = None


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
    doc = target.get(KEY_CONVERSATION_DOCUMENT, None)
    if doc is not None:
        return doc
    sel_id = target.get(KEY_SELECTED_DOCUMENT_ID)
    if sel_id:
        available = target.get(KEY_AVAILABLE_DOCUMENTS, {})
        return available.get(sel_id)
    return None


def get_document_analysis_state(
    document_id: str,
    state: MutableMapping[str, Any] | None = None,
) -> DocumentAnalysisState:
    """
    Restituisce (o crea isolatamente se assente) lo stato di analisi tipizzato per lo specifico document_id.
    Non eredita MAI dati appartenenti a conversazioni precedenti.
    """
    target = _get_target_state(state)
    analysis_map = target.setdefault(KEY_ANALYSIS_BY_DOCUMENT, {})
    if document_id not in analysis_map:
        init_dets: list[TopicDetectionResult] = []
        raw_dets = target.get(KEY_DETECTION_RESULTS)
        if isinstance(raw_dets, (tuple, list)):
            init_dets = [r for r in raw_dets if getattr(r, "provenance_document_id", None) == document_id]

        init_discs: list[TopicDiscoveryResult] = []
        raw_discs = target.get(KEY_DISCOVERY_RESULTS)
        if isinstance(raw_discs, (tuple, list)):
            init_discs = [r for r in raw_discs if getattr(r, "provenance_document_id", None) == document_id]

        analysis_map[document_id] = DocumentAnalysisState(
            document_id=document_id,
            detection_runs=tuple(init_dets),
            discovery_runs=tuple(init_discs),
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


# --- Pure Derived Views per la conversazione attiva ---

def get_detection_results(
    state: MutableMapping[str, Any] | None = None,
) -> tuple[TopicDetectionResult, ...]:
    """Restituisce i risultati di Topic Detection per la conversazione attiva (pure derived view)."""
    active_analysis = get_active_analysis_state(state)
    if active_analysis is not None:
        return active_analysis.detection_results
    return ()


def set_detection_results(
    results: Sequence[TopicDetectionResult],
    state: MutableMapping[str, Any] | None = None,
) -> None:
    """Imposta i risultati di Topic Detection nello stato scoped del documento attivo."""
    active_analysis = get_active_analysis_state(state)
    if active_analysis is not None:
        active_analysis.detection_results = tuple(results)


def get_discovery_results(
    state: MutableMapping[str, Any] | None = None,
) -> tuple[TopicDiscoveryResult, ...]:
    """Restituisce i risultati di Topic Discovery per la conversazione attiva (pure derived view)."""
    active_analysis = get_active_analysis_state(state)
    if active_analysis is not None:
        return active_analysis.discovery_results
    return ()


def set_discovery_results(
    results: Sequence[TopicDiscoveryResult] | TopicDiscoveryResult,
    state: MutableMapping[str, Any] | None = None,
) -> None:
    """Imposta i risultati di Topic Discovery nello stato scoped del documento attivo."""
    active_analysis = get_active_analysis_state(state)
    if active_analysis is not None:
        if isinstance(results, TopicDiscoveryResult):
            active_analysis.discovery_results = (results,)
        else:
            active_analysis.discovery_results = tuple(results)


def get_search_service(
    state: MutableMapping[str, Any] | None = None,
) -> Optional[SearchService]:
    """
    Restituisce un'istanza SearchService configurata per il documento attivo,
    riutilizzando l'EvidenceIndex indicizzato una sola volta in RAM.
    """
    doc = get_conversation_document(state)
    if doc is None:
        return None

    active_analysis = get_active_analysis_state(state)
    if active_analysis is None:
        return None

    # Indicizzazione deterministica ONE-TIME per document_id
    if active_analysis.cached_evidence_index is None:
        active_analysis.cached_evidence_index = EvidenceIndex.from_document(doc)

    return SearchService(
        index=active_analysis.cached_evidence_index,
        detection_results=active_analysis.detection_results,
        discovery_results=active_analysis.discovery_results,
    )


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
    - Inizializza il ConversationEvidenceDocument con data_classification=SYNTHETIC_DEMO.
    - I risultati sintetici di riferimento sono registrati separatamente in KEY_REFERENCE_*_RESULTS.
    - I risultati LIVE partono inizialmente vuoti (), consentendo l'avvio su richiesta
      di vera Topic Detection e vera Topic Discovery.
    """
    doc, ref_detections, ref_discoveries = build_synthetic_demo_dataset()
    target = _get_target_state(state)

    # Pulisce registri media del dataset precedente prima di attivare la demo
    old_regs: set[Any] = set()
    raw_old = target.get(KEY_MEDIA_REGISTRIES_BY_DOCUMENT)
    if isinstance(raw_old, dict):
        for reg in raw_old.values():
            if reg is not None:
                old_regs.add(reg)
    target[KEY_MEDIA_REGISTRIES_BY_DOCUMENT] = {}
    for old_reg in old_regs:
        if hasattr(old_reg, "clear"):
            try:
                old_reg.clear()
            except Exception:
                pass

    target[KEY_DATASET_LOADED] = True
    target[KEY_DATASET_MODE] = DatasetMode.DEMO
    target[KEY_CONVERSATION_DOCUMENT] = doc
    target[KEY_REFERENCE_DETECTION_RESULTS] = ref_detections
    target[KEY_REFERENCE_DISCOVERY_RESULTS] = ref_discoveries
    target[KEY_SELECTED_EVIDENCE_ID] = None
    target[KEY_ERROR_MESSAGE] = None
    target[KEY_REAL_INGESTION_RESULT] = None
    target[KEY_AVAILABLE_DOCUMENTS] = {doc.document_id: doc}
    target[KEY_SELECTED_DOCUMENT_ID] = doc.document_id
    target[KEY_INGESTION_SUMMARY] = None

    # Inizializza lo stato document-scoped pulito per la demo con indice cached
    demo_analysis = DocumentAnalysisState(
        document_id=doc.document_id,
        cached_evidence_index=EvidenceIndex.from_document(doc),
    )
    target[KEY_ANALYSIS_BY_DOCUMENT] = {doc.document_id: demo_analysis}

    # Le run LIVE di discovery iniziano vuote (0); le reference sintetiche NON vanno nel live store!
    target[KEY_DISCOVERY_RUN_STORE_BY_DOCUMENT] = {}
    target[KEY_AUTHORITATIVE_LIVE_RUN_IDS] = set()


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
    target[KEY_SELECTED_EVIDENCE_ID] = None
    target[KEY_ERROR_MESSAGE] = None
    target[KEY_REAL_INGESTION_RESULT] = None
    target[KEY_AVAILABLE_DOCUMENTS] = {}
    target[KEY_SELECTED_DOCUMENT_ID] = None
    target[KEY_INGESTION_SUMMARY] = None
    target[KEY_CUSTOM_TOPIC_LABEL] = ""
    target[KEY_CUSTOM_TOPIC_DESCRIPTION] = ""
    target[KEY_ANALYSIS_BY_DOCUMENT] = {}
    target[KEY_REFERENCE_DETECTION_RESULTS] = ()
    target[KEY_REFERENCE_DISCOVERY_RESULTS] = ()
    target[KEY_DISCOVERY_RUN_STORE_BY_DOCUMENT] = {}
    target[KEY_AUTHORITATIVE_LIVE_RUN_IDS] = set()
    media_regs = target.get(KEY_MEDIA_REGISTRIES_BY_DOCUMENT, {})
    if isinstance(media_regs, dict):
        for reg in set(media_regs.values()):
            if hasattr(reg, "clear"):
                try:
                    reg.clear()
                except Exception:
                    pass
    target[KEY_MEDIA_REGISTRIES_BY_DOCUMENT] = {}


def get_media_asset_registry(
    document_id: str | None = None,
    state: MutableMapping[str, Any] | None = None,
) -> Any:
    """Restituisce il MediaAssetRegistry isolato per il documento specificato o attivo."""
    target = _get_target_state(state)
    target_id = document_id or get_selected_document_id(state) or "default"
    regs = target.setdefault(KEY_MEDIA_REGISTRIES_BY_DOCUMENT, {})
    if target_id not in regs:
        from multimodal.asset_access import MediaAssetRegistry
        regs[target_id] = MediaAssetRegistry(document_id=target_id)
    return regs[target_id]


def update_conversation_document(
    document: ConversationEvidenceDocument,
    state: MutableMapping[str, Any] | None = None,
) -> None:
    """
    Aggiorna la versione corrente del documento nella sessione (es. dopo arricchimento con media derivati),
    invalidando la cache dell'EvidenceIndex per forzare la reindicizzazione deterministica.
    """
    target = _get_target_state(state)
    target[KEY_CONVERSATION_DOCUMENT] = document
    available = target.setdefault(KEY_AVAILABLE_DOCUMENTS, {})
    available[document.document_id] = document

    active_analysis = get_active_analysis_state(state)
    if active_analysis is not None:
        active_analysis.cached_evidence_index = None


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
    Applica un lifecycle transazionale:
    - Se l'ingestion fallisce (FAILED) o ha errore bloccante, il dataset precedente non viene alterato.
    - Se l'ingestion è AUXILIARY_ONLY senza conversazioni, il dataset precedente viene sostituito
      con uno stato caricato privo di conversazioni fittizie e con notifica forense appropriata.
    - Se l'ingestion ha successo (SUCCESS con documenti), ripulisce le risorse del dataset precedente
      e rende attiva la nuova conversazione.
    """
    target = _get_target_state(state)

    # 1. In caso di fallimento esplicito, preserva integralmente il dataset precedente
    if result.status == IngestionStatus.FAILED or (result.error_message and result.status != IngestionStatus.AUXILIARY_ONLY):
        if result.error_message:
            target[KEY_ERROR_MESSAGE] = result.error_message
        return

    # Registri precedentemente posseduti dalla sessione da pulire in caso di sostituzione dataset
    old_regs: set[Any] = set()
    raw_old = target.get(KEY_MEDIA_REGISTRIES_BY_DOCUMENT)
    if isinstance(raw_old, dict):
        for reg in raw_old.values():
            if reg is not None and reg is not result.media_registry:
                old_regs.add(reg)

    # 2. In caso di AUXILIARY_ONLY senza conversazioni
    if result.status == IngestionStatus.AUXILIARY_ONLY:
        target[KEY_REAL_INGESTION_RESULT] = result
        target[KEY_INGESTION_SUMMARY] = result.summary
        target[KEY_DATASET_LOADED] = True
        target[KEY_DATASET_MODE] = DatasetMode.FILE
        target[KEY_SELECTED_EVIDENCE_ID] = None
        target[KEY_ERROR_MESSAGE] = None
        target[KEY_CUSTOM_TOPIC_LABEL] = ""
        target[KEY_CUSTOM_TOPIC_DESCRIPTION] = ""
        target[KEY_REFERENCE_DETECTION_RESULTS] = ()
        target[KEY_REFERENCE_DISCOVERY_RESULTS] = ()
        target[KEY_DISCOVERY_RUN_STORE_BY_DOCUMENT] = {}
        target[KEY_AUTHORITATIVE_LIVE_RUN_IDS] = set()

        target[KEY_AVAILABLE_DOCUMENTS] = {}
        target[KEY_SELECTED_DOCUMENT_ID] = None
        target[KEY_CONVERSATION_DOCUMENT] = None
        target[KEY_SEARCH_SERVICE] = None
        target[KEY_ANALYSIS_BY_DOCUMENT] = {}
        target[KEY_MEDIA_REGISTRIES_BY_DOCUMENT] = {}

        for old_reg in old_regs:
            if hasattr(old_reg, "clear"):
                try:
                    old_reg.clear()
                except Exception:
                    pass
        return

    # 3. SUCCESS deve contenere documenti analizzabili
    if not result.documents:
        if result.error_message:
            target[KEY_ERROR_MESSAGE] = result.error_message
        return

    if isinstance(result.documents, Mapping):
        docs_map = dict(result.documents)
        first_doc = next(iter(result.documents.values()))
    else:
        docs_map = {doc.document_id: doc for doc in result.documents}
        first_doc = result.documents[0]

    # Regola 2: Il nuovo dataset diventa attivo solo ora
    target[KEY_REAL_INGESTION_RESULT] = result
    target[KEY_INGESTION_SUMMARY] = result.summary
    target[KEY_DATASET_LOADED] = True
    target[KEY_DATASET_MODE] = DatasetMode.FILE
    target[KEY_SELECTED_EVIDENCE_ID] = None
    target[KEY_ERROR_MESSAGE] = None
    target[KEY_CUSTOM_TOPIC_LABEL] = ""
    target[KEY_CUSTOM_TOPIC_DESCRIPTION] = ""
    target[KEY_REFERENCE_DETECTION_RESULTS] = ()
    target[KEY_REFERENCE_DISCOVERY_RESULTS] = ()
    target[KEY_DISCOVERY_RUN_STORE_BY_DOCUMENT] = {}
    target[KEY_AUTHORITATIVE_LIVE_RUN_IDS] = set()

    target[KEY_AVAILABLE_DOCUMENTS] = docs_map
    target[KEY_SELECTED_DOCUMENT_ID] = first_doc.document_id
    target[KEY_CONVERSATION_DOCUMENT] = first_doc

    # Inizializza gli stati document-scoped isolati per ciascun documento importato
    target[KEY_ANALYSIS_BY_DOCUMENT] = {
        doc_id: DocumentAnalysisState(document_id=doc_id) for doc_id in docs_map
    }

    # Registra il nuovo media registry della sessione per ciascun documento importato
    new_media_regs: dict[str, Any] = {}
    if result.media_registry is not None:
        for doc_id in docs_map:
            new_media_regs[doc_id] = result.media_registry
    target[KEY_MEDIA_REGISTRIES_BY_DOCUMENT] = new_media_regs

    # Regola 3 & 9: Cleanup idempotente delle risorse del dataset sostituito
    for old_reg in old_regs:
        if hasattr(old_reg, "clear"):
            try:
                old_reg.clear()
            except Exception:
                pass


def get_discovery_run_store(
    state: MutableMapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """
    Restituisce il registro autorevole delle run di Topic Discovery prodotte nella sessione.
    Le run live partono vuote; le reference sintetiche NON vengono mai sincronizzate in questo registro.
    """
    target = _get_target_state(state)
    return target.setdefault(KEY_DISCOVERY_RUN_STORE_BY_DOCUMENT, {})


def get_authoritative_live_run_ids(
    state: MutableMapping[str, Any] | None = None,
) -> set[str]:
    """
    Restituisce l'insieme dei run_id live autoritativamente generati nella sessione corrente.
    """
    target = _get_target_state(state)
    return target.setdefault(KEY_AUTHORITATIVE_LIVE_RUN_IDS, set())


def select_conversation(
    document_id: str,
    state: MutableMapping[str, Any] | None = None,
) -> bool:
    """
    Seleziona una conversazione tra quelle disponibili per il dataset corrente.
    Preserva rigorosamente l'isolamento multi-conversazione (FASE 2, 25):
    - Seleziona document_id come conversazione attiva.
    - NON cancella né sovrascrive i risultati della conversazione precedentemente attiva.
    - Tutti i risultati appartengono esclusivamente a analysis_by_document[document_id].
    """
    target = _get_target_state(state)
    available = target.get(KEY_AVAILABLE_DOCUMENTS, {})
    if not isinstance(available, dict) or document_id not in available:
        return False

    doc = available[document_id]
    target[KEY_SELECTED_DOCUMENT_ID] = document_id
    target[KEY_CONVERSATION_DOCUMENT] = doc

    # Garantisce l'esistenza dello stato scoped isolato
    analysis_map = target.setdefault(KEY_ANALYSIS_BY_DOCUMENT, {})
    if document_id not in analysis_map:
        analysis_map[document_id] = DocumentAnalysisState(document_id=document_id)

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
    """Restituisce il risultato dell'ultima Topic Detection manuale per la conversazione attiva."""
    active_analysis = get_active_analysis_state(state)
    if active_analysis is not None:
        return active_analysis.last_detection
    return None


def set_last_manual_detection(
    result: Optional[TopicDetectionResult],
    state: MutableMapping[str, Any] | None = None,
) -> None:
    """Memorizza il risultato dell'ultima Topic Detection manuale per la conversazione attiva."""
    active_analysis = get_active_analysis_state(state)
    if active_analysis is not None and result is not None:
        active_analysis.add_detection_result(result)


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
    Aggiunge un risultato di Topic Detection alla conversazione corretta in modo document-scoped.
    Preserva l'integrità del modello di run senza scritture duplicate.
    """
    doc_id = result.provenance_document_id
    doc_analysis = get_document_analysis_state(doc_id, state=state)
    doc_analysis.add_detection_result(result)


def add_discovery_result(
    result: TopicDiscoveryResult,
    state: MutableMapping[str, Any] | None = None,
) -> None:
    """
    Aggiunge un risultato di Topic Discovery alla conversazione corretta in modo document-scoped.
    Preserva l'integrità del modello di run senza scritture duplicate.
    """
    doc_id = result.provenance_document_id
    doc_analysis = get_document_analysis_state(doc_id, state=state)
    doc_analysis.add_discovery_result(result)


def add_consistency_result(
    result: ConsistencyResult,
    state: MutableMapping[str, Any] | None = None,
) -> None:
    """Aggiunge un ConsistencyResult allo stato scoped del rispettivo documento."""
    doc_id = result.document_id
    doc_analysis = get_document_analysis_state(doc_id, state=state)
    doc_analysis.add_consistency_result(result)


def get_consistency_results(
    document_id: str | None = None,
    state: MutableMapping[str, Any] | None = None,
) -> tuple[ConsistencyResult, ...]:
    """Restituisce i risultati di consistency per il documento specificato o per quello attivo."""
    target_id = document_id or get_selected_document_id(state)
    if not target_id:
        return ()
    target = _get_target_state(state)
    analysis_map = target.get(KEY_ANALYSIS_BY_DOCUMENT, {})
    if target_id in analysis_map:
        return analysis_map[target_id].consistency_results
    return ()


def get_operational_profile(
    state: MutableMapping[str, Any] | None = None,
) -> OperationalProfile:
    """Restituisce il profilo operativo configurato nella sessione (default: BALANCED)."""
    target = _get_target_state(state)
    val = target.get(KEY_OPERATIONAL_PROFILE, OperationalProfile.BALANCED.value)
    if isinstance(val, OperationalProfile):
        return val
    try:
        return OperationalProfile(str(val).upper())
    except Exception:
        return OperationalProfile.BALANCED


def set_operational_profile(
    profile: OperationalProfile | str,
    state: MutableMapping[str, Any] | None = None,
) -> None:
    """Imposta il profilo operativo nella sessione Streamlit."""
    target = _get_target_state(state)
    val = profile.value if isinstance(profile, OperationalProfile) else str(profile).upper()
    target[KEY_OPERATIONAL_PROFILE] = val


def get_checkpoint_store(
    state: MutableMapping[str, Any] | None = None,
) -> Any:
    """Restituisce l'AnalysisCheckpointStore della sessione Streamlit."""
    target = _get_target_state(state)
    return target.get(KEY_CHECKPOINT_STORE)


def set_checkpoint_store(
    store: Any,
    state: MutableMapping[str, Any] | None = None,
) -> None:
    """Imposta l'AnalysisCheckpointStore della sessione Streamlit."""
    target = _get_target_state(state)
    target[KEY_CHECKPOINT_STORE] = store

