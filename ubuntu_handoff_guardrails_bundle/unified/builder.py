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
from typing import Any, Iterable, Iterator, Sequence

from entity_resolution.models import CandidateEntity, DuplicateCandidate, ResolutionResult
from normalization.models import NormalizedActor, NormalizedRecord
from unified.context import UnifiedBuildContext
from unified.models import Chat, Participant, UnifiedMessage


class UnifiedModelBuilder:
    """
    Builder che assembla istanze di UnifiedMessage da NormalizedRecord.
    Utilizza un UnifiedBuildContext immutabile per il lookup di metadati ed entità.
    """

    def __init__(
        self,
        resolution: ResolutionResult | None = None,
        chat_metadata: dict[str, dict[str, Any]] | None = None,
        context: UnifiedBuildContext | None = None,
    ) -> None:
        """
        Inizializza il builder con un UnifiedBuildContext oppure crea un contesto
        predefinito da ResolutionResult e chat_metadata.
        """
        if context is not None:
            self.context = context
        else:
            self.context = UnifiedBuildContext.create(
                resolution=resolution,
                chat_metadata=chat_metadata,
            )
        self.resolution = self.context.resolution

    @classmethod
    def from_records(
        cls,
        records: Sequence[NormalizedRecord],
        resolution: ResolutionResult | None = None,
        chat_metadata: dict[str, dict[str, Any]] | None = None,
    ) -> UnifiedModelBuilder:
        """
        Factory che pre-analizza una sequenza indicizzabile/replayable di record per estrarre
        metadati contestuali (es. titoli delle chat dai record 'chat') e assembla un UnifiedModelBuilder.
        Richiede Sequence[NormalizedRecord] (es. list o tuple).
        """
        context = UnifiedBuildContext.from_records(
            records=records,
            resolution=resolution,
            chat_metadata=chat_metadata,
        )
        return cls(context=context)

    @property
    def _chat_titles(self) -> MappingProxyType[str, str]:
        return self.context.chat_titles

    @property
    def _ref_to_candidate(self) -> MappingProxyType[tuple[str, str, str], CandidateEntity]:
        return self.context.ref_to_candidate

    @property
    def _ident_to_candidate(self) -> MappingProxyType[str, CandidateEntity]:
        return self.context.ident_to_candidate

    @property
    def _duplicate_map(self) -> MappingProxyType[tuple[str, str], tuple[str, ...]]:
        return self.context.duplicate_map

    def build_stream(
        self, records: Iterable[NormalizedRecord]
    ) -> Iterator[UnifiedMessage]:
        """
        Elabora in streaming una sequenza di NormalizedRecord e produce UnifiedMessage.
        È un generatore puro a singolo passaggio: non esegue scansioni condizionali né
        muta lo stato interno durante lo streaming.
        I record non di tipo 'message' (es. 'chat', 'contact', 'media_ref') vengono saltati.
        """
        for rec in records:
            if rec.record_type != "message":
                continue

            # 1. Costruzione partecipante mittente
            sender = self._build_participant(
                actor=rec.actor_from,
                role="sender",
                source_name=rec.source_name,
                source_record_id=rec.source_record_id,
            )

            # 2. Costruzione partecipante destinatario
            recipient = self._build_participant(
                actor=rec.actor_to,
                role="recipient",
                source_name=rec.source_name,
                source_record_id=rec.source_record_id,
            )

            # 3. Costruzione contenitore di chat
            chat = self._build_chat(
                rec=rec,
                sender=sender,
                recipient=recipient,
            )

            # 4. Lookup deterministico duplicati candidati
            dup_ids = self.context.get_duplicate_ids(rec.source_name, rec.source_record_id)

            # 5. Identificatore deterministico del messaggio
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
        Implementato rigorosamente come list(self.build_stream(records)).
        """
        return list(self.build_stream(records))

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

        # Caso LOCAL_USER (B5): ruolo tecnico del proprietario del dispositivo
        if actor.actor_type == "local_user" or actor.raw_value == "LOCAL_USER":
            cand = self.context.get_candidate_by_ident("LOCAL_USER")
            cand_id = cand.candidate_id if cand else None
            return Participant(
                participant_id="participant:local_user:LOCAL_USER",
                identifier="LOCAL_USER",
                display_name=None,
                entity_candidate_id=cand_id,
                is_local_user=True,
                metadata=MappingProxyType({
                    "actor_type": "local_user",
                    "role": role,
                    "technical_role": True,
                }),
            )

        # Lookup CandidateEntity per reference esatta (source_name, record_id, role)
        cand = self.context.get_candidate_by_ref(source_name, source_record_id, role)

        # Se non trovato per reference, tenta lookup per valore normalizzato
        if cand is None and actor.raw_value:
            lookup_val = actor.normalized_phone or actor.raw_value
            cand = self.context.get_candidate_by_ident(lookup_val)

        # Caso B4 & B2: Se l'entità risolta è di tipo 'group' o il JID è @g.us, NON deve essere promossa a persona!
        is_group_actor = (
            (cand is not None and cand.entity_type == "group")
            or (actor.jid_domain == "g.us")
            or (bool(actor.raw_value and actor.raw_value.endswith("@g.us")))
        )
        if is_group_actor:
            group_ident = (cand.candidate_identifier if cand else actor.raw_value) or "unknown_group"
            return Participant(
                participant_id=f"participant:group:{group_ident}",
                identifier=group_ident,
                display_name=None,  # Evita assegnazione di display_name persona
                entity_candidate_id=cand.candidate_id if cand else None,
                is_local_user=False,
                metadata=MappingProxyType({
                    "actor_type": actor.actor_type,
                    "raw_value": actor.raw_value,
                    "group_jid_as_actor": True,
                    "is_group": True,
                }),
            )

        # Caso B1 & B2: Entità persona risolta (JID individuale o telefono canonicalizzato)
        if cand is not None:
            display_name = cand.display_names[0] if cand.display_names else None
            if cand.entity_type == "jid":
                part_id = f"participant:jid:{cand.candidate_identifier}"
            elif cand.entity_type == "phone":
                part_id = f"participant:phone:{cand.candidate_identifier}"
            elif cand.entity_type == "local_user":
                part_id = "participant:local_user:LOCAL_USER"
            else:
                part_id = f"participant:{cand.entity_type}:{cand.candidate_identifier}"

            return Participant(
                participant_id=part_id,
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

        # Caso B3: Fallback attore non risolto (es. group_participant_A o attore senza correlazione)
        # Identificatore scoped alla source-reference per non fondere occorrenze distinte
        val = actor.normalized_phone or actor.raw_value or "unknown"
        return Participant(
            participant_id=f"participant:unresolved:{source_name}:{source_record_id}:{role}",
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
            title = self.context.get_chat_title(raw_cid)

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

        # Fallback quando chat_id è assente
        if recipient is not None:
            # Se non è determinabile se la chat è 1-to-1 o gruppo, chat_type deve essere 'unknown'
            # a meno che l'identificatore non sia un JID individuale WhatsApp (@s.whatsapp.net)
            is_direct = recipient.identifier.endswith("@s.whatsapp.net")
            chat_type = "direct" if is_direct else "unknown"
            participants = (sender, recipient) if sender else (recipient,)
            return Chat(
                chat_id=f"chat:{rec.source_name}:peer:{recipient.identifier}",
                chat_type=chat_type,
                title=recipient.display_name,
                participants=participants,
                source_name=rec.source_name,
                metadata=MappingProxyType({
                    "peer_identifier": recipient.identifier,
                    "inferred_from_recipient": True,
                }),
            )

        return None
