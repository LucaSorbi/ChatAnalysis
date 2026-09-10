"""
unified/builder.py
------------------
Costruttore del modello unificato: UnifiedModelBuilder.

Converte una sequenza di NormalizedRecord (con l'eventuale ResolutionResult di Entity Resolution)
nella collezione di UnifiedMessage canonici immutabili.

Principi:
- Non-distruttivo: ogni record di tipo messaggio produce un UnifiedMessage. Nessun messaggio
  viene scartato, sovrascritto o fuso arbitrariamente.
- Provenance completa: ogni UnifiedMessage mantiene il riferimento al NormalizedRecord originale
  (e quindi al RawRecord e al ValidationResult).
- Identificatore deterministico: message_id nel formato 'unified:{source_name}:{source_record_id}'.
- Integrità temporale: NormalizedTimestamp è preservato inalterato. I timestamp locali naive
  (NAIVE_UNKNOWN) non vengono mai convertiti con assunzioni di fuso orario né uniti in una timeline
  unica forzata con timestamp UTC (KNOWN_UTC).
- Duplicate linking: associa esplicitamente duplicate_candidate_ids senza alcuna eliminazione.
- Streaming: yield lazy incrementale dei messaggi.
"""
from __future__ import annotations

from collections import defaultdict
from types import MappingProxyType
from typing import Any, Iterable, Iterator

from entity_resolution.models import CandidateEntity, DuplicateCandidate, ResolutionResult
from normalization.models import NormalizedActor, NormalizedRecord
from unified.models import Chat, Participant, UnifiedMessage


