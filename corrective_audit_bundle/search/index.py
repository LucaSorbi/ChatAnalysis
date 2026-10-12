"""
search/index.py
---------------
Indice deterministico e risolutore di evidenze (evidence_id -> TextEvidenceSection).

Principi architetturali:
1. INTEGRITÀ RIGOROSA: Rifiuta collisioni di evidence_id sollevando EvidenceIntegrityError.
2. NON-HALLUCINATION: La richiesta di un evidence_id inesistente restituisce None
   (tramite .get()) oppure solleva EvidenceNotFoundError (tramite .resolve()).
   Nessun dato viene inventato o inferito.
3. PRESERVAZIONE DELL'ORDINE NATURALE: Le evidenze sono indicizzate e preservate
   nell'ordine deterministico esatto del ConversationEvidenceDocument sorgente:
   ordine dei bundle -> ordine delle TextEvidenceSection nel bundle.
4. IMMUTABILITÀ: L'indice espone strutture di sola lettura (MappingProxyType e tuple).
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Any, Iterable, Mapping

from ai.models import ConversationEvidenceDocument
from multimodal.evidence import MessageEvidenceBundle, TextEvidenceSection


class EvidenceIntegrityError(ValueError):
    """Sollevata in caso di collisione di evidence_id all'interno del documento o collezione."""
    pass


class EvidenceNotFoundError(KeyError):
    """Sollevata quando un evidence_id richiesto non è presente nell'indice."""
    pass


class EvidenceIndex:
    """
    Indice deterministico in memoria per la risoluzione rapida di TextEvidenceSection.

    Mantiene sia il lookup O(1) per evidence_id che la sequenza deterministica
    originale delle sezioni (ordine del ConversationEvidenceDocument).
    """

    def __init__(
        self,
        sections: Iterable[TextEvidenceSection],
        document_id: str | None = None,
    ) -> None:
        """
        Costruisce l'indice a partire da un iterabile ordinato di TextEvidenceSection.
        Solleva EvidenceIntegrityError in caso di evidence_id duplicati.
        """
        self._document_id = document_id
        ordered_sections: list[TextEvidenceSection] = []
        id_map: dict[str, TextEvidenceSection] = {}

        for idx, sec in enumerate(sections):
            if not isinstance(sec, TextEvidenceSection):
                raise ValueError(
                    f"Ogni elemento dell'indice deve essere una TextEvidenceSection, ricevuto {type(sec)} alla posizione {idx}"
                )
            eid = sec.evidence_id
            if eid in id_map:
                raise EvidenceIntegrityError(
                    f"Collisione di integrità: evidence_id '{eid}' duplicato (posizione {idx})."
                )
            id_map[eid] = sec
            ordered_sections.append(sec)

        self._sections: tuple[TextEvidenceSection, ...] = tuple(ordered_sections)
        self._id_map: Mapping[str, TextEvidenceSection] = MappingProxyType(id_map)

    @classmethod
    def from_document(cls, document: ConversationEvidenceDocument) -> EvidenceIndex:
        """
        Costruisce l'indice direttamente da un ConversationEvidenceDocument.
        L'ordine di inserimento è:
        per ciascun bundle in document.bundles:
            per ciascuna sezione in bundle.text_evidence_sections
        """
        if not isinstance(document, ConversationEvidenceDocument):
            raise ValueError(
                f"document deve essere un'istanza di ConversationEvidenceDocument, ricevuto {type(document)}"
            )
        sections: list[TextEvidenceSection] = []
        for bundle in document.bundles:
            sections.extend(bundle.text_evidence_sections)
        return cls(sections=sections, document_id=document.document_id)

    @classmethod
    def from_bundles(cls, bundles: Iterable[MessageEvidenceBundle]) -> EvidenceIndex:
        """
        Costruisce l'indice da una sequenza ordinata di MessageEvidenceBundle.
        """
        sections: list[TextEvidenceSection] = []
        for idx, bundle in enumerate(bundles):
            if not isinstance(bundle, MessageEvidenceBundle):
                raise ValueError(
                    f"Ogni bundle deve essere un'istanza di MessageEvidenceBundle, ricevuto {type(bundle)} alla posizione {idx}"
                )
            sections.extend(bundle.text_evidence_sections)
        return cls(sections=sections)

    @property
    def document_id(self) -> str | None:
        """Identificativo del documento d'origine se disponibile."""
        return self._document_id

    @property
    def all_sections(self) -> tuple[TextEvidenceSection, ...]:
        """Restituisce tutte le sezioni di evidenza nell'ordine deterministico originario."""
        return self._sections

    @property
    def all_evidence_ids(self) -> tuple[str, ...]:
        """Restituisce tutti gli evidence_id registrati nell'ordine deterministico originario."""
        return tuple(sec.evidence_id for sec in self._sections)

    def get(self, evidence_id: str) -> TextEvidenceSection | None:
        """
        Risolve un evidence_id restituendo la TextEvidenceSection originaria,
        oppure None se non presente.
        """
        if not isinstance(evidence_id, str):
            return None
        return self._id_map.get(evidence_id)

    def resolve(self, evidence_id: str) -> TextEvidenceSection:
        """
        Risolve un evidence_id restituendo la TextEvidenceSection originaria.
        Solleva EvidenceNotFoundError se l'identificativo non esiste.
        """
        sec = self.get(evidence_id)
        if sec is None:
            raise EvidenceNotFoundError(
                f"Evidence ID non trovato nell'indice: '{evidence_id}'"
            )
        return sec

    def resolve_many(
        self,
        evidence_ids: Iterable[str],
        strict: bool = False,
    ) -> tuple[TextEvidenceSection, ...]:
        """
        Risolve una collezione di evidence_id.

        Parametri:
        ----------
        evidence_ids : Iterable[str]
            Identificativi da risolvere.
        strict : bool
            Se True, solleva EvidenceNotFoundError se anche un solo ID non esiste.
            Se False, include solo gli ID effettivamente risolti (ignorando quelli inesistenti).
        """
        resolved: list[TextEvidenceSection] = []
        for eid in evidence_ids:
            sec = self.get(eid)
            if sec is None:
                if strict:
                    raise EvidenceNotFoundError(f"Evidence ID non trovato: '{eid}'")
            else:
                resolved.append(sec)
        return tuple(resolved)

    def __contains__(self, evidence_id: str) -> bool:
        return evidence_id in self._id_map

    def __len__(self) -> int:
        return len(self._sections)

    def __iter__(self):
        return iter(self._sections)
