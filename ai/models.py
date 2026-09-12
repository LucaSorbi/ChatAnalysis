"""
ai/models.py
------------
Modelli immutabili per l'elaborazione AI locale, Topic Analysis e Document Evidence.

Principi architetturali:
1. SEPARAZIONE DEI LIVELLI:
   ConversationEvidenceDocument aggrega MessageEvidenceBundle in sola lettura.
   Nessuna alterazione delle evidenze originali o dei messaggi unificati.
2. NESSUNA FUSIONE CROSS-SOURCE IMPLICITA:
   I bundle appartengono allo scope definito dal chiamante. Nessun merge arbitrario tra WhatsApp e Cellebrite.
   Se source_name è specificato, tutti i bundle devono appartenere a tale sorgente.
   chat_id costituisce uno scope dichiarativo del chiamante (nessun cross-source chat resolution presunto).
3. IDENTIFICATORI DETERMINISTICI E PROVENANCE TRACCIABILE:
   Tutti i risultati di Topic Detection, Topic Discovery e Traduzione citano esplicitamente
   gli evidence_id deterministici e mantengono il riferimento a provenance_document_id.
   Tutti gli evidence_id all'interno di un ConversationEvidenceDocument devono essere univoci (nessuna collisione).
4. DECISIONI TRI-STATO ED INVARIANTI DI DOMINIO:
   TopicDecision comprende PRESENT (almeno 1 evidence_id univoco), ABSENT (evidence_ids rigorosamente vuoto)
   e UNCERTAIN (evidenze ambigue opzionali univoche).
5. IMMUTABILITÀ PROFONDA:
   Tutte le strutture sono @dataclass(frozen=True) e impiegano core.immutability.freeze_structural.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from core.immutability import freeze_structural
from multimodal.evidence import MessageEvidenceBundle, TextEvidenceSection


class ModelFamily(str, Enum):
    """Famiglie di modelli LLM supportate nell'analisi sperimentale."""
    LLAMA = "LLAMA"
    QWEN = "QWEN"
    DEEPSEEK = "DEEPSEEK"
    OTHER = "OTHER"


@dataclass(frozen=True)
class ExperimentModelSpec:
    """
    Specifica sperimentale del modello locale da confrontare.
    """
    family: ModelFamily
    model_id: str
    quantization: str | None = None
    parameter_size: str | None = None
    context_length: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.family, ModelFamily):
            object.__setattr__(self, "family", ModelFamily(self.family))
        if not isinstance(self.model_id, str) or not self.model_id.strip():
            raise ValueError("model_id deve essere una stringa non vuota")
        object.__setattr__(self, "metadata", freeze_structural(self.metadata))


@dataclass(frozen=True)
class ConversationEvidenceDocument:
    """
    Documento aggregatore di evidenze per una specifica conversazione/chat.

    Campi:
    ------
    document_id : str
        Identificativo tecnico deterministico del documento (es. 'doc::msgstore::chat_1').
    bundles : tuple[MessageEvidenceBundle, ...]
        Sequenza ordinata dei bundle di evidenza appartenenti alla conversazione.
    chat_id : str | None
        Identificativo opzionale della chat/conversazione analizzata (scope dichiarativo del chiamante).
    source_name : str | None
        Sorgente forense principale (es. 'msgstore_db'). Nessuna fusione cross-source automatica.
        Se specificato, tutti i bundle devono provenire da questa medesima sorgente.
    metadata : Mapping[str, Any]
        Metadati aggiuntivi immutabili.
    """
    document_id: str
    bundles: tuple[MessageEvidenceBundle, ...]
    chat_id: str | None = None
    source_name: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.document_id, str) or not self.document_id.strip():
            raise ValueError("document_id deve essere una stringa non vuota")

        # Conversione e validazione della sequenza di bundle
        if not isinstance(self.bundles, (tuple, list)):
            raise ValueError(f"bundles deve essere una sequenza, ricevuto {type(self.bundles)}")

        validated_bundles: list[MessageEvidenceBundle] = []
        seen_eids: set[str] = set()

        for b in self.bundles:
            if not isinstance(b, MessageEvidenceBundle):
                raise ValueError(f"Ogni elemento di bundles deve essere un MessageEvidenceBundle, ricevuto {type(b)}")

            # G2: Source scope check
            if self.source_name is not None and b.message.source_name != self.source_name:
                raise ValueError(
                    f"Disallineamento source_name nel documento '{self.document_id}': atteso '{self.source_name}', "
                    f"trovato '{b.message.source_name}' per il messaggio '{b.message.message_id}'."
                )

            # G1: Evidence ID uniqueness across document
            for sec in b.text_evidence_sections:
                if sec.evidence_id in seen_eids:
                    raise ValueError(
                        f"Rilevata collisione di evidence_id nel documento '{self.document_id}': "
                        f"identificativo '{sec.evidence_id}' duplicato."
                    )
                seen_eids.add(sec.evidence_id)

            validated_bundles.append(b)

        object.__setattr__(self, "bundles", tuple(validated_bundles))
        object.__setattr__(self, "metadata", freeze_structural(self.metadata))

    @property
    def all_evidence_sections(self) -> tuple[TextEvidenceSection, ...]:
        """Restituisce la tupla piatta di tutte le sezioni testuali presenti nei bundle in ordine di inserimento."""
        sections: list[TextEvidenceSection] = []
        for b in self.bundles:
            sections.extend(b.text_evidence_sections)
        return tuple(sections)

    @property
    def message_count(self) -> int:
        return len(self.bundles)

    @property
    def evidence_count(self) -> int:
        return len(self.all_evidence_sections)


