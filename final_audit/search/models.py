"""
search/models.py
----------------
Modelli dati immutabili per il Search Layer Foundation.

Principi architetturali:
1. DEEP IMMUTABILITY: Tutti i modelli sono frozen dataclass e applicano freeze_structural
   sui dizionari/mapping e convertono sequenze in tuple immutabili.
2. NESSUNA ALTERAZIONE DEI MODELLI PRECEDENTI: Nessun modello dei layer upstream
   (RawRecord, UnifiedMessage, MessageEvidenceBundle, ConversationEvidenceDocument,
   TopicDetectionResult, TopicDiscoveryResult) viene modificato.
3. PROVENANCE DIRETTA: Ciascun hit preserva il riferimento all'evidenza originale,
   l'identificativo esatto (evidence_id), message_id, source_name, source_record_id,
   e il testo originale intatto.
4. INVARIANTI RIGOROSI DI PROVENANCE: EvidenceSearchHit convalida tutti i campi contro
   la TextEvidenceSection sorgente (evidence_id, source_type, message_id, source_name,
   source_record_id, language, original_text).
5. INDIPENDENZA DALLA UI: SearchViewResult fornisce una rappresentazione omogenea
   per la futura UI Streamlit tramite il campo comune display_text, senza markup.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from ai.models import DiscoveredTopic, TopicDecision, TopicDetectionResult
from core.immutability import freeze_structural
from multimodal.evidence import EvidenceSourceType, TextEvidenceSection


class MatchMode(str, Enum):
    """
    Modalità di matching testuale deterministico.

    - PHRASE: la stringa query deve comparire come sequenza contigua (sottostringa esatta post-normalizzazione).
    - ALL_TERMS: tutti i termini della query devono essere presenti come token interi nell'evidenza (AND logico).
    - ANY_TERM: almeno uno dei termini della query deve essere presente come token intero nell'evidenza (OR logico).
    - EXACT: corrispondenza esatta dell'intero testo dell'evidenza (post-normalizzazione).
    """
    PHRASE = "PHRASE"
    ALL_TERMS = "ALL_TERMS"
    ANY_TERM = "ANY_TERM"
    EXACT = "EXACT"


@dataclass(frozen=True)
class EvidenceSearchQuery:
    """
    Specifica immutabile di una query di ricerca sulle evidenze testuali.

    Campi:
    ------
    query_text : str
        Testo da ricercare. Deve essere non vuoto (solleva ValueError altrimenti).
    match_mode : MatchMode
        Modalità di matching (default: PHRASE).
    source_types : tuple[EvidenceSourceType, ...] | None
        Filtro opzionale sulle sorgenti probatorie consentite.
    language : str | None
        Filtro opzionale sul codice lingua.
    source_name : str | None
        Filtro opzionale sul nome della fonte forense (es. 'msgstore_db').
    limit : int | None
        Numero massimo di risultati da restituire (deve essere intero strettamente > 0 se valorizzato).
    """
    query_text: str
    match_mode: MatchMode = MatchMode.PHRASE
    source_types: tuple[EvidenceSourceType, ...] | None = None
    language: str | None = None
    source_name: str | None = None
    limit: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.query_text, str) or not self.query_text.strip():
            raise ValueError("query_text non può essere vuoto o composto solo da spazi")

        if not isinstance(self.match_mode, MatchMode):
            object.__setattr__(self, "match_mode", MatchMode(self.match_mode))

        if self.source_types is not None:
            if not isinstance(self.source_types, (tuple, list, set)):
                raise ValueError(
                    f"source_types deve essere una sequenza di EvidenceSourceType, ricevuto {type(self.source_types)}"
                )
            validated_types = tuple(
                st if isinstance(st, EvidenceSourceType) else EvidenceSourceType(st)
                for st in self.source_types
            )
            object.__setattr__(self, "source_types", validated_types)

        if self.language is not None:
            if not isinstance(self.language, str) or not self.language.strip():
                raise ValueError("language deve essere una stringa non vuota se specificato")
            object.__setattr__(self, "language", self.language.strip())

        if self.source_name is not None:
            if not isinstance(self.source_name, str) or not self.source_name.strip():
                raise ValueError("source_name deve essere una stringa non vuota se specificato")
            object.__setattr__(self, "source_name", self.source_name.strip())

        if self.limit is not None:
            if not isinstance(self.limit, int) or isinstance(self.limit, bool) or self.limit <= 0:
                raise ValueError(f"limit deve essere un intero strettamente positivo (> 0), ricevuto {self.limit}")


@dataclass(frozen=True)
class EvidenceSearchHit:
    """
    Singolo risultato di ricerca su una TextEvidenceSection.

    Preserva la piena provenance forense verso la sezione d'origine verificando
    rigorosamente l'identità di tutti i campi probatori.

    Campi:
    ------
    evidence_id : str
        Identificatore deterministico dell'evidenza (dal bundle originario).
    source_type : EvidenceSourceType
        Tipologia di fonte dell'evidenza.
    message_id : str
        Identificativo del messaggio unificato associato.
    source_name : str
        Nome della sorgente forense d'origine.
    source_record_id : str
        Identificativo del record nella sorgente originaria.
    language : str | None
        Lingua dichiarata o rilevata per la sezione.
    original_text : str
        Testo sorgente integrale e intatto, privo di modifiche o normalizzazioni.
    section : TextEvidenceSection
        Riferimento diretto e immutabile all'oggetto TextEvidenceSection originario.
    matched_terms : tuple[str, ...]
        Termini di query effettivamente riscontrati nella sezione.
    """
    evidence_id: str
    source_type: EvidenceSourceType
    message_id: str
    source_name: str
    source_record_id: str
    language: str | None
    original_text: str
    section: TextEvidenceSection
    matched_terms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.section, TextEvidenceSection):
            raise ValueError(
                f"section deve essere un'istanza di TextEvidenceSection, ricevuto {type(self.section)}"
            )
        if not isinstance(self.source_type, EvidenceSourceType):
            object.__setattr__(self, "source_type", EvidenceSourceType(self.source_type))
        if not isinstance(self.matched_terms, tuple):
            object.__setattr__(self, "matched_terms", tuple(self.matched_terms))

        # Invarianti rigorosi di provenance verso la TextEvidenceSection sorgente
        if self.evidence_id != self.section.evidence_id:
            raise ValueError(
                f"Discordanza evidence_id tra hit ('{self.evidence_id}') e section ('{self.section.evidence_id}')"
            )
        if self.source_type != self.section.source_type:
            raise ValueError(
                f"Discordanza source_type tra hit ('{self.source_type}') e section ('{self.section.source_type}')"
            )
        if self.message_id != self.section.message_id:
            raise ValueError(
                f"Discordanza message_id tra hit ('{self.message_id}') e section ('{self.section.message_id}')"
            )
        if self.source_name != self.section.source_name:
            raise ValueError(
                f"Discordanza source_name tra hit ('{self.source_name}') e section ('{self.section.source_name}')"
            )
        if self.source_record_id != self.section.source_record_id:
            raise ValueError(
                f"Discordanza source_record_id tra hit ('{self.source_record_id}') e section ('{self.section.source_record_id}')"
            )
        if self.language != self.section.language:
            raise ValueError(
                f"Discordanza language tra hit ('{self.language}') e section ('{self.section.language}')"
            )
        if self.original_text != self.section.text:
            raise ValueError(
                "Discordanza original_text: il testo dell'hit non corrisponde al testo della TextEvidenceSection sorgente"
            )


@dataclass(frozen=True)
class EvidenceSearchResult:
    """
    Risultato aggregato e immutabile di una ricerca su evidenze.

    Campi:
    ------
    query : EvidenceSearchQuery
        Query originale eseguita.
    hits : tuple[EvidenceSearchHit, ...]
        Sequenza ordinata deterministica dei risultati restituiti (rispettando eventuale limit).
    total_hits : int
        Numero totale di evidenze che soddisfano la query e i filtri prima dell'applicazione del limit.
    metadata : Mapping[str, Any]
        Metadati diagnostici immutabili (returned_hits, truncated, document_id, ecc.).
    """
    query: EvidenceSearchQuery
    hits: tuple[EvidenceSearchHit, ...] = ()
    total_hits: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.query, EvidenceSearchQuery):
            raise ValueError(f"query deve essere un'istanza di EvidenceSearchQuery, ricevuto {type(self.query)}")
        if not isinstance(self.hits, tuple):
            object.__setattr__(self, "hits", tuple(self.hits))
        if self.total_hits == 0 and len(self.hits) > 0:
            object.__setattr__(self, "total_hits", len(self.hits))
        object.__setattr__(self, "metadata", freeze_structural(self.metadata))


@dataclass(frozen=True)
class TopicSearchHit:
    """
    Risultato di ricerca su evidenze tematiche prodotte dal layer AI
    (TopicDetectionResult o TopicDiscoveryResult).

    Invariante: Quando matched_sections è popolato, deve corrispondere rigorosamente
    per cardinalità e ordinamento a evidence_ids.

    Campi:
    ------
    topic_id : str
        Identificativo del topic o etichetta.
    label : str
        Etichetta human-readable del topic.
    description : str
        Descrizione del topic.
    source_kind : str
        Tipologia di risultato ("TOPIC_DETECTION" o "TOPIC_DISCOVERY").
    decision : TopicDecision | None
        Decisione di rilevamento (PRESENT, ABSENT, UNCERTAIN), solo per detection.
    evidence_ids : tuple[str, ...]
        Identificatori delle evidenze probatorie collegate dal layer AI.
    matched_sections : tuple[TextEvidenceSection, ...]
        Sezioni di evidenza originali risolte tramite EvidenceIndex.
    raw_result : TopicDetectionResult | DiscoveredTopic | None
        Riferimento immutabile al risultato originario prodotto dall'AI.
    metadata : Mapping[str, Any]
        Metadati diagnostici immutabili.
    """
    topic_id: str
    label: str
    description: str
    source_kind: str
    decision: TopicDecision | None = None
    evidence_ids: tuple[str, ...] = ()
    matched_sections: tuple[TextEvidenceSection, ...] = ()
    raw_result: Any = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_ids, tuple):
            object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids))
        if not isinstance(self.matched_sections, tuple):
            object.__setattr__(self, "matched_sections", tuple(self.matched_sections))
        if self.decision is not None and not isinstance(self.decision, TopicDecision):
            object.__setattr__(self, "decision", TopicDecision(self.decision))

        # Invariante Point H: Corrispondenza rigida di lunghezza e ordine con matched_sections
        if self.matched_sections:
            if len(self.evidence_ids) != len(self.matched_sections):
                raise ValueError(
                    f"Discordanza tra evidence_ids ({len(self.evidence_ids)}) e matched_sections ({len(self.matched_sections)})"
                )
            for idx, (eid, sec) in enumerate(zip(self.evidence_ids, self.matched_sections)):
                if not isinstance(sec, TextEvidenceSection):
                    raise ValueError(
                        f"Ogni elemento di matched_sections deve essere TextEvidenceSection, ricevuto {type(sec)} alla posizione {idx}"
                    )
                if eid != sec.evidence_id:
                    raise ValueError(
                        f"Disallineamento d'ordine o identificativo alla posizione {idx}: "
                        f"atteso evidence_id '{eid}', trovato '{sec.evidence_id}'"
                    )

        object.__setattr__(self, "metadata", freeze_structural(self.metadata))


@dataclass(frozen=True)
class TopicSearchResult:
    """
    Risultato aggregato di una ricerca tematica.
    """
    hits: tuple[TopicSearchHit, ...] = ()
    total_hits: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.hits, tuple):
            object.__setattr__(self, "hits", tuple(self.hits))
        if self.total_hits == 0 and len(self.hits) > 0:
            object.__setattr__(self, "total_hits", len(self.hits))
        object.__setattr__(self, "metadata", freeze_structural(self.metadata))


@dataclass(frozen=True)
class SearchViewResult:
    """
    Rappresentazione unificata e immutabile di un risultato per la futura UI Streamlit.

    Garantisce totale disaccoppiamento da HTML e Streamlit: contiene solo
    campi primitivi e strutture immutabili.

    Semantica pulita dei campi di testo:
    - display_text: testo principale comune da mostrare nell'interfaccia
      (testo originale per EVIDENCE, descrizione tematica per TOPIC).
    - original_text: popolato ESCLUSIVAMENTE per riscontri di tipo EVIDENCE;
      sempre None per i risultati AI generati (nessun testo AI viene chiamato 'original_text').

    Campi:
    ------
    result_type : str
        Tipologia di entità ("EVIDENCE", "TOPIC_DETECTION", "TOPIC_DISCOVERY").
    title : str
        Titolo sintetico o etichetta per visualizzazione.
    display_text : str
        Testo primario da presentare all'operatore.
    original_text : str | None
        Testo sorgente autentico (presente solo se result_type == "EVIDENCE").
    evidence_id : str | None
        Identificativo evidenza specifico (se singolo, es. per EVIDENCE).
    evidence_ids : tuple[str, ...]
        Elenco di identificativi evidenza collegati (es. per topic).
    message_id : str | None
        Identificativo del messaggio unificato d'origine.
    source_type : EvidenceSourceType | None
        Fonte probatoria (ORIGINAL_TEXT, OCR, STT, VISION, ecc.).
    language : str | None
        Codice lingua.
    topic_decision : TopicDecision | None
        Esito della decisione semantica (se pertinente).
    provenance : Mapping[str, Any]
        Mappa immutabile contenente tutti i dettagli di provenance forense.
    """
    result_type: str
    title: str
    display_text: str
    original_text: str | None = None
    evidence_id: str | None = None
    evidence_ids: tuple[str, ...] = ()
    message_id: str | None = None
    source_type: EvidenceSourceType | None = None
    language: str | None = None
    topic_decision: TopicDecision | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.result_type, str) or not self.result_type.strip():
            raise ValueError("result_type deve essere una stringa non vuota")
        if not isinstance(self.title, str):
            raise ValueError("title deve essere una stringa")
        if not isinstance(self.display_text, str):
            raise ValueError("display_text deve essere una stringa")
        if not isinstance(self.evidence_ids, tuple):
            object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids))
        if self.source_type is not None and not isinstance(self.source_type, EvidenceSourceType):
            object.__setattr__(self, "source_type", EvidenceSourceType(self.source_type))
        if self.topic_decision is not None and not isinstance(self.topic_decision, TopicDecision):
            object.__setattr__(self, "topic_decision", TopicDecision(self.topic_decision))
        object.__setattr__(self, "provenance", freeze_structural(self.provenance))
