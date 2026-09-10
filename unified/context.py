"""
unified/context.py
------------------
Contesto di esecuzione per la Unified Model Foundation.
Incapsula tutti gli indici di risoluzione entità, metadati delle chat e correlazione
dei duplicati prima della fase di build dei messaggi unificati.

Garantisce:
- Disaccoppiamento tra preparazione dello stato e streaming dei messaggi.
- Immutabilità profonda su tutti gli indici.
- Lookup deterministico O(1).
- Piena equivalenza tra streaming lazy e materializzazione all-at-once.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Sequence

from entity_resolution.models import CandidateEntity, ResolutionResult
from core.immutability import freeze_structural
from normalization.models import NormalizedRecord


@dataclass(frozen=True)
class UnifiedBuildContext:
    """
    Contesto immutabile pre-calcolato per UnifiedModelBuilder.
    """
    resolution: ResolutionResult | None = None
    chat_titles: MappingProxyType[str, str] = MappingProxyType({})
    ref_to_candidate: MappingProxyType[tuple[str, str, str], CandidateEntity] = MappingProxyType({})
    ident_to_candidate: MappingProxyType[str, CandidateEntity] = MappingProxyType({})
    duplicate_map: MappingProxyType[tuple[str, str], tuple[str, ...]] = MappingProxyType({})
    metadata: MappingProxyType[str, Any] = MappingProxyType({})

    def __post_init__(self) -> None:
        object.__setattr__(self, "chat_titles", freeze_structural(self.chat_titles))
        object.__setattr__(self, "ref_to_candidate", freeze_structural(self.ref_to_candidate))
        object.__setattr__(self, "ident_to_candidate", freeze_structural(self.ident_to_candidate))
        object.__setattr__(self, "duplicate_map", freeze_structural(self.duplicate_map))
        object.__setattr__(self, "metadata", freeze_structural(self.metadata))

    @classmethod
    def create(
        cls,
        resolution: ResolutionResult | None = None,
        chat_titles: dict[str, str] | None = None,
        chat_metadata: dict[str, dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> UnifiedBuildContext:
        """
        Crea un contesto immutabile a partire da ResolutionResult e dizionari di titoli/metadati.
        """
        merged_titles: dict[str, str] = {}
        if chat_metadata:
            for cid, meta in chat_metadata.items():
                if "title" in meta and meta["title"]:
                    merged_titles[str(cid).strip()] = str(meta["title"]).strip()
        if chat_titles:
            for cid, title in chat_titles.items():
                if title:
                    merged_titles[str(cid).strip()] = str(title).strip()

        ref_to_cand: dict[tuple[str, str, str], CandidateEntity] = {}
        ident_to_cand: dict[str, CandidateEntity] = {}
        dup_map: dict[tuple[str, str], list[str]] = defaultdict(list)

        if resolution is not None:
            for cand in resolution.candidate_entities:
                ident_to_cand[cand.candidate_identifier] = cand
                for ref in cand.references:
                    ref_key = (ref.source_name, ref.source_record_id, ref.actor_role)
                    ref_to_cand[ref_key] = cand

            for dup in resolution.duplicate_candidates:
                for rec_key in dup.records:
                    dup_map[rec_key].append(dup.candidate_id)

        frozen_dup_map = {k: tuple(sorted(v)) for k, v in dup_map.items()}

        return cls(
            resolution=resolution,
            chat_titles=MappingProxyType(merged_titles),
            ref_to_candidate=MappingProxyType(ref_to_cand),
            ident_to_candidate=MappingProxyType(ident_to_cand),
            duplicate_map=MappingProxyType(frozen_dup_map),
            metadata=MappingProxyType(dict(metadata or {})),
        )

    @classmethod
    def from_records(
        cls,
        records: Sequence[NormalizedRecord],
        resolution: ResolutionResult | None = None,
        chat_metadata: dict[str, dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> UnifiedBuildContext:
        """
        Scansiona una collezione indicizzabile o replayable di NormalizedRecord
        (Sequence[NormalizedRecord], es. list o tuple) per estrarre i descrittori delle chat
        (record_type == 'chat') e assembla il contesto immutabile completo.

        NOTA SULL'ITERAZIONE:
        Questo metodo itera sulla collezione per pre-estrarre i metadati prima che i messaggi
        vengano elaborati in streaming. Richiede una Sequence (non un generator monouso che
        verrebbe consumato irreversibilmente). Per generatori monouso, utilizzare
        from_records_and_stream() o materializzare esplicitamente la sequenza.
        """
        titles: dict[str, str] = {}
        if chat_metadata:
            for cid, meta in chat_metadata.items():
                if "title" in meta and meta["title"]:
                    titles[str(cid).strip()] = str(meta["title"]).strip()

        for rec in records:
            if rec.record_type == "chat":
                raw_f = rec.raw_record.raw_fields
                subj = raw_f.get("subject")
                remote_jid = raw_f.get("key_remote_jid")
                if remote_jid and subj:
                    titles[str(remote_jid).strip()] = str(subj).strip()

        return cls.create(
            resolution=resolution,
            chat_titles=titles,
            metadata=metadata,
        )

    @classmethod
    def from_records_and_stream(
        cls,
        records: Iterable[NormalizedRecord],
        resolution: ResolutionResult | None = None,
        chat_metadata: dict[str, dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[UnifiedBuildContext, list[NormalizedRecord]]:
        """
        Helper esplicito per flussi potenzialmente monouso (es. generatori):
        materializza i record in una lista, costruisce il UnifiedBuildContext
        e restituisce la coppia (context, materialized_list) consentendo l'elaborazione
        successiva senza perdita di record né presupposti impliciti di replayability.
        """
        rec_list = list(records)
        context = cls.from_records(
            records=rec_list,
            resolution=resolution,
            chat_metadata=chat_metadata,
            metadata=metadata,
        )
        return context, rec_list

    def get_chat_title(self, chat_id: str) -> str | None:
        """Restituisce il titolo della chat se registrato, altrimenti None."""
        return self.chat_titles.get(chat_id)

    def get_candidate_by_ref(
        self, source_name: str, source_record_id: str, actor_role: str
    ) -> CandidateEntity | None:
        """Lookup O(1) di CandidateEntity per reference esatta."""
        return self.ref_to_candidate.get((source_name, source_record_id, actor_role))

    def get_candidate_by_ident(self, identifier: str) -> CandidateEntity | None:
        """Lookup O(1) di CandidateEntity per identifier normalizzato."""
        return self.ident_to_candidate.get(identifier)

    def get_duplicate_ids(
        self, source_name: str, source_record_id: str
    ) -> tuple[str, ...]:
        """Restituisce la tupla ordinata di candidate_id di duplicati associati al record."""
        return self.duplicate_map.get((source_name, source_record_id), ())
