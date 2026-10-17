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

import functools
import math
import string
import unicodedata
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


@dataclass(frozen=True)
class DocumentAnalysisProfile:
    """
    Profilo statico e forense del documento calcolato prima dell'inferenza.
    Distingue testo analizzabile e conteggi strutturali senza invocare l'AI.
    """
    document_id: str
    total_messages: int
    total_evidence_sections: int
    total_text_characters: int
    estimated_text_tokens: int
    estimated_chunks: int
    source_name: str
    has_multimodal_evidence: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "total_messages": self.total_messages,
            "total_evidence_sections": self.total_evidence_sections,
            "total_text_characters": self.total_text_characters,
            "estimated_text_tokens": self.estimated_text_tokens,
            "estimated_chunks": self.estimated_chunks,
            "source_name": self.source_name,
            "has_multimodal_evidence": self.has_multimodal_evidence,
        }


_PUNCTUATION_TRANSLATION_TABLE = str.maketrans(string.punctuation, " " * len(string.punctuation))


def estimate_evidence_framing_tokens(section: Any, default_floor: int = 85) -> int:
    """
    Stima conservativa e verificabile dell'overhead di serializzazione JSON per una sezione di evidenza.
    Tiene conto di identificativi complessi, metadati e delimitatori JSON (~85 token floor).
    """
    cached = getattr(section, "_cached_framing_tokens", None)
    if cached is not None and default_floor == 85:
        return cached

    ev_id = getattr(section, "evidence_id", "")
    s_type = getattr(getattr(section, "source_type", None), "value", "text")
    msg_id = getattr(section, "message_id", "")
    s_name = getattr(section, "source_name", "") or ""
    s_rec = getattr(section, "source_record_id", "") or ""
    s_lang = getattr(section, "language", "") or ""
    meta_chars = len(ev_id) + len(s_type) + len(msg_id) + len(s_name) + len(s_rec) + len(s_lang) + 110
    val = max(default_floor, math.ceil(meta_chars / 2.5))
    if default_floor == 85:
        try:
            object.__setattr__(section, "_cached_framing_tokens", val)
        except Exception:
            pass
    return val


def profile_document_for_analysis(
    document: ConversationEvidenceDocument,
    max_characters: int = 12000,
    max_bundles: int = 100,
    max_tokens: int | None = 2500,
    service: Any = None,
    analysis_type: str = "detection",
    max_topics: int | None = None,
    profile: Any = None,
) -> DocumentAnalysisProfile:
    """
    Esegue la profilazione preventiva del documento prima di iniziare l'analisi AI.
    Se viene fornita un'istanza del servizio, ne utilizza la configurazione effettiva
    per garantire coerenza al 100% tra stima preventiva e run operativa.
    """
    if service is not None and hasattr(service, "profile_document"):
        return service.profile_document(
            document=document,
            analysis_type=analysis_type,
            max_topics=max_topics,
            profile=profile,
        )

    total_chars = 0
    sec_count = 0
    has_multimodal = False

    for b in document.bundles:
        if b.audio_transcription or b.image_ocr or b.image_vision:
            has_multimodal = True
        for sec in b.text_evidence_sections:
            sec_count += 1
            total_chars += len(sec.text)

    # Stima prudenziale dei token (~2.8 caratteri per token in italiano/multilingue)
    est_tokens = max(math.ceil(total_chars / 2.8), 0)

    # Calcolo dei chunk effettivi tramite split_document_into_chunks
    chunks = split_document_into_chunks(
        document,
        max_characters=max_characters,
        max_bundles=max_bundles,
        max_tokens=max_tokens,
    )

    return DocumentAnalysisProfile(
        document_id=document.document_id,
        total_messages=document.message_count,
        total_evidence_sections=sec_count,
        total_text_characters=total_chars,
        estimated_text_tokens=est_tokens,
        estimated_chunks=len(chunks),
        source_name=document.source_name or "unknown",
        has_multimodal_evidence=has_multimodal,
    )


