"""
ui/verification.py
------------------
Logica APPLICATIVA PURA (nessuna dipendenza da Streamlit) della card «Verifica con Topic Detection» della scheda Discovery.

La vista (`ui/presentation.py`) si limita a mostrare ciò che questo modulo decide:
- precondizioni (pulsante abilitato/disabilitato con motivo) calcolate con le STESSE funzioni usate dal service;
- selezione del risultato di verifica per identità (run Discovery + UID del topic), mai per label;
- testi d'esito non ambigui;
- esportazione del rapporto SAFE tramite `analysis.export` (nessun dizionario assemblato nella vista).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, MutableMapping, Sequence

from ai.models import (
    ConversationEvidenceDocument,
    DiscoveredTopic,
    TopicDetectionResult,
    TopicDiscoveryResult,
)
from analysis.export import (
    SemanticVerificationExportError,
    build_semantic_verification_export_report,
    generate_export_filename,
    serialize_report_to_json,
)
from analysis.models import ConsistencyResult, ConsistencyStatus
from analysis.verification import (
    ExecutionOrigin,
    SnapshotStatus,
    check_document_snapshot,
    compute_discovered_topic_uid,
)

# Codici di blocco (chiusi) -> messaggio comprensibile per l'utente.
BLOCK_MESSAGES: dict[str, str] = {
    "NO_DOCUMENT": "Nessun documento di conversazione disponibile.",
    "NO_EVIDENCE": "Il documento non contiene evidenze analizzabili: impossibile avviare la Topic Detection.",
    "PROVENANCE_MISSING": (
        "Metadati di provenienza del documento mancanti per questo topic: verifica indipendente disabilitata."
    ),
    "DOCUMENT_MISMATCH": "Il topic appartiene a un altro documento: verifica bloccata per discrepanza di provenienza.",
    "RUN_ID_MISSING": (
        "Identificativo della run di Discovery mancante nei metadati: verifica indipendente disabilitata."
    ),
    "DISCOVERY_RUN_UNAVAILABLE": (
        "La run di Discovery di questo topic non è più disponibile in sessione: rieseguire la Topic Discovery."
    ),
    "ORIGIN_MISSING": (
        "Origine dell'esecuzione Discovery mancante o non valida nei metadati: verifica indipendente disabilitata."
    ),
    "SYNTHETIC_REFERENCE_ORIGIN": (
        "Il topic proviene da un riferimento sintetico dimostrativo: "
        "la verifica indipendente richiede una Topic Discovery effettiva."
    ),
    "TOPIC_METADATA_INVALID": (
        "Dati strutturali del topic incompleti nei metadati della scheda: verifica indipendente disabilitata."
    ),
    "SNAPSHOT_MISMATCH": (
        "Il documento attuale ha un contenuto diverso da quello analizzato da questa Discovery "
        "(reimport o arricchimento STT/OCR successivo): rieseguire la Topic Discovery prima di verificare."
    ),
}

EXPORT_ERROR_MESSAGES: dict[str, str] = {
    "DETECTION_RESULT_MISSING": "Risultato della Topic Detection non più disponibile in sessione: ripetere la verifica.",
    "DISCOVERY_RESULT_MISSING": "Risultato della Topic Discovery non più disponibile in sessione: ripetere la Discovery.",
    "RUN_ID_MISSING": "Identificativi di run mancanti: il rapporto non è dimostrabile.",
    "DOCUMENT_ID_MISMATCH": "I risultati appartengono a documenti diversi: esportazione bloccata.",
    "RUN_LINK_MISMATCH": "Il collegamento fra Discovery e Detection non è verificabile: esportazione bloccata.",
    "NOT_A_VERIFICATION_RUN": "La Detection associata non è una verifica da Discovery: esportazione bloccata.",
    "TOPIC_NOT_IN_DISCOVERY_RUN": "Il topic non risulta nella run di Discovery indicata: esportazione bloccata.",
    "QUERY_NOT_FROM_DISCOVERY_TOPIC": "La query della Detection non coincide con il topic di Discovery: esportazione bloccata.",
    "QUERY_IS_LABEL_ONLY": "La Detection è stata eseguita a sola label: non è una verifica associata.",
    "COMPARATOR_RECHECK_FAILED": "Il confronto non è ricalcolabile: esportazione bloccata.",
    "CONSISTENCY_RESULT_STALE": "L'esito registrato non coincide con il ricalcolo: esportazione bloccata.",
    "PRIVACY_CHECK_FAILED": "Controllo privacy fallito: esportazione SAFE bloccata.",
}


def describe_export_error(code: str) -> str:
    key = code.split(":", 1)[0]
    return EXPORT_ERROR_MESSAGES.get(key, f"Rapporto non esportabile ({code}).")


@dataclass(frozen=True)
class VerificationPreconditions:
    can_verify: bool
    block_code: str | None
    block_message: str | None
    warnings: tuple[str, ...]
    snapshot_status: str | None
    discovery_status: str | None


_UNSET = object()


def _clean_non_empty_str(value: Any) -> str | None:
    """Normalizza un identificativo testuale non vuoto; rifiuta None, stringhe vuote o solo spazi."""
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    return None


def find_discovery_run(
    run_id: str | None,
    document_id: str,
    candidate_runs: Sequence[TopicDiscoveryResult],
) -> TopicDiscoveryResult | None:
    """Run di Discovery per IDENTIFICATIVO (e documento): mai per posizione né per label."""
    clean_run_id = _clean_non_empty_str(run_id)
    clean_doc_id = _clean_non_empty_str(document_id)
    if clean_run_id is None or clean_doc_id is None:
        return None
    for r in candidate_runs:
        meta = r.metadata if isinstance(r.metadata, Mapping) else {}
        r_run_id = _clean_non_empty_str(meta.get("run_id"))
        if getattr(r, "provenance_document_id", None) == clean_doc_id and r_run_id == clean_run_id:
            return r
    return None


def evaluate_verification_preconditions(
    document: ConversationEvidenceDocument | None,
    discovery_result: TopicDiscoveryResult | None,
    topic_provenance_document_id: Any = _UNSET,
    *,
    topic_discovery_run_id: Any = _UNSET,
    discovered_topic: Any = _UNSET,
) -> VerificationPreconditions:
    """Precondizioni della verifica: stesse regole del service (snapshot, provenienza), valutate PRIMA del click."""

    def _blocked(code: str, snap: str | None = None, disc: str | None = None) -> VerificationPreconditions:
        return VerificationPreconditions(False, code, BLOCK_MESSAGES[code], (), snap, disc)

    if document is None:
        return _blocked("NO_DOCUMENT")
    if not document.all_evidence_sections:
        return _blocked("NO_EVIDENCE")

    if topic_provenance_document_id is not _UNSET:
        clean_topic_prov = _clean_non_empty_str(topic_provenance_document_id)
        if clean_topic_prov is None:
            return _blocked("PROVENANCE_MISSING")
        if clean_topic_prov != document.document_id:
            return _blocked("DOCUMENT_MISMATCH")

    if topic_discovery_run_id is not _UNSET:
        clean_topic_run_id = _clean_non_empty_str(topic_discovery_run_id)
        if clean_topic_run_id is None:
            return _blocked("RUN_ID_MISSING")

    if discovered_topic is not _UNSET:
        if (
            not isinstance(discovered_topic, DiscoveredTopic)
            or not _clean_non_empty_str(discovered_topic.label)
            or not discovered_topic.evidence_ids
        ):
            return _blocked("TOPIC_METADATA_INVALID")

    if discovery_result is None:
        return _blocked("DISCOVERY_RUN_UNAVAILABLE")

    res_prov = _clean_non_empty_str(getattr(discovery_result, "provenance_document_id", None))
    if res_prov is None:
        return _blocked("PROVENANCE_MISSING")
    if res_prov != document.document_id:
        return _blocked("DOCUMENT_MISMATCH")

    meta = discovery_result.metadata if isinstance(discovery_result.metadata, Mapping) else {}
    res_run_id = _clean_non_empty_str(meta.get("run_id"))
    if res_run_id is None:
        return _blocked("RUN_ID_MISSING")
    if topic_discovery_run_id is not _UNSET and res_run_id != _clean_non_empty_str(topic_discovery_run_id):
        return _blocked("DISCOVERY_RUN_UNAVAILABLE")

    raw_origin = meta.get("origin")
    origin_val = getattr(raw_origin, "value", raw_origin)
    clean_origin = _clean_non_empty_str(origin_val)
    if clean_origin is None:
        return _blocked("ORIGIN_MISSING")
    if clean_origin == "SYNTHETIC_REFERENCE":
        return _blocked("SYNTHETIC_REFERENCE_ORIGIN")

    disc_status = str(meta.get("status") or "NOT_RECORDED")
    snap = check_document_snapshot(meta, document)
    if snap.status == SnapshotStatus.MISMATCH:
        return _blocked("SNAPSHOT_MISMATCH", snap.status.value, disc_status)

    warnings: list[str] = []
    if disc_status != "COMPLETE":
        total = meta.get("total_evidence_count")
        analyzed = meta.get("analyzed_evidence_count")
        cov = f" (evidenze analizzate: {analyzed}/{total})" if analyzed is not None and total is not None else ""
        warnings.append(
            f"Discovery sorgente {disc_status}{cov}: il topic proviene da una copertura parziale; la verifica non la estende."
        )
    if snap.status == SnapshotStatus.UNVERIFIABLE:
        warnings.append(
            "L'equivalenza del documento con quello analizzato dalla Discovery non è dimostrabile "
            "(fingerprint non registrata dalla run sorgente)."
        )
    return VerificationPreconditions(True, None, None, tuple(warnings), snap.status.value, disc_status)


def select_verification_results(
    consistency_results: Sequence[ConsistencyResult],
    document_id: str,
    discovery_run_id: str | None,
    topic: DiscoveredTopic | None,
    *,
    detection_runs: Sequence[TopicDetectionResult] | None = None,
) -> tuple[ConsistencyResult, ...]:
    """
    Risultati di verifica di QUESTO topic in QUESTA run Discovery, in ordine cronologico.
    L'associazione usa document_id + discovery_run_id + UID del topic (contenuto validato) +
    detection_run_id valido (e, quando `detection_runs` è fornito, provenienza `DISCOVERY_VERIFICATION`
    compatibile): nessuna corrispondenza per label e nessun legame con `MANUAL_DETECTION`.
    """
    clean_run_id = _clean_non_empty_str(discovery_run_id)
    clean_doc_id = _clean_non_empty_str(document_id)
    if clean_run_id is None or clean_doc_id is None or not isinstance(topic, DiscoveredTopic):
        return ()
    uid = compute_discovered_topic_uid(clean_run_id, topic)
    selected: list[ConsistencyResult] = []
    for c in consistency_results:
        if c.document_id != clean_doc_id:
            continue
        if _clean_non_empty_str(c.discovery_run_id) != clean_run_id:
            continue
        det_run_id = _clean_non_empty_str(c.detection_run_id)
        if det_run_id is None:
            continue
        c_meta = c.metadata if isinstance(c.metadata, Mapping) else {}
        if c_meta.get("discovery_topic_uid") != uid:
            continue
        if detection_runs is not None:
            det_run = _find_detection(detection_runs, det_run_id)
            if det_run is None or getattr(det_run, "provenance_document_id", None) != clean_doc_id:
                continue
            det_meta = det_run.metadata if isinstance(det_run.metadata, Mapping) else {}
            if det_meta.get("origin_flow") != "DISCOVERY_VERIFICATION":
                continue
            verified_disc = _clean_non_empty_str(det_meta.get("verified_discovery_run_id"))
            if verified_disc is not None and verified_disc != clean_run_id:
                continue
        selected.append(c)
    return tuple(selected)


def has_unverifiable_verification_records(
    consistency_results: Sequence[ConsistencyResult],
    document_id: str,
    discovery_run_id: str | None,
    topic: DiscoveredTopic | None,
    *,
    detection_runs: Sequence[TopicDetectionResult] | None = None,
) -> bool:
    """
    Rileva in modo fail-closed se esistono record `ConsistencyResult` riferiti a questo topic
    che sono stati scartati perché privi di provenienza valida o compatibile (es. `discovery_run_id`
    o `detection_run_id` mancanti, `document_id` incoerente a parità di run, `origin_flow` manuale
    invece di `DISCOVERY_VERIFICATION`, o `verified_discovery_run_id` discordante).
    Ignora i record legittimamente appartenenti ad altre esecuzioni di Discovery o ad altri topic.
    """
    clean_doc_id = _clean_non_empty_str(document_id)
    if clean_doc_id is None or not isinstance(topic, DiscoveredTopic):
        return False
    clean_label = _clean_non_empty_str(topic.label)
    if clean_label is None:
        return False
    target_key = clean_label.casefold()
    clean_run_id = _clean_non_empty_str(discovery_run_id)
    valid_set = set(
        id(v)
        for v in select_verification_results(
            consistency_results,
            clean_doc_id,
            clean_run_id,
            topic,
            detection_runs=detection_runs,
        )
    )

    for c in consistency_results:
        if id(c) in valid_set:
            continue
        if _clean_non_empty_str(getattr(c, "topic_label", None)) is None:
            continue
        if c.topic_label.strip().casefold() != target_key:
            continue
        c_disc_id = _clean_non_empty_str(getattr(c, "discovery_run_id", None))
        c_doc_id = _clean_non_empty_str(getattr(c, "document_id", None))

        # Se il record dichiara esplicitamente un'ALTRA run di Discovery valida sullo stesso documento,
        # appartiene a quell'altra run (es. due run Discovery distinte in sessione) e non è un errore di questa card.
        if c_doc_id == clean_doc_id and c_disc_id is not None and clean_run_id is not None and c_disc_id != clean_run_id:
            continue
        # Se il record appartiene a un altro documento e non cita questa specifica run di Discovery, ignoralo.
        if c_doc_id != clean_doc_id and (clean_run_id is None or c_disc_id != clean_run_id):
            continue
        # Se sulla stessa run esistono due topic omonimi distinti, un record valido per l'ALTRO omonimo
        # ha UID valido diverso ma `origin_flow == DISCOVERY_VERIFICATION` e ID completi: non segnalarlo come corrotto.
        if (
            c_doc_id == clean_doc_id
            and clean_run_id is not None
            and c_disc_id == clean_run_id
            and _clean_non_empty_str(getattr(c, "detection_run_id", None)) is not None
        ):
            c_meta = c.metadata if isinstance(c.metadata, Mapping) else {}
            c_uid = _clean_non_empty_str(c_meta.get("discovery_topic_uid"))
            expected_uid = compute_discovered_topic_uid(clean_run_id, topic)
            if c_uid is not None and c_uid != expected_uid:
                if detection_runs is None:
                    continue
                det_r = _find_detection(detection_runs, c.detection_run_id)
                det_m = det_r.metadata if (det_r is not None and isinstance(det_r.metadata, Mapping)) else {}
                if (
                    det_r is not None
                    and getattr(det_r, "provenance_document_id", None) == clean_doc_id
                    and det_m.get("origin_flow") == "DISCOVERY_VERIFICATION"
                    and _clean_non_empty_str(det_m.get("verified_discovery_run_id")) in (None, clean_run_id)
                ):
                    continue
        return True
    return False


def build_manual_detection_correlation_note(
    *,
    document_id: str,
    topic_label: str,
    detection_runs: Sequence[TopicDetectionResult],
    card_discovery_run_id: str | None,
    topic_verifications: Sequence[ConsistencyResult] = (),
) -> str | None:
    """
    Costruisce la nota di ancoraggio SOLO quando coesistono:
    1. una verifica indipendente (`Prova del Nove`) effettiva e validata sulla card di Discovery corrente,
       con `discovery_run_id` e `detection_run_id` non vuoti e compatibili con `card_discovery_run_id`
       e `document_id` (`origin_flow == 'DISCOVERY_VERIFICATION'`);
    2. una `Topic Detection` normale separata (`origin_flow == 'MANUAL_DETECTION'`) sullo stesso documento
       e con la medesima etichetta tematica.

    Non mostra MAI questo messaggio per una semplice Detection normale in assenza di una Prova del Nove
    validata sulla card, e non inventa né sostituisce mai identificativi mancanti o incompatibili.
    """
    if not topic_verifications:
        return None

    clean_doc_id = _clean_non_empty_str(document_id)
    clean_label = _clean_non_empty_str(topic_label)
    clean_card_run_id = _clean_non_empty_str(card_discovery_run_id)
    if clean_doc_id is None or clean_label is None or clean_card_run_id is None:
        return None

    latest_cons = topic_verifications[-1]
    verif_disc_run_id = _clean_non_empty_str(getattr(latest_cons, "discovery_run_id", None))
    verif_det_run_id = _clean_non_empty_str(getattr(latest_cons, "detection_run_id", None))
    if (
        getattr(latest_cons, "document_id", None) != clean_doc_id
        or verif_disc_run_id is None
        or verif_det_run_id is None
        or verif_disc_run_id != clean_card_run_id
    ):
        return None

    # La Detection della Prova del Nove deve esistere in sessione, appartenere al documento,
    # avere `origin_flow == 'DISCOVERY_VERIFICATION'` ed essere compatibile con `verif_disc_run_id`.
    verif_det = _find_detection(detection_runs, verif_det_run_id)
    if verif_det is None or getattr(verif_det, "provenance_document_id", None) != clean_doc_id:
        return None
    v_meta = verif_det.metadata if isinstance(verif_det.metadata, Mapping) else {}
    if v_meta.get("origin_flow") != "DISCOVERY_VERIFICATION":
        return None
    v_linked_disc = _clean_non_empty_str(v_meta.get("verified_discovery_run_id"))
    if v_linked_disc is not None and v_linked_disc != verif_disc_run_id:
        return None

    target_key = clean_label.casefold()
    manual_dets = [
        r for r in detection_runs
        if getattr(r, "provenance_document_id", None) == clean_doc_id
        and isinstance(getattr(r, "metadata", None), Mapping)
        and r.metadata.get("origin_flow") == "MANUAL_DETECTION"
        and _clean_non_empty_str(r.metadata.get("run_id")) is not None
        and _clean_non_empty_str(r.metadata.get("run_id")) != verif_det_run_id
        and r.topic.label.strip().casefold() == target_key
    ]
    if not manual_dets:
        return None

    latest_m = manual_dets[-1]
    m_meta = latest_m.metadata if isinstance(latest_m.metadata, Mapping) else {}
    m_run_id = _clean_non_empty_str(m_meta.get("run_id"))
    if m_run_id is None:
        return None
    m_mode = "Sola label" if m_meta.get("query_mode") == "LABEL_ONLY" else "Label + Descrizione"
    m_decision = latest_m.decision.value if hasattr(latest_m.decision, "value") else str(latest_m.decision)

    return (
        f"📌 **Nota di correlazione indipendente**: Nella sessione esiste anche una Detection manuale per '{clean_label}' "
        f"(Run `{m_run_id}`, input: {m_mode}) con esito **{m_decision}**. "
        f"La verifica sopra visualizzata è ancorata unicamente alla specifica Discovery (Run `{verif_disc_run_id}`) "
        "e non viene alterata da nuove inferenze indipendenti."
    )


@dataclass(frozen=True)
class VerificationOutcomeView:
    badge: str
    headline: str
    notes: tuple[str, ...]
    execution_origin_label: str


_BADGES = {
    ConsistencyStatus.CONSISTENT: "✅ COERENTE",
    ConsistencyStatus.PARTIALLY_CONSISTENT: "🔵 PARZIALMENTE COERENTE",
    ConsistencyStatus.DISCREPANCY_ABSENT: "⚠️ DISCREPANZA (ABSENT)",
    ConsistencyStatus.DISCREPANCY_UNCERTAIN: "🟡 INCERTO (UNCERTAIN)",
}

_EXECUTION_LABELS = {
    ExecutionOrigin.COMPUTED_THIS_RUN.value: "Detection calcolata in questa esecuzione.",
    ExecutionOrigin.RESTORED_FROM_CHECKPOINT.value: (
        "Risultato Detection RECUPERATO da checkpoint (nessuna nuova richiesta al modello)."
    ),
    ExecutionOrigin.MIXED.value: "Detection ottenuta in parte da checkpoint e in parte calcolata ora.",
    ExecutionOrigin.UNDETERMINED.value: "Origine dell'esecuzione della Detection non determinabile.",
}


def describe_verification_outcome(result: ConsistencyResult) -> VerificationOutcomeView:
    """Testi d'esito. ABSENT è una DISCREPANZA fra due inferenze indipendenti, non un errore né una prova di assenza."""
    meta = result.metadata
    notes: list[str] = []
    if result.status == ConsistencyStatus.DISCREPANCY_ABSENT:
        headline = (
            "La Discovery ha identificato questo topic; la Detection indipendente (eseguita con etichetta e descrizione, "
            "senza le evidenze della Discovery) non lo ha rilevato. È una discrepanza fra due inferenze, non un errore "
            "di esecuzione né una prova che l'argomento sia assente."
        )
    elif result.status == ConsistencyStatus.DISCREPANCY_UNCERTAIN:
        headline = (
            "La Discovery ha identificato questo topic; la Detection indipendente è rimasta INCERTA. Giudizio limitato."
        )
    elif result.status == ConsistencyStatus.PARTIALLY_CONSISTENT:
        headline = (
            "Entrambe le inferenze indicano il topic, ma citano evidenze diverse (nessuna evidenza in comune)."
        )
    else:
        headline = "Entrambe le inferenze indipendenti indicano il topic con almeno un'evidenza in comune."
    if meta.get("is_partial_detection"):
        notes.append("Detection PARZIALE: l'esito vale solo per le porzioni analizzate.")
    if str(meta.get("discovery_status", "COMPLETE")) != "COMPLETE":
        notes.append(f"Discovery sorgente {meta.get('discovery_status')}: copertura non totale.")
    if meta.get("snapshot_status") == SnapshotStatus.UNVERIFIABLE.value:
        notes.append("Equivalenza del documento con quello della Discovery non dimostrabile.")
    notes.append("La coerenza fra inferenze dello stesso modello non è prova di verità.")
    origin = str(meta.get("detection_execution_origin", ExecutionOrigin.UNDETERMINED.value))
    return VerificationOutcomeView(
        badge=_BADGES[result.status],
        headline=headline,
        notes=tuple(notes),
        execution_origin_label=_EXECUTION_LABELS.get(origin, _EXECUTION_LABELS[ExecutionOrigin.UNDETERMINED.value]),
    )


