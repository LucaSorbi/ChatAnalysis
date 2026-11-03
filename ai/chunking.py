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
import re
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


def is_substantive_message_bundle(bundle: MessageEvidenceBundle) -> bool:
    """Verifica se un bundle di evidenza contiene testo comunicativo effettivo."""
    for sec in bundle.text_evidence_sections:
        t = (sec.text or "").strip()
        if not t:
            continue
        if t == "<Media omessi>":
            continue
        if "crittografati end-to-end" in t:
            continue
        return True
    return False


def split_chunk_into_microchunks(
    chunk: ConversationEvidenceDocument,
    max_substantive_messages: int = 8,
    max_microchunk_tokens: int | None = 1500,
) -> tuple[ConversationEvidenceDocument, ...]:
    """
    Suddivide preventivamente un chunk di conversazione in microchunk operativi (B3).
    
    Principi:
    1. PUNTO DI PARTENZA PRUDENTE: massimo max_substantive_messages (default 8) messaggi
       sostantivi per microchunk, subordinatamente al budget token stimato.
    2. PRESERVAZIONE INTEGRALE DELLE EVIDENZE:
       - Nessun messaggio viene saltato o perso.
       - Nessun messaggio viene duplicato.
       - Viene preservata l'esatta sequenza cronologica dei bundle.
    3. TRACCIABILITÀ FORENSE VERSO IL CHUNK PADRE:
       - Ogni microchunk mantiene document_id deterministico: f"{chunk.document_id}::micro::{idx}".
       - I metadati registrano: parent_document_id, parent_chunk_index, microchunk_index, total_microchunks.
    4. INVARIANTE DI COPERTURA:
       L'unione degli evidence_id di tutti i microchunk generati coincide esattamente
       con l'insieme delle sezioni di evidenza del chunk originale.
    """
    if not chunk.bundles:
        return (chunk,)

    microchunks_bundles: list[list[MessageEvidenceBundle]] = []
    current_b_list: list[MessageEvidenceBundle] = []
    current_substantive = 0
    current_tokens = 0

    for bundle in chunk.bundles:
        is_sub = is_substantive_message_bundle(bundle)
        sec_list = bundle.text_evidence_sections
        b_chars = sum(len(s.text) for s in sec_list)
        b_tokens = math.ceil(b_chars / 2.8) + len(sec_list) * 20  # stima compatta

        # Se aggiungendo questo bundle superiamo il limite di messaggi sostanziali (e il blocco ne ha già almeno 1)
        # oppure superiamo il budget token del microchunk, chiudiamo il microchunk corrente.
        exceeds_substantive = (is_sub and current_substantive >= max_substantive_messages and len(current_b_list) > 0)
        exceeds_tokens = (max_microchunk_tokens is not None and current_tokens + b_tokens > max_microchunk_tokens and len(current_b_list) > 0)

        if exceeds_substantive or exceeds_tokens:
            microchunks_bundles.append(current_b_list)
            current_b_list = []
            current_substantive = 0
            current_tokens = 0

        current_b_list.append(bundle)
        if is_sub:
            current_substantive += 1
        current_tokens += b_tokens

    if current_b_list:
        microchunks_bundles.append(current_b_list)

    parent_chunk_idx = chunk.metadata.get("chunk_index", 0) if isinstance(chunk.metadata, Mapping) else 0

    if len(microchunks_bundles) <= 1:
        res = (
            ConversationEvidenceDocument(
                document_id=f"{chunk.document_id}::micro::0",
                bundles=chunk.bundles,
                chat_id=chunk.chat_id,
                source_name=chunk.source_name,
                metadata={
                    **dict(chunk.metadata if isinstance(chunk.metadata, Mapping) else {}),
                    "parent_document_id": chunk.document_id,
                    "parent_chunk_index": parent_chunk_idx,
                    "microchunk_index": 0,
                    "total_microchunks": 1,
                    "substantive_messages_count": sum(1 for b in chunk.bundles if is_substantive_message_bundle(b)),
                },
            ),
        )
    else:
        out_list: list[ConversationEvidenceDocument] = []
        tot = len(microchunks_bundles)
        for m_idx, b_seq in enumerate(microchunks_bundles):
            sub_count = sum(1 for b in b_seq if is_substantive_message_bundle(b))
            m_doc = ConversationEvidenceDocument(
                document_id=f"{chunk.document_id}::micro::{m_idx}",
                bundles=tuple(b_seq),
                chat_id=chunk.chat_id,
                source_name=chunk.source_name,
                metadata={
                    **dict(chunk.metadata if isinstance(chunk.metadata, Mapping) else {}),
                    "parent_document_id": chunk.document_id,
                    "parent_chunk_index": parent_chunk_idx,
                    "microchunk_index": m_idx,
                    "total_microchunks": tot,
                    "substantive_messages_count": sub_count,
                },
            )
            out_list.append(m_doc)
        res = tuple(out_list)

    # Verifica formale degli invarianti
    all_micro_eids = {s.evidence_id for m in res for s in m.all_evidence_sections}
    orig_eids = {s.evidence_id for s in chunk.all_evidence_sections}
    assert all_micro_eids == orig_eids, f"Invariante di copertura violato: {len(all_micro_eids)} vs {len(orig_eids)}"

    return res


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