class UnifiedModelBuilder:
    """
    Builder che assembla istanze di UnifiedMessage da NormalizedRecord.
    """

    def __init__(
        self,
        resolution: ResolutionResult | None = None,
        chat_metadata: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """
        Inizializza il builder con l'eventuale risultato di Entity Resolution
        e metadati aggiuntivi opzionali sulle chat.
        """
        self.resolution = resolution
        self._chat_titles: dict[str, str] = {}
        if chat_metadata:
            for cid, meta in chat_metadata.items():
                if "title" in meta and meta["title"]:
                    self._chat_titles[cid] = str(meta["title"])

        # Indici di risoluzione
        self._ref_to_candidate: dict[tuple[str, str, str], CandidateEntity] = {}
        self._ident_to_candidate: dict[str, CandidateEntity] = {}
        self._duplicate_map: dict[tuple[str, str], list[str]] = defaultdict(list)

        if resolution is not None:
            self._index_resolution(resolution)

    def _index_resolution(self, resolution: ResolutionResult) -> None:
        """
        Indicizza CandidateEntity e DuplicateCandidate per lookup deterministico O(1).
        """
        for cand in resolution.candidate_entities:
            self._ident_to_candidate[cand.candidate_identifier] = cand
            for ref in cand.references:
                key = (ref.source_name, ref.source_record_id, ref.actor_role)
                self._ref_to_candidate[key] = cand

        for dup in resolution.duplicate_candidates:
            for rec_key in dup.records:
                self._duplicate_map[rec_key].append(dup.candidate_id)

    def build_stream(
        self, records: Iterable[NormalizedRecord]
    ) -> Iterator[UnifiedMessage]:
        """
        Elabora in streaming una sequenza di NormalizedRecord e produce UnifiedMessage.
        I record non di tipo 'message' (es. 'chat', 'contact', 'media_ref') vengono utilizzati
        per arricchire i metadati contestuali (es. titoli chat) senza produrre UnifiedMessage.
        """
        # Se records è già una collezione indicizzabile, pre-indicizza i record 'chat'
        if isinstance(records, (list, tuple)):
            for rec in records:
                if rec.record_type == "chat":
                    raw_f = rec.raw_record.raw_fields
                    subj = raw_f.get("subject")
                    remote_jid = raw_f.get("key_remote_jid")
                    if remote_jid and subj:
                        self._chat_titles[str(remote_jid).strip()] = str(subj).strip()

        for rec in records:
            # 1. Se il record è un contenitore di chat, memorizza i metadati descrittivi
            if rec.record_type == "chat":
                raw_f = rec.raw_record.raw_fields
                subj = raw_f.get("subject")
                remote_jid = raw_f.get("key_remote_jid")
                if remote_jid and subj:
                    self._chat_titles[str(remote_jid).strip()] = str(subj).strip()
                continue

            # 2. Se non è un messaggio, passa oltre (es. 'contact', 'media_ref')
            if rec.record_type != "message":
                continue

            # 3. Costruzione partecipante mittente
            sender = self._build_participant(
                actor=rec.actor_from,
                role="sender",
                source_name=rec.source_name,
                source_record_id=rec.source_record_id,
            )

            # 4. Costruzione partecipante destinatario
            recipient = self._build_participant(
                actor=rec.actor_to,
                role="recipient",
                source_name=rec.source_name,
                source_record_id=rec.source_record_id,
            )

            # 5. Costruzione contenitore di chat
            chat = self._build_chat(
                rec=rec,
                sender=sender,
                recipient=recipient,
            )

            # 6. Lookup duplicati candidati
            rec_key = (rec.source_name, rec.source_record_id)
            dup_ids = tuple(sorted(self._duplicate_map.get(rec_key, [])))

            # 7. Identificatore deterministico del messaggio
            message_id = f"unified:{rec.source_name}:{rec.source_record_id}"

            yield UnifiedMessage(
                message_id=message_id,
                source_name=rec.source_name,
                source_record_id=rec.source_record_id,
                source_path=rec.raw_record.source_path,
                record_type=rec.record_type,
                timestamp=rec.timestamp,
                sender=sender,
                recipient=recipient,
                chat=chat,
                message_type=rec.message_type,
                raw_message_type=rec.raw_message_type,
                text_content=rec.text_content,
                media_reference=rec.media_reference,
                is_deleted=rec.is_deleted,
                raw_deleted=rec.raw_deleted,
                duplicate_candidate_ids=dup_ids,
                provenance_record=rec,
                metadata=rec.metadata,
            )

    def build_all(self, records: Iterable[NormalizedRecord]) -> list[UnifiedMessage]:
        """
        Materializza tutti i messaggi unificati in una lista.
        Pre-indicizza i metadati dei contenitori di chat per garantire l'arricchimento completo.
        """
        rec_list = list(records) if not isinstance(records, (list, tuple)) else records
        for rec in rec_list:
            if rec.record_type == "chat":
                raw_f = rec.raw_record.raw_fields
                subj = raw_f.get("subject")
                remote_jid = raw_f.get("key_remote_jid")
                if remote_jid and subj:
                    self._chat_titles[str(remote_jid).strip()] = str(subj).strip()
        return list(self.build_stream(rec_list))

    def _build_participant(
        self,
        actor: NormalizedActor | None,
        role: str,
        source_name: str,
        source_record_id: str,
    ) -> Participant | None:
        """
        Costruisce un'istanza immutabile di Participant correlando l'attore normalizzato
        con i risultati di Entity Resolution.
        """
        if actor is None:
            return None

        # Caso LOCAL_USER: proprietario del dispositivo
        if actor.actor_type == "local_user" or actor.raw_value == "LOCAL_USER":
            cand = self._ident_to_candidate.get("LOCAL_USER")
            cand_id = cand.candidate_id if cand else None
            return Participant(
                participant_id="participant:LOCAL_USER",
                identifier="LOCAL_USER",
                display_name="LOCAL_USER",
                entity_candidate_id=cand_id,
                is_local_user=True,
                metadata=MappingProxyType({"actor_type": "local_user", "role": role}),
            )

        # Lookup CandidateEntity per reference esatta (source_name, record_id, role)
        ref_key = (source_name, source_record_id, role)
        cand = self._ref_to_candidate.get(ref_key)

        # Se non trovato per reference, tenta lookup per valore normalizzato
        if cand is None and actor.raw_value:
            lookup_val = actor.normalized_phone or actor.raw_value
            cand = self._ident_to_candidate.get(lookup_val)

        if cand is not None:
            display_name = cand.display_names[0] if cand.display_names else None
            return Participant(
                participant_id=f"participant:{cand.candidate_id}",
                identifier=cand.candidate_identifier,
                display_name=display_name,
                entity_candidate_id=cand.candidate_id,
                is_local_user=False,
                metadata=MappingProxyType({
                    "actor_type": actor.actor_type,
                    "raw_value": actor.raw_value,
                    "entity_type": cand.entity_type,
                }),
            )

        # Fallback non risolto: identità deterministica basata su sorgente e valore
        val = actor.normalized_phone or actor.raw_value or "unknown"
        return Participant(
            participant_id=f"participant:{source_name}:{val}",
            identifier=val,
            display_name=None,
            entity_candidate_id=None,
            is_local_user=False,
            metadata=MappingProxyType({
                "actor_type": actor.actor_type,
                "raw_value": actor.raw_value,
                "unresolved": True,
            }),
        )

    def _build_chat(
        self,
        rec: NormalizedRecord,
        sender: Participant | None,
        recipient: Participant | None,
    ) -> Chat | None:
        """
        Costruisce il contenitore Chat associato al messaggio.
        """
        chat_id_val = rec.chat_id

        if chat_id_val is not None and str(chat_id_val).strip():
            raw_cid = str(chat_id_val).strip()
            is_group = "@g.us" in raw_cid
            chat_type = "group" if is_group else ("direct" if "@s.whatsapp.net" in raw_cid else "unknown")
            title = self._chat_titles.get(raw_cid)

            participants: list[Participant] = []
            if sender:
                participants.append(sender)
            if recipient and recipient not in participants:
                participants.append(recipient)

            return Chat(
                chat_id=f"chat:{rec.source_name}:{raw_cid}",
                chat_type=chat_type,
                title=title,
                participants=tuple(participants),
                source_name=rec.source_name,
                metadata=MappingProxyType({"raw_chat_id": raw_cid}),
            )

        # Fallback 1-to-1 se destinatario noto
        if recipient is not None:
            participants = (sender, recipient) if sender else (recipient,)
            return Chat(
                chat_id=f"chat:{rec.source_name}:direct:{recipient.identifier}",
                chat_type="direct",
                title=recipient.display_name,
                participants=participants,
                source_name=rec.source_name,
                metadata=MappingProxyType({"direct_peer": recipient.identifier}),
            )

        return None