def _find_detection(runs: Sequence[TopicDetectionResult], run_id: str | None) -> TopicDetectionResult | None:
    clean_run_id = _clean_non_empty_str(run_id)
    if clean_run_id is None:
        return None
    for r in runs:
        meta = r.metadata if isinstance(r.metadata, Mapping) else {}
        if _clean_non_empty_str(meta.get("run_id")) == clean_run_id:
            return r
    return None


def export_semantic_verification_json(
    consistency_result: ConsistencyResult,
    document: ConversationEvidenceDocument,
    session_state: MutableMapping[str, Any] | None = None,
    redact_sensitive_topics: bool = True,
) -> tuple[str, str]:
    """
    Serializza il rapporto di verifica (SAFE_REDACTED di default). Risolve Detection e Discovery per run_id dallo
    stato di sessione e delega la costruzione a `analysis.export`. Non invoca mai il modello.
    Solleva SemanticVerificationExportError (codice chiuso) se il rapporto non è dimostrabile.
    """
    from ui import state

    doc_analysis = state.get_document_analysis_state(document.document_id, state=session_state)
    det = _find_detection(doc_analysis.detection_runs, consistency_result.detection_run_id)
    if det is None:
        raise SemanticVerificationExportError("DETECTION_RESULT_MISSING")
    disc = find_discovery_run(
        consistency_result.discovery_run_id, document.document_id,
        list(doc_analysis.discovery_runs) + list(state.get_discovery_results(state=session_state)),
    )
    if disc is None:
        raise SemanticVerificationExportError("DISCOVERY_RESULT_MISSING")
    try:
        dataset_mode = state.get_dataset_mode(state=session_state)
    except Exception:
        dataset_mode = None
    auth_ids = set(state.get_authoritative_live_run_ids(session_state))
    report = build_semantic_verification_export_report(
        consistency_result=consistency_result,
        detection_result=det,
        discovery_result=disc,
        document=document,
        dataset_mode=dataset_mode,
        authoritative_live_run_ids=auth_ids,
        redact_sensitive_topics=redact_sensitive_topics,
    )
    json_str = serialize_report_to_json(report)
    filename = generate_export_filename(
        analysis_type="SEMANTIC_VERIFICATION",
        dataset_id=document.chat_id or document.document_id,
        model_id=report["detection"]["model"],
        run_id=det.metadata.get("run_id"),
        timestamp=report["experiment"]["generated_at"],
        export_id=report["experiment"]["export_id"],
    )
    return json_str, filename