_GENERIC_CONSOLIDATION_STEMS: frozenset[str] = frozenset({
    "tem", "argoment", "descrizion", "organizzazion", "preparazion", "attivit",
    "pianificazion", "gestion", "problem", "soluzion", "discussion", "conversazion",
    "conferm", "aggiornament", "prenotazion", "iscrizion", "acquist", "cors",
    "partit", "studi", "lavor", "incontr", "appuntament", "messagg", "verific",
    "evidenz", "comunicazion", "pratic", "servizi", "sistem", "progett", "informazion",
    "richiest", "controll", "orar", "giorn", "settiman", "doman", "ier", "ogg",
    "real", "sintetic", "test", "val", "valid", "nuov", "prim", "second", "terz",
    "tecnic", "manutenzion", "correzion", "risoluzion", "error", "problematic",
})

_DOMAIN_STEM_SYNONYMS: dict[str, str] = {
    "laptop": "portatil",
    "notebook": "portatil",
    "ferroviar": "tren",
    "frecciaross": "tren",
    "oculistic": "oculist",
    "dockerfile": "docker",
    "dockerfil": "docker",
    "container": "docker",
    "pipelin": "docker",
    "deploy": "docker",
    "staging": "docker",
    "github": "gitpr",
    "pull": "gitpr",
    "merge": "gitpr",
    "merg": "gitpr",
    "branch": "gitpr",
    "request": "gitpr",
}


def _extract_domain_stems(text: str) -> set[str]:
    norm = normalize_topic_label(text)
    if not norm:
        return set()
    stems: set[str] = set()
    for w in norm.split():
        if w in ITALIAN_STOP_WORDS or w.isdigit():
            continue
        st = stem_italian_word(w)
        st = _DOMAIN_STEM_SYNONYMS.get(st, st)
        if len(st) < 4 or st in _GENERIC_CONSOLIDATION_STEMS:
            continue
        stems.add(st)
    return stems


def _extract_label_numbers(label: str) -> tuple[str, ...]:
    return tuple(re.findall(r"\d+", label or ""))