def split_document_into_chunks(
    document: ConversationEvidenceDocument,
    max_characters: int = 8000,
    max_bundles: int = 25,
    max_tokens: int | None = None,
) -> tuple[ConversationEvidenceDocument, ...]:
    """
    Suddivide deterministicamente un ConversationEvidenceDocument in una sequenza di sotto-documenti
    preservando l'atomicità dei bundle di evidenza e la cronologia.

    Supporta budget congiunto di caratteri, numero di bundle e token stimati:
    - max_characters: limite massimo prudenziale di caratteri testuali per chunk;
    - max_bundles: numero massimo di bundle atomici per chunk;
    - max_tokens: limite opzionale sul budget stimato di token di evidenza.
    """
    if not document.bundles:
        return (document,)

    cache_key = (max_characters, max_bundles, max_tokens)
    cached_map = getattr(document, "_cached_chunks_map", None)
    if cached_map is not None and cache_key in cached_map:
        return cached_map[cache_key]

    chunks: list[list[MessageEvidenceBundle]] = []
    current_chunk: list[MessageEvidenceBundle] = []
    current_chars = 0
    current_tokens = 0

    for bundle in document.bundles:
        sec_list = bundle.text_evidence_sections
        bundle_chars = sum(len(sec.text) for sec in sec_list)
        # Stima token realistica che include il testo e il framing JSON serializzato della sezione (~85 token floor o dinamico)
        bundle_framing_tokens = sum(
            estimate_evidence_framing_tokens(sec) for sec in sec_list
        )
        bundle_tokens = math.ceil(bundle_chars / 2.8) + bundle_framing_tokens

        # H1: Controllo singola evidenza oversize
        for sec in sec_list:
            if len(sec.text) > max_characters:
                raise AnalysisInputTooLargeError(
                    f"La sezione di evidenza '{sec.evidence_id}' ({len(sec.text)} caratteri) "
                    f"supera il limite massimo consentito per chunk ({max_characters} caratteri)."
                )
            if max_tokens is not None and math.ceil(len(sec.text) / 2.8) > max_tokens:
                raise AnalysisInputTooLargeError(
                    f"La sezione di evidenza '{sec.evidence_id}' supera il limite token consentito per chunk ({max_tokens} token)."
                )

        # H2: Controllo bundle atomico oversize
        if bundle_chars > max_characters:
            raise AnalysisInputTooLargeError(
                f"Il bundle atomico per il messaggio '{bundle.message.message_id}' ({bundle_chars} caratteri) "
                f"supera da solo il limite massimo consentito per chunk ({max_characters} caratteri)."
            )
        if max_tokens is not None and bundle_tokens > max_tokens:
            raise AnalysisInputTooLargeError(
                f"Il bundle atomico per il messaggio '{bundle.message.message_id}' ({bundle_tokens} token stimati) "
                f"supera da solo il limite massimo token consentito per chunk ({max_tokens} token)."
            )

        # Se l'aggiunta supera i limiti e il chunk corrente non è vuoto, chiude il chunk
        exceeds_chars = (current_chars + bundle_chars > max_characters)
        exceeds_bundles = (len(current_chunk) >= max_bundles)
        exceeds_tokens = (max_tokens is not None and (current_tokens + bundle_tokens > max_tokens))

        if current_chunk and (exceeds_chars or exceeds_bundles or exceeds_tokens):
            chunks.append(current_chunk)
            current_chunk = []
            current_chars = 0
            current_tokens = 0

        current_chunk.append(bundle)
        current_chars += bundle_chars
        current_tokens += bundle_tokens

    if current_chunk:
        chunks.append(current_chunk)

    if len(chunks) <= 1:
        res = (document,)
    else:
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
        res = tuple(result_chunks)

    if cached_map is None:
        cached_map = {}
        try:
            object.__setattr__(document, "_cached_chunks_map", cached_map)
        except Exception:
            pass
    cached_map[cache_key] = res
    return res


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
        aggregated_eids_set: set[str] = set()
        for pr in present_results:
            for eid in pr.evidence_ids:
                if eid not in aggregated_eids_set:
                    aggregated_eids_set.add(eid)
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
        aggregated_eids_set: set[str] = set()
        for ur in uncertain_results:
            for eid in ur.evidence_ids:
                if eid not in aggregated_eids_set:
                    aggregated_eids_set.add(eid)
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


ITALIAN_STOP_WORDS: frozenset[str] = frozenset({
    "di", "a", "da", "in", "con", "su", "per", "tra", "fra",
    "il", "lo", "la", "i", "gli", "le", "un", "uno", "una",
    "del", "dello", "della", "dei", "degli", "delle",
    "al", "allo", "alla", "ai", "agli", "alle",
    "dal", "dallo", "dalla", "dai", "dagli", "dalle",
    "nel", "nello", "nella", "nei", "negli", "nelle",
    "sul", "sullo", "sulla", "sui", "sugli", "sulle",
    "d", "l", "ed", "ad",
})


def stem_italian_word(word: str) -> str:
    """Semplice e deterministico normalizzatore morfologico per sostantivi e aggettivi italiani."""
    w = word.strip().casefold()
    if len(w) <= 3:
        return w
    if w.endswith("i"):
        return w[:-1]
    if w.endswith("e") and len(w) > 4:
        return w[:-1]
    if w.endswith(("o", "a")) and len(w) > 4:
        return w[:-1]
    return w