@dataclass(frozen=True)
class TopicQuery:
    """
    Query tematica da verificare all'interno del documento di conversazione.
    """
    topic_id: str
    label: str
    description: str

    def __post_init__(self) -> None:
        if not isinstance(self.topic_id, str) or not self.topic_id.strip():
            raise ValueError("topic_id deve essere una stringa non vuota")
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("label deve essere una stringa non vuota")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("description deve essere una stringa non vuota")


class TopicDecision(str, Enum):
    """
    Decisione sull'esistenza del topic nella conversazione.
    """
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True)
class TopicDetectionResult:
    """
    Risultato della verifica mirata di un singolo topic su una conversazione.
    """
    topic: TopicQuery
    decision: TopicDecision
    evidence_ids: tuple[str, ...]
    rationale: str
    provenance_document_id: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.topic, TopicQuery):
            raise ValueError("topic deve essere un'istanza di TopicQuery")
        if not isinstance(self.decision, TopicDecision):
            object.__setattr__(self, "decision", TopicDecision(self.decision))

        if not isinstance(self.rationale, str) or not self.rationale.strip():
            raise ValueError("rationale deve essere una stringa non vuota")

        # Conversione e validazione evidence_ids
        if not isinstance(self.evidence_ids, (tuple, list)):
            raise ValueError(f"evidence_ids deve essere una tupla o lista, ricevuto {type(self.evidence_ids)}")

        eids_tuple = tuple(str(eid) for eid in self.evidence_ids)

        # Rifiuto duplicati
        if len(eids_tuple) != len(set(eids_tuple)):
            raise ValueError(f"evidence_ids contiene identificatori duplicati: {eids_tuple}")

        # Invarianti per decisione
        if self.decision == TopicDecision.PRESENT and len(eids_tuple) == 0:
            raise ValueError("Un topic con decisione PRESENT deve citare almeno un evidence_id valido")
        if self.decision == TopicDecision.ABSENT and len(eids_tuple) != 0:
            raise ValueError("Un topic con decisione ABSENT deve avere evidence_ids rigorosamente vuoto")

        object.__setattr__(self, "evidence_ids", eids_tuple)
        object.__setattr__(self, "metadata", freeze_structural(self.metadata))


@dataclass(frozen=True)
class DiscoveredTopic:
    """
    Singolo argomento emergente rilevato durante l'Open Topic Discovery.
    """
    label: str
    short_description: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("label deve essere una stringa non vuota")
        if not isinstance(self.short_description, str) or not self.short_description.strip():
            raise ValueError("short_description deve essere una stringa non vuota")
        if not self.evidence_ids or len(self.evidence_ids) == 0:
            raise ValueError("DiscoveredTopic deve citare almeno un evidence_id valido")

        eids_tuple = tuple(str(eid) for eid in self.evidence_ids)
        if len(eids_tuple) != len(set(eids_tuple)):
            raise ValueError(f"DiscoveredTopic contiene evidence_ids duplicati: {eids_tuple}")

        object.__setattr__(self, "evidence_ids", eids_tuple)


@dataclass(frozen=True)
class TopicDiscoveryResult:
    """
    Risultato complessivo dell'Open Topic Discovery su una conversazione.
    """
    topics: tuple[DiscoveredTopic, ...]
    provenance_document_id: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.topics, (tuple, list)):
            raise ValueError("topics deve essere una sequenza di DiscoveredTopic")

        validated: list[DiscoveredTopic] = []
        for t in self.topics:
            if not isinstance(t, DiscoveredTopic):
                raise ValueError(f"Ogni elemento di topics deve essere DiscoveredTopic, ricevuto {type(t)}")
            validated.append(t)

        object.__setattr__(self, "topics", tuple(validated))
        object.__setattr__(self, "metadata", freeze_structural(self.metadata))


class AnalysisLanguageStrategy(str, Enum):
    """
    Strategia linguistica applicata nell'analisi AI.
    """
    DIRECT_MULTILINGUAL = "DIRECT_MULTILINGUAL"
    TRANSLATE_FIRST = "TRANSLATE_FIRST"


@dataclass(frozen=True)
class EvidenceTranslationItem:
    """
    Traduzione derivata di una singola sezione di evidenza.
    """
    original_evidence_id: str
    original_language: str | None
    translated_text: str
    target_language: str = "it"

    def __post_init__(self) -> None:
        if not isinstance(self.original_evidence_id, str) or not self.original_evidence_id.strip():
            raise ValueError("original_evidence_id deve essere una stringa non vuota")
        if not isinstance(self.translated_text, str):
            raise ValueError("translated_text deve essere una stringa")


@dataclass(frozen=True)
class EvidenceTranslationResult:
    """
    Insieme delle traduzioni derivate associate a un ConversationEvidenceDocument.
    """
    translations: tuple[EvidenceTranslationItem, ...]
    provenance_document_id: str
    target_language: str = "it"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.translations, (tuple, list)):
            raise ValueError("translations deve essere una sequenza")

        validated: list[EvidenceTranslationItem] = []
        seen_eids: set[str] = set()
        for tr in self.translations:
            if not isinstance(tr, EvidenceTranslationItem):
                raise ValueError(f"Ogni elemento deve essere EvidenceTranslationItem, ricevuto {type(tr)}")
            if tr.original_evidence_id in seen_eids:
                raise ValueError(f"EvidenceTranslationResult contiene traduzione duplicata per '{tr.original_evidence_id}'")
            seen_eids.add(tr.original_evidence_id)
            validated.append(tr)

        object.__setattr__(self, "translations", tuple(validated))
        object.__setattr__(self, "metadata", freeze_structural(self.metadata))

    def get_translation(self, evidence_id: str) -> str | None:
        """Restituisce il testo tradotto associato a un determinato evidence_id originario."""
        for tr in self.translations:
            if tr.original_evidence_id == evidence_id:
                return tr.translated_text
        return None