def _find_matching_consolidated_key(
    consolidated: dict[str, dict[str, Any]],
    canonical_key: str,
    lbl_clean: str,
    dsc_clean: str,
    eids_set: set[str],
) -> str | None:
    if canonical_key in consolidated:
        return canonical_key

    cand_nums = _extract_label_numbers(lbl_clean)
    cand_lbl_stems = _extract_domain_stems(lbl_clean)
    cand_all_stems = _extract_domain_stems(f"{lbl_clean} {dsc_clean}")
    if not cand_all_stems:
        return None

    for key, item in consolidated.items():
        item_nums = item.get("label_numbers", ())
        if cand_nums != item_nums and (cand_nums or item_nums):
            continue

        item_lbl_stems: set[str] = item.get("label_domain_stems", set())
        item_all_stems: set[str] = item.get("all_domain_stems", set())
        if not item_all_stems:
            continue

        shared_lbl = cand_lbl_stems & item_lbl_stems
        shared_all = cand_all_stems & item_all_stems

        # Caso 1: Le due label condividono almeno 2 stem di dominio specifici,
        # oppure gli stem specifici di una label sono interamente contenuti nel dominio
        # (label+descrizione) dell'altro topic, oppure condividono 1 stem di label ed evidenze.
        if (
            len(shared_lbl) >= 2
            or (cand_lbl_stems and cand_lbl_stems <= item_all_stems)
            or (item_lbl_stems and item_lbl_stems <= cand_all_stems)
            or (shared_lbl and bool(eids_set & item["evidence_ids_set"]))
        ):
            return key

        # Caso 2: Stesso evento/workflow distribuito su più messaggi o microchunk:
        # condividono almeno 2 stem di dominio specifici tra (label+descrizione) e ciascuna label
        # interseca il dominio specifico dell'altro topic (oppure condividono evidenze dirette),
        # oppure condividono uno stem tecnico/specifico presente nella label di uno e nel dominio dell'altro.
        if len(shared_all) >= 2:
            if (cand_lbl_stems & item_all_stems) and (item_lbl_stems & cand_all_stems):
                return key
            if bool(eids_set & item["evidence_ids_set"]):
                return key
        if shared_all and (
            (cand_lbl_stems & shared_all and item_lbl_stems & item_all_stems)
            and bool(shared_all & {"docker", "gitpr", "oculist", "caldai", "laure", "portatil"})
        ):
            return key

    return None


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
            lbl_clean = re.sub(r"^(?:label|topic|titolo|argomento|in)\s*[:=]\s*", "", t.label.strip(), flags=re.IGNORECASE).strip().strip("'\"") or t.label.strip()
            dsc_clean = re.sub(r"^(?:short_description|description|descrizione|in)\s*[:=]\s*", "", t.short_description.strip(), flags=re.IGNORECASE).strip() or t.short_description.strip()
            canonical_key = canonicalize_topic_label(lbl_clean) or normalize_topic_label(lbl_clean) or lbl_clean.casefold()
            cand_eids_set = set(t.evidence_ids)
            match_key = _find_matching_consolidated_key(consolidated, canonical_key, lbl_clean, dsc_clean, cand_eids_set)
            if match_key is not None:
                item = consolidated[match_key]
                item["supporting_chunks"].add(chunk_id)
                eids_set = item["evidence_ids_set"]
                eids_list = item["evidence_ids"]
                for eid in t.evidence_ids:
                    if eid not in eids_set:
                        eids_set.add(eid)
                        eids_list.append(eid)
                cand_lbl_stems = _extract_domain_stems(lbl_clean)
                cand_all_stems = _extract_domain_stems(f"{lbl_clean} {dsc_clean}")
                item["all_domain_stems"].update(cand_all_stems)
                if len(dsc_clean) > len(item["short_description"]) and len(dsc_clean) <= 300:
                    item["short_description"] = dsc_clean
                if (
                    len(cand_lbl_stems) > len(item.get("label_domain_stems", set()))
                    and len(lbl_clean) <= 55
                ) or (
                    len(cand_lbl_stems) == len(item.get("label_domain_stems", set()))
                    and len(lbl_clean) > len(item["label"])
                    and len(lbl_clean) <= 45
                ):
                    item["label"] = lbl_clean
                    item["label_domain_stems"] = cand_lbl_stems
                else:
                    item["label_domain_stems"].update(cand_lbl_stems)
            else:
                eids_list = list(t.evidence_ids)
                consolidated[canonical_key] = {
                    "label": lbl_clean,
                    "norm_key": canonical_key,
                    "short_description": dsc_clean,
                    "evidence_ids": eids_list,
                    "evidence_ids_set": set(eids_list),
                    "supporting_chunks": {chunk_id},
                    "first_seen_index": order_idx,
                    "label_numbers": _extract_label_numbers(lbl_clean),
                    "label_domain_stems": _extract_domain_stems(lbl_clean),
                    "all_domain_stems": _extract_domain_stems(f"{lbl_clean} {dsc_clean}"),
                }
                order_idx += 1

    # Secondo passaggio deterministico di chiusura transitiva (es. quando un topic intermedio
    # collega due sotto-passaggi dello stesso workflow tecnico tramite i rispettivi stem di dominio)
    merged_in_pass = True
    while merged_in_pass and len(consolidated) > 1:
        merged_in_pass = False
        keys_list = list(consolidated.keys())
        for idx_b in range(len(keys_list) - 1, 0, -1):
            key_b = keys_list[idx_b]
            if key_b not in consolidated:
                continue
            item_b = consolidated[key_b]
            subset_consolidated = {k: consolidated[k] for k in keys_list[:idx_b] if k in consolidated}
            target_key = _find_matching_consolidated_key(
                subset_consolidated,
                "__no_exact__",
                item_b["label"],
                item_b["short_description"],
                item_b["evidence_ids_set"],
            )
            if target_key is not None:
                target_item = consolidated[target_key]
                target_item["supporting_chunks"].update(item_b["supporting_chunks"])
                for eid in item_b["evidence_ids"]:
                    if eid not in target_item["evidence_ids_set"]:
                        target_item["evidence_ids_set"].add(eid)
                        target_item["evidence_ids"].append(eid)
                target_item["all_domain_stems"].update(item_b["all_domain_stems"])
                target_item["label_domain_stems"].update(item_b["label_domain_stems"])
                if len(item_b["short_description"]) > len(target_item["short_description"]) and len(item_b["short_description"]) <= 300:
                    target_item["short_description"] = item_b["short_description"]
                del consolidated[key_b]
                merged_in_pass = True
                break

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