@functools.lru_cache(maxsize=4096)
def canonicalize_topic_label(label: str) -> str:
    """
    Normalizza la label a livello morfologico/sintattico (senza falsi claim di embedding neurale):
    1. Normalizzazione testuale NFKC, rimozione punteggiatura;
    2. Rimozione stop words e preposizioni italiane;
    3. Stemming morfologico deterministico (singolari/plurali e flessioni);
    4. Conservazione rigorosa dell'ordine sintattico dei lemmi per evitare fusioni
       semanticamente improprie (es. 'Cessione di armi a privati' != 'Cessione di privati ad armi').
    Esempi:
    - 'Traffico di armi' e 'Traffico d'armi' -> 'traffic arm'
    - 'Incontri riservati' e 'Incontro riservato' -> 'incontr riservat'
    - 'Spaccio di stupefacenti' e 'Spaccio stupefacenti' -> 'spacci stupefacent'
    """
    raw_norm = normalize_topic_label(label)
    if not raw_norm:
        return ""
    words = raw_norm.split()
    content_words = [w for w in words if w not in ITALIAN_STOP_WORDS]
    if not content_words:
        content_words = words
    stems = [stem_italian_word(w) for w in content_words]
    return " ".join(stems)


@functools.lru_cache(maxsize=4096)
def normalize_topic_label(label: str) -> str:
    """
    Normalizza deterministicamente una label tematica per il consolidamento:
    - Normalizzazione Unicode NFKC;
    - casefold;
    - rimozione punteggiatura (sostituita da spazio) e normalizzazione spazi.
    """
    if not label:
        return ""
    norm = unicodedata.normalize("NFKC", label).casefold()
    norm = norm.translate(_PUNCTUATION_TRANSLATION_TABLE)
    return " ".join(norm.split())


def aggregate_topic_discovery(
    chunk_results: Sequence[TopicDiscoveryResult],
    parent_document_id: str,
    max_topics: int = 10,
) -> TopicDiscoveryResult:
    """
    Aggrega deterministicamente i risultati di Topic Discovery provenienti da più chunk.
    Fonde topic semanticamente/sintatticamente equivalenti (NFKC, casefold, punteggiatura, stem morfologici)
    preservando tutti gli evidence_ids e tracciando il supporto dei chunk.
    Se il numero di topic consolidati supera max_topics, applica Deterministic Support Ranking:
    1. Supporto dei chunk indipendenti (quanti chunk hanno rilevato il tema);
    2. Numero di evidenze uniche a supporto;
    3. Posizione iniziale nel documento;
    4. Tie-breaker alfabetico stabile sulla label normalizzata.
    """
    if not chunk_results:
        return TopicDiscoveryResult(
            topics=(),
            provenance_document_id=parent_document_id,
            metadata={"aggregation": "empty"},
        )

    raw_topics_count = 0
    consolidated: dict[str, dict[str, Any]] = {}
    order_idx = 0

    for chunk_idx, cr in enumerate(chunk_results):
        chunk_id = getattr(cr, "metadata", {}).get("chunk_id") or f"{getattr(cr, 'provenance_document_id', 'doc')}::chunk_{chunk_idx}"
        for t in cr.topics:
            raw_topics_count += 1
            canonical_key = canonicalize_topic_label(t.label) or normalize_topic_label(t.label) or t.label.strip().casefold()
            if canonical_key in consolidated:
                item = consolidated[canonical_key]
                item["supporting_chunks"].add(chunk_id)
                eids_set = item["evidence_ids_set"]
                eids_list = item["evidence_ids"]
                for eid in t.evidence_ids:
                    if eid not in eids_set:
                        eids_set.add(eid)
                        eids_list.append(eid)
                if len(t.short_description.strip()) > len(item["short_description"]):
                    item["short_description"] = t.short_description.strip()
                if len(t.label.strip()) > len(item["label"]):
                    item["label"] = t.label.strip()
            else:
                eids_list = list(t.evidence_ids)
                consolidated[canonical_key] = {
                    "label": t.label.strip(),
                    "norm_key": canonical_key,
                    "short_description": t.short_description.strip(),
                    "evidence_ids": eids_list,
                    "evidence_ids_set": set(eids_list),
                    "supporting_chunks": {chunk_id},
                    "first_seen_index": order_idx,
                }
                order_idx += 1

    consolidated_items = list(consolidated.values())
    consolidated_count = len(consolidated_items)

    if consolidated_count > max_topics:
        ranked_items = sorted(
            consolidated_items,
            key=lambda it: (
                -len(it["supporting_chunks"]),
                -len(it["evidence_ids"]),
                it["first_seen_index"],
                it["norm_key"],
            ),
        )
        selected_items = ranked_items[:max_topics]
        reduction_strategy = "deterministic_support_ranking"
    else:
        selected_items = consolidated_items
        reduction_strategy = "none"

    topics = [
        DiscoveredTopic(
            label=it["label"],
            short_description=it["short_description"],
            evidence_ids=tuple(it["evidence_ids"]),
        )
        for it in selected_items
    ]

    return TopicDiscoveryResult(
        topics=tuple(topics),
        provenance_document_id=parent_document_id,
        metadata={
            "aggregation": "consolidated_multi_chunk",
            "total_raw_chunks": len(chunk_results),
            "raw_topic_count": raw_topics_count,
            "consolidated_topic_count": consolidated_count,
            "selected_topic_count": len(topics),
            "reduction_strategy": reduction_strategy,
            "consolidation_mode": "stem_canonical_deduplication",
            "semantic_deduplication": False,
        },
    )
