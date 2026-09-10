"""
entity_resolution/resolver.py
-----------------------------
Implementazione concreta della Entity Resolution Foundation: DeterministicEntityResolver.

Principi:
- Deterministico, riproducibile, privo di euristiche probabilistiche o modelli AI.
- Evidence-based: ogni collegamento genera un'istanza immutabile di ResolutionEvidence.
- Priorità al JID esatto: WhatsApp msgstore.db <-> wa.db contatti (EvidenceLevel.EXACT).
- Canonical phone matching: numeri internazionali ripuliti tra le sorgenti (EvidenceLevel.STRONG).
- Collegamento deterministico Telefono <-> JID local part se e solo se coincidenza esatta (EvidenceLevel.STRONG).
- Incompatibilità o assenza di evidenza:
  - 'group_participant_A' -> UNRESOLVED (UNRESOLVED_ALIAS);
  - Cellebrite 'chat_1' -> UNRESOLVED (UNRESOLVED_CHAT_ID);
  - Timestamp o testo simile da soli -> UNRESOLVED (UNRESOLVED_HEURISTIC, nessuna fusione).
- Non-distruttivo:
  - Nessun record viene cancellato;
  - DuplicateCandidate raggruppa candidati duplicati cross-source preservando tutti i record;
  - Nessun UnifiedMessage o Participant definitivo creato.
- Tracciamento dello stato in memoria esplicitato nei metadati del risultato.
"""
from __future__ import annotations

from collections import defaultdict
from types import MappingProxyType
from typing import Any, Iterable

from entity_resolution.base import BaseEntityResolver
from entity_resolution.models import (
    CandidateEntity,
    DuplicateCandidate,
    EntityReference,
    EvidenceLevel,
    EvidenceType,
    ResolutionEvidence,
    ResolutionResult,
)
from normalization.models import NormalizedRecord


