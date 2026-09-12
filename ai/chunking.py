"""
ai/chunking.py
--------------
Strategie di chunking deterministico per conversazioni estese e aggregazione multi-chunk.

Principi architetturali (Fasi H, I):
1. PRESERVAZIONE DELL'ORDINE E DELL'ATOMICITÀ DEI MESSAGGI:
   I chunk rispettano l'ordine nativo dei bundle senza alcun riordinamento basato su timestamp eterogenei.
   Un singolo bundle di evidenza non viene mai spezzato a metà.
2. GESTIONE EVIDENZE E BUNDLE OVERSIZE (FASE H):
   - Nessuna falsa frammentazione: il parametro allow_fragmentation è rimosso.
   - Se una singola evidenza supera max_characters -> AnalysisInputTooLargeError.
   - Se la somma dei caratteri delle sezioni di UN singolo bundle supera max_characters -> AnalysisInputTooLargeError.
   - max_characters è un budget deterministico prudenziale di caratteri, NON un conteggio esatto di token.
   - Verifica pre-invio dell'espansione testuale post-traduzione (TRANSLATE_FIRST).
3. AGGREGAZIONE DETERMINISTICA CONSAPEVOLE DEI GUASTI (FASE I):
   - Struttura ChunkAnalysisOutcome con tracking dello stato (SUCCESS, FAILED) ed eventuale errore.
   - PRESENT: se almeno un chunk ha esito PRESENT con evidenze valide.
   - ABSENT: SOLTANTO se TUTTI i chunk attesi sono stati analizzati con successo e sono ABSENT.
   - UNCERTAIN: se nessun PRESENT e almeno un chunk è UNCERTAIN oppure fallito / mancante.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ai.backend import (
    AnalysisInputTooLargeError,
    BaseLocalLlmClient,
)
from ai.models import (
    ConversationEvidenceDocument,
    DiscoveredTopic,
    EvidenceTranslationResult,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
    TopicQuery,
)
from ai.serializer import SYSTEM_SECURITY_DIRECTIVE
from ai.structured import (
    TOPIC_DISCOVERY_SCHEMA,
    validate_topic_discovery_payload,
)
from multimodal.evidence import MessageEvidenceBundle

PROMPT_VERSION_DISCOVERY_REDUCE = "topic_discovery_reduce_v1"


@dataclass(frozen=True)
class ChunkAnalysisOutcome:
    """
    Rappresenta l'esito dell'analisi di un singolo chunk documentale.
    """
    chunk_id: str
    result: TopicDetectionResult | None = None
    status: str = "SUCCESS"  # "SUCCESS" | "FAILED"
    error_type: str | None = None
    error_message: str | None = None


def split_document_into_chunks(
    document: ConversationEvidenceDocument,
    max_characters: int = 8000,
    max_bundles: int = 25,
) -> tuple[ConversationEvidenceDocument, ...]:
    """
    Suddivide deterministicamente un ConversationEvidenceDocument in una sequenza di sotto-documenti
    preservando l'atomicità dei bundle di evidenza.

    Nota metodologica: max_characters rappresenta una stima prudenziale del budget di caratteri,
    non un calcolo token-perfect.
    """
    if not document.bundles:
        return (document,)

    chunks: list[list[MessageEvidenceBundle]] = []
    current_chunk: list[MessageEvidenceBundle] = []
    current_chars = 0

    for bundle in document.bundles:
        bundle_chars = sum(len(sec.text) for sec in bundle.text_evidence_sections)

        # H1: Controllo singola evidenza oversize
        for sec in bundle.text_evidence_sections:
            if len(sec.text) > max_characters:
                raise AnalysisInputTooLargeError(
                    f"La sezione di evidenza '{sec.evidence_id}' ({len(sec.text)} caratteri) "
                    f"supera il limite massimo consentito per chunk ({max_characters} caratteri)."
                )

        # H2: Controllo bundle atomico oversize
        if bundle_chars > max_characters:
            raise AnalysisInputTooLargeError(
                f"Il bundle atomico per il messaggio '{bundle.message.message_id}' ({bundle_chars} caratteri) "
                f"supera da solo il limite massimo consentito per chunk ({max_characters} caratteri)."
            )

        # Se l'aggiunta supera i limiti e il chunk corrente non è vuoto, chiude il chunk
        if current_chunk and (
            current_chars + bundle_chars > max_characters
            or len(current_chunk) >= max_bundles
        ):
            chunks.append(current_chunk)
            current_chunk = []
            current_chars = 0

        current_chunk.append(bundle)
        current_chars += bundle_chars

    if current_chunk:
        chunks.append(current_chunk)

    if len(chunks) <= 1:
        return (document,)

    result_chunks: list[ConversationEvidenceDocument] = []
    for idx, b_list in enumerate(chunks):
        chunk_id = f"{document.document_id}::chunk::{idx}"
        result_chunks.append(
            ConversationEvidenceDocument(
                document_id=chunk_id,
                bundles=tuple(b_list),
                chat_id=document.chat_id,
                source_name=document.source_name,
                metadata={
                    "parent_document_id": document.document_id,
                    "chunk_index": idx,
                    "total_chunks": len(chunks),
                },
            )
        )

    return tuple(result_chunks)


def verify_translated_document_size(
    document: ConversationEvidenceDocument,
    translation: EvidenceTranslationResult,
    max_characters: int = 8000,
) -> None:
    """
    Verifica che l'espansione testuale post-traduzione non ecceda i limiti di chunking atomico (H4).
    Non effettua MAI fallback al testo originale: se la traduzione manca solleva ValueError;
    se è una stringa vuota la preserva senza sostituirla con l'originale.
    """
    if translation.provenance_document_id != document.document_id:
        raise ValueError(
            f"Disallineamento document_id per la traduzione: atteso '{document.document_id}', "
            f"trovato '{translation.provenance_document_id}'."
        )

    for bundle in document.bundles:
        bundle_translated_chars = 0
        for sec in bundle.text_evidence_sections:
            tr_text = translation.get_translation(sec.evidence_id)
            if tr_text is None:
                raise ValueError(
                    f"Traduzione mancante per evidence_id '{sec.evidence_id}' sotto TRANSLATE_FIRST. "
                    f"Nessun fallback al testo originale consentito."
                )
            if len(tr_text) > max_characters:
                raise AnalysisInputTooLargeError(
                    f"La traduzione dell'evidenza '{sec.evidence_id}' ({len(tr_text)} caratteri) "
                    f"supera il limite massimo per chunk ({max_characters} caratteri)."
                )
            bundle_translated_chars += len(tr_text)

        if bundle_translated_chars > max_characters:
            raise AnalysisInputTooLargeError(
                f"Il bundle tradotto per il messaggio '{bundle.message.message_id}' ({bundle_translated_chars} caratteri) "
                f"supera il limite massimo consentito per chunk ({max_characters} caratteri)."
            )


def aggregate_topic_detection(
    outcomes: Sequence[ChunkAnalysisOutcome | TopicDetectionResult],
    topic: TopicQuery,
    parent_document_id: str,
    expected_chunk_count: int | None = None,
) -> TopicDetectionResult:
    """
    Aggrega deterministicamente i risultati di Topic Detection provenienti da più chunk.

    Regola FASE I:
    - PRESENT: se almeno un chunk ha concluso con successo PRESENT con evidenze valide.
    - ABSENT: SOLTANTO se tutti i chunk attesi sono stati analizzati con successo e sono ABSENT.
    - UNCERTAIN: se nessun PRESENT ed è presente almeno un UNCERTAIN oppure almeno un chunk è fallito / mancante.
    """
    if not outcomes:
        return TopicDetectionResult(
            topic=topic,
            decision=TopicDecision.ABSENT if (expected_chunk_count or 0) == 0 else TopicDecision.UNCERTAIN,
            evidence_ids=(),
            rationale="Nessun chunk fornito per l'aggregazione.",
            provenance_document_id=parent_document_id,
            metadata={"aggregation": "empty"},
        )

    normalized_outcomes: list[ChunkAnalysisOutcome] = []
    for o in outcomes:
        if isinstance(o, TopicDetectionResult):
            normalized_outcomes.append(
                ChunkAnalysisOutcome(
                    chunk_id=o.provenance_document_id,
                    result=o,
                    status="SUCCESS",
                )
            )
        elif isinstance(o, ChunkAnalysisOutcome):
            normalized_outcomes.append(o)
        else:
            raise ValueError(f"Elemento non valido in outcomes: {type(o)}")

    total_expected = expected_chunk_count if expected_chunk_count is not None else len(normalized_outcomes)
    has_missing_chunks = len(normalized_outcomes) < total_expected

    successful_results = [o.result for o in normalized_outcomes if o.status == "SUCCESS" and o.result is not None]
    failed_outcomes = [o for o in normalized_outcomes if o.status != "SUCCESS"]

    present_results = [r for r in successful_results if r.decision == TopicDecision.PRESENT]
    uncertain_results = [r for r in successful_results if r.decision == TopicDecision.UNCERTAIN]
    absent_results = [r for r in successful_results if r.decision == TopicDecision.ABSENT]

    # Regola 1: PRESENT vince sempre se almeno un chunk ha rilevato evidenze valide
    if present_results:
        aggregated_eids: list[str] = []
        for pr in present_results:
            for eid in pr.evidence_ids:
                if eid not in aggregated_eids:
                    aggregated_eids.append(eid)

        rationales = " | ".join(pr.rationale for pr in present_results if pr.rationale)
        return TopicDetectionResult(
            topic=topic,
            decision=TopicDecision.PRESENT,
            evidence_ids=tuple(aggregated_eids),
            rationale=f"Rilevato in {len(present_results)} chunk: {rationales}",
            provenance_document_id=parent_document_id,
            metadata={
                "aggregation": "multi_chunk",
                "chunks_count": len(normalized_outcomes),
                "expected_chunks": total_expected,
                "present_chunks": len(present_results),
            },
        )

    # Regola 2: Se nessun PRESENT, ma vi sono chunk falliti o mancanti, NON si può concludere ABSENT
    if failed_outcomes or has_missing_chunks:
        reasons: list[str] = []
        if failed_outcomes:
            reasons.append(f"{len(failed_outcomes)} chunk falliti durante l'inferenza")
        if has_missing_chunks:
            reasons.append(f"ricevuti {len(normalized_outcomes)} chunk su {total_expected} attesi")

        return TopicDetectionResult(
            topic=topic,
            decision=TopicDecision.UNCERTAIN,
            evidence_ids=(),
            rationale=f"Analisi multi-chunk parziale o incompleta ({'; '.join(reasons)}). Impossibile escludere il topic con certezza.",
            provenance_document_id=parent_document_id,
            metadata={
                "aggregation": "multi_chunk_incomplete",
                "failed_chunks": len(failed_outcomes),
                "received_chunks": len(normalized_outcomes),
                "expected_chunks": total_expected,
            },
        )

    # Regola 3: Se vi sono chunk con decisione UNCERTAIN
    if uncertain_results:
        aggregated_eids: list[str] = []
        for ur in uncertain_results:
            for eid in ur.evidence_ids:
                if eid not in aggregated_eids:
                    aggregated_eids.append(eid)

        return TopicDetectionResult(
            topic=topic,
            decision=TopicDecision.UNCERTAIN,
            evidence_ids=tuple(aggregated_eids),
            rationale=f"Verdetto incerto riscontrato in {len(uncertain_results)} chunk.",
            provenance_document_id=parent_document_id,
            metadata={
                "aggregation": "multi_chunk_uncertain",
                "uncertain_chunks": len(uncertain_results),
            },
        )

    # Regola 4: ABSENT valido solo se TUTTI i chunk attesi sono SUCCESS e ABSENT
    return TopicDetectionResult(
        topic=topic,
        decision=TopicDecision.ABSENT,
        evidence_ids=(),
        rationale=f"Topic assente verificato con successo su tutti i {len(absent_results)} chunk attesi.",
        provenance_document_id=parent_document_id,
        metadata={
            "aggregation": "multi_chunk_absent",
            "absent_chunks": len(absent_results),
            "expected_chunks": total_expected,
        },
    )


def aggregate_topic_discovery(
    chunk_results: Sequence[TopicDiscoveryResult],
    parent_document_id: str,
    max_topics: int = 10,
) -> TopicDiscoveryResult:
    """
    Aggrega i risultati di Topic Discovery provenienti da più chunk fondendo temi con label identiche
    e preservando tutti gli evidence_ids associati.
    """
    if not chunk_results:
        return TopicDiscoveryResult(
            topics=(),
            provenance_document_id=parent_document_id,
            metadata={"aggregation": "empty"},
        )

    consolidated: dict[str, dict[str, Any]] = {}

    for cr in chunk_results:
        for t in cr.topics:
            norm_key = t.label.strip().lower()
            if norm_key in consolidated:
                existing = consolidated[norm_key]
                combined_eids = list(existing["evidence_ids"])
                for eid in t.evidence_ids:
                    if eid not in combined_eids:
                        combined_eids.append(eid)
                existing["evidence_ids"] = combined_eids
            else:
                consolidated[norm_key] = {
                    "label": t.label.strip(),
                    "short_description": t.short_description.strip(),
                    "evidence_ids": list(t.evidence_ids),
                }

    topics: list[DiscoveredTopic] = []
    for item in list(consolidated.values())[:max_topics]:
        topics.append(
            DiscoveredTopic(
                label=item["label"],
                short_description=item["short_description"],
                evidence_ids=tuple(item["evidence_ids"]),
            )
        )

    return TopicDiscoveryResult(
        topics=tuple(topics),
        provenance_document_id=parent_document_id,
        metadata={
            "aggregation": "consolidated_multi_chunk",
            "total_raw_chunks": len(chunk_results),
            "consolidated_count": len(topics),
        },
    )