class DeterministicEntityResolver(BaseEntityResolver):
    """
    Resolver deterministico basato su regole di evidenza forense verificabile.
    """

    def resolve(self, records: Iterable[NormalizedRecord]) -> ResolutionResult:
        """
        Esegue la risoluzione deterministica su una sequenza di NormalizedRecord.
        """
        # Materializzazione dei record per consentire indicizzazione bidirezionale
        record_list = list(records)
        total_records = len(record_list)

        # 1. Estrazione di tutti i riferimenti ad attori (EntityReference)
        all_refs: list[EntityReference] = []
        # Mapping record_key -> NormalizedRecord per correlazioni successive
        record_map: dict[tuple[str, str], NormalizedRecord] = {}

        for rec in record_list:
            rec_key = (rec.source_name, rec.source_record_id)
            record_map[rec_key] = rec

            # Non estrarre riferimenti entità persona da record di tipo chat o media_ref
            if rec.record_type in ("chat", "media_ref"):
                continue

            # Attore mittente o contatto
            if rec.actor_from is not None:
                if rec.record_type == "contact":
                    role = "contact"
                elif rec.actor_from.actor_type == "local_user":
                    role = "local_user"
                else:
                    role = "sender"
                norm_val = self._compute_normalized_value(rec.actor_from)
                all_refs.append(
                    EntityReference(
                        source_name=rec.source_name,
                        source_record_id=rec.source_record_id,
                        actor_role=role,
                        raw_value=rec.actor_from.raw_value or "",
                        actor_type=rec.actor_from.actor_type,
                        normalized_value=norm_val,
                    )
                )

            # Eventuale attore destinatario (es. To in Cellebrite CSV o msgstore_db)
            if rec.actor_to is not None:
                role = "local_user" if rec.actor_to.actor_type == "local_user" else "recipient"
                norm_val = self._compute_normalized_value(rec.actor_to)
                all_refs.append(
                    EntityReference(
                        source_name=rec.source_name,
                        source_record_id=rec.source_record_id,
                        actor_role=role,
                        raw_value=rec.actor_to.raw_value or "",
                        actor_type=rec.actor_to.actor_type,
                        normalized_value=norm_val,
                    )
                )

        # 2. Partizionamento: riferimenti risolvibili vs non risolvibili
        jid_groups: dict[str, list[EntityReference]] = defaultdict(list)
        phone_groups: dict[str, list[EntityReference]] = defaultdict(list)
        local_user_refs: list[EntityReference] = []
        unresolved_refs: list[EntityReference] = []

        for ref in all_refs:
            if ref.actor_type == "local_user":
                local_user_refs.append(ref)
            elif ref.actor_type == "jid" and ref.normalized_value:
                jid_groups[ref.normalized_value].append(ref)
            elif ref.actor_type == "phone" and ref.normalized_value:
                phone_groups[ref.normalized_value].append(ref)
            else:
                # Alias ('group_participant_A'), Chat ID ('chat_1'), Unknown
                unresolved_refs.append(ref)

        # Mappa dei display name da wa.db contatti (jid -> display_name, phone -> display_name)
        display_names_by_jid: dict[str, set[str]] = defaultdict(set)
        display_names_by_phone: dict[str, set[str]] = defaultdict(set)

        for rec in record_list:
            if rec.source_name == "wa_db" and rec.record_type == "contact":
                raw_f = rec.raw_record.raw_fields
                d_name = raw_f.get("display_name")
                raw_phone = raw_f.get("phone_number")
                raw_jid = raw_f.get("jid")

                if d_name and str(d_name).strip():
                    name_str = str(d_name).strip()
                    if raw_jid:
                        display_names_by_jid[str(raw_jid).strip()].add(name_str)
                    if raw_phone:
                        clean_p = "+" + "".join(c for c in str(raw_phone) if c.isdigit())
                        display_names_by_phone[clean_p].add(name_str)

        # 3. Costruzione delle CandidateEntity
        candidate_entities: list[CandidateEntity] = []
        entity_counter = 1

        # Traccia i numeri di telefono già assorbiti tramite JID local part
        absorbed_phones: set[str] = set()

        # A0. Risoluzione proprietario dispositivo (LOCAL_USER)
        if local_user_refs:
            sources_involved = set(r.source_name for r in local_user_refs)
            source_recs = tuple((r.source_name, r.source_record_id) for r in local_user_refs)
            evidence = ResolutionEvidence(
                evidence_type=EvidenceType.LOCAL_USER_EXACT,
                evidence_level=EvidenceLevel.EXACT,
                matched_value="LOCAL_USER",
                reason=f"Proprietario del dispositivo (LOCAL_USER) identificato nelle sorgenti: {', '.join(sorted(sources_involved))}",
                source_records=source_recs,
            )
            candidate_entities.append(
                CandidateEntity(
                    candidate_id=f"entity_candidate:{entity_counter}",
                    candidate_identifier="LOCAL_USER",
                    entity_type="local_user",
                    references=tuple(local_user_refs),
                    evidence_chain=(evidence,),
                    display_names=("LOCAL_USER",),
                )
            )
            entity_counter += 1

        # A. Risoluzione dei gruppi JID (con eventuale unione telefono tramite local part per JID individuali)
        for jid, refs in sorted(jid_groups.items()):
            evidence_list: list[ResolutionEvidence] = []
            source_recs = tuple((r.source_name, r.source_record_id) for r in refs)
            sources_involved = set(r.source_name for r in refs)

            is_group_jid = jid.endswith("@g.us")
            if is_group_jid:
                # Requisito A1: I JID di gruppo (@g.us) identificano una chat/gruppo, MAI una persona/contatto.
                # Non vengono uniti ad alcun numero telefonico né trattati come partecipanti persona.
                evidence_list.append(
                    ResolutionEvidence(
                        evidence_type=EvidenceType.GROUP_JID_EXACT,
                        evidence_level=EvidenceLevel.EXACT,
                        matched_value=jid,
                        reason=f"Identificatore JID di gruppo WhatsApp (@g.us) presente in: {', '.join(sorted(sources_involved))}",
                        source_records=source_recs,
                    )
                )
                candidate_entities.append(
                    CandidateEntity(
                        candidate_id=f"entity_candidate:{entity_counter}",
                        candidate_identifier=jid,
                        entity_type="group",
                        references=tuple(refs),
                        evidence_chain=tuple(evidence_list),
                        display_names=(),
                    )
                )
                entity_counter += 1
                continue

            # JID Individuale (@s.whatsapp.net o altro utente)
            if len(sources_involved) > 1:
                reason = f"Corrispondenza esatta JID WhatsApp tra sorgenti: {', '.join(sorted(sources_involved))}"
            else:
                reason = f"Identificatore JID coerente all'interno di {list(sources_involved)[0]}"

            evidence_list.append(
                ResolutionEvidence(
                    evidence_type=EvidenceType.JID_EXACT,
                    evidence_level=EvidenceLevel.EXACT,
                    matched_value=jid,
                    reason=reason,
                    source_records=source_recs,
                )
            )

            # Verifica collegamento deterministico Telefono <-> JID local part SOLO per individual JID
            all_entity_refs = list(refs)
            local_part = jid.split("@", 1)[0]
            if local_part in phone_groups:
                phone_refs = phone_groups[local_part]
                absorbed_phones.add(local_part)
                all_entity_refs.extend(phone_refs)

                phone_sources = set(r.source_name for r in phone_refs)
                evidence_list.append(
                    ResolutionEvidence(
                        evidence_type=EvidenceType.PHONE_JID_LOCAL,
                        evidence_level=EvidenceLevel.STRONG,
                        matched_value=local_part,
                        reason=(
                            f"Corrispondenza deterministica tra local part JID '{local_part}' "
                            f"e numero telefonico canonicalizzato presente in: {', '.join(sorted(phone_sources))}"
                        ),
                        source_records=tuple((r.source_name, r.source_record_id) for r in phone_refs),
                    )
                )

            # Raccogli display names associati
            names = set(display_names_by_jid.get(jid, set()))
            if local_part in display_names_by_phone:
                names.update(display_names_by_phone[local_part])

            candidate_entities.append(
                CandidateEntity(
                    candidate_id=f"entity_candidate:{entity_counter}",
                    candidate_identifier=jid,
                    entity_type="jid",
                    references=tuple(all_entity_refs),
                    evidence_chain=tuple(evidence_list),
                    display_names=tuple(sorted(names)),
                )
            )
            entity_counter += 1

        # B. Risoluzione dei gruppi telefonici residui (non assorbiti da JID)
        for phone, refs in sorted(phone_groups.items()):
            if phone in absorbed_phones:
                continue

            sources_involved = set(r.source_name for r in refs)
            source_recs = tuple((r.source_name, r.source_record_id) for r in refs)

            evidence = ResolutionEvidence(
                evidence_type=EvidenceType.PHONE_CANONICAL,
                evidence_level=EvidenceLevel.STRONG,
                matched_value=phone,
                reason=f"Corrispondenza numero telefonico canonicalizzato tra sorgenti: {', '.join(sorted(sources_involved))}",
                source_records=source_recs,
            )

            names = set(display_names_by_phone.get(phone, set()))

            candidate_entities.append(
                CandidateEntity(
                    candidate_id=f"entity_candidate:{entity_counter}",
                    candidate_identifier=phone,
                    entity_type="phone",
                    references=tuple(refs),
                    evidence_chain=(evidence,),
                    display_names=tuple(sorted(names)),
                )
            )
            entity_counter += 1

        # 4. Rilevamento non-distruttivo di DuplicateCandidate (B8)
        duplicate_candidates = self._detect_duplicate_candidates(record_list)

        # Metadati sullo stato in memoria
        metadata: dict[str, Any] = {
            "indexed_jid_count": len(jid_groups),
            "indexed_phone_count": len(phone_groups),
            "absorbed_phone_count": len(absorbed_phones),
            "local_user_ref_count": len(local_user_refs),
            "unresolved_ref_count": len(unresolved_refs),
            "candidate_entity_count": len(candidate_entities),
            "duplicate_candidate_count": len(duplicate_candidates),
            "memory_state_description": (
                "Indici mantenuti in memoria: dizionari di EntityReference indicizzati per "
                "JID completo esatto e per numero telefonico canonicalizzato. "
                "Crescita O(U) dove U e il numero di identificatori unici distinti."
            ),
        }

        return ResolutionResult(
            candidate_entities=tuple(candidate_entities),
            unresolved_references=tuple(unresolved_refs),
            duplicate_candidates=tuple(duplicate_candidates),
            total_records_processed=total_records,
            metadata=MappingProxyType(metadata),
        )

    def _compute_normalized_value(self, actor: Any) -> str | None:
        """
        Determina l'identificatore per l'indicizzazione.
        """
        if actor.actor_type == "jid":
            return actor.raw_value.strip() if actor.raw_value else None
        elif actor.actor_type == "phone":
            return actor.normalized_phone
        elif actor.actor_type == "local_user":
            return "LOCAL_USER"
        return actor.raw_value.strip() if actor.raw_value else None

    def _detect_duplicate_candidates(
        self, records: list[NormalizedRecord]
    ) -> list[DuplicateCandidate]:
        """
        Rileva candidati duplicati cross-source su base di testo e timestamp coincidenti.
        NON elimina alcun record: preserva tutti i riferimenti sorgente.
        """
        text_time_map: dict[tuple[str, str], list[NormalizedRecord]] = defaultdict(list)

        for rec in records:
            # Considera solo messaggi con contenuto testuale significativo
            if rec.record_type != "message" or not rec.text_content:
                continue

            clean_text = rec.text_content.strip()
            if not clean_text:
                continue

            # Chiave temporale (se disponibile)
            ts_key = rec.timestamp.iso_string if rec.timestamp.iso_string else "NO_TS"

            # Raggruppa per (testo esatto, timestamp)
            text_time_map[(clean_text, ts_key)].append(rec)

        duplicates: list[DuplicateCandidate] = []
        dup_counter = 1

        for (text_val, ts_val), group in text_time_map.items():
            if len(group) < 2:
                continue

            sources = set(r.source_name for r in group)
            # Solo se coinvolge sorgenti differenti o ID distinti
            rec_tuples = tuple((r.source_name, r.source_record_id) for r in group)

            if len(sources) > 1:
                reason = (
                    f"Messaggio duplicato candidato cross-source presente in {', '.join(sorted(sources))}: "
                    f"stesso testo ('{text_val[:30]}...') e timestamp '{ts_val}'"
                )
                confidence = EvidenceLevel.EXACT if ts_val != "NO_TS" else EvidenceLevel.STRONG
            else:
                reason = f"Messaggi identici ripetuti all'interno della medesima sorgente {list(sources)[0]}"
                confidence = EvidenceLevel.STRONG

            duplicates.append(
                DuplicateCandidate(
                    candidate_id=f"duplicate_candidate:{dup_counter}",
                    records=rec_tuples,
                    reason=reason,
                    confidence=confidence,
                )
            )
            dup_counter += 1

        return duplicates
