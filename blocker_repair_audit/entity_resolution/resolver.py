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
    ActorCompatibility,
    CandidateEntity,
    DuplicateCandidate,
    EntityReference,
    EvidenceLevel,
    EvidenceType,
    ResolutionEvidence,
    ResolutionResult,
)
from normalization.models import CanonicalMessageType, NormalizedRecord, TimestampTzStatus
from normalization.phone import canonicalize_phone_syntax


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
                        clean_p = canonicalize_phone_syntax(str(raw_phone))
                        if clean_p is not None:
                            display_names_by_phone[clean_p].add(name_str)

        # 3. Costruzione delle CandidateEntity
        candidate_entities: list[CandidateEntity] = []
        entity_counter = 1

        # Traccia i numeri di telefono già assorbiti tramite JID local part
        absorbed_phones: set[str] = set()

        # A0. Risoluzione proprietario dispositivo (LOCAL_USER - ruolo tecnico)
        if local_user_refs:
            sources_involved = set(r.source_name for r in local_user_refs)
            source_recs = tuple((r.source_name, r.source_record_id) for r in local_user_refs)
            evidence = ResolutionEvidence(
                evidence_type=EvidenceType.LOCAL_USER_EXACT,
                evidence_level=EvidenceLevel.EXACT,
                matched_value="LOCAL_USER",
                reason=f"Ruolo locale del dispositivo (LOCAL_USER) determinato dalla direzione del messaggio nelle sorgenti: {', '.join(sorted(sources_involved))}",
                source_records=source_recs,
            )
            candidate_entities.append(
                CandidateEntity(
                    candidate_id=f"entity_candidate:{entity_counter}",
                    candidate_identifier="LOCAL_USER",
                    entity_type="local_user",
                    references=tuple(local_user_refs),
                    evidence_chain=(evidence,),
                    display_names=(),
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
            # C1: Singolo JID osservato != match fra due o più riferimenti
            if len(refs) == 1:
                evidence_list.append(
                    ResolutionEvidence(
                        evidence_type=EvidenceType.IDENTIFIER_OBSERVED,
                        evidence_level=EvidenceLevel.WEAK,
                        matched_value=jid,
                        reason=f"Identificatore JID osservato in un unico riferimento ({refs[0].source_name}:{refs[0].source_record_id})",
                        source_records=source_recs,
                    )
                )
            else:
                if len(sources_involved) > 1:
                    reason = f"Corrispondenza esatta JID WhatsApp tra sorgenti: {', '.join(sorted(sources_involved))}"
                else:
                    reason = f"Identificatore JID coerente presente in più record ({len(refs)}) all'interno di {list(sources_involved)[0]}"

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

            # C2: Singolo numero osservato != match fra due o più riferimenti
            if len(refs) == 1:
                evidence = ResolutionEvidence(
                    evidence_type=EvidenceType.IDENTIFIER_OBSERVED,
                    evidence_level=EvidenceLevel.WEAK,
                    matched_value=phone,
                    reason=f"Numero telefonico osservato in un unico riferimento ({refs[0].source_name}:{refs[0].source_record_id})",
                    source_records=source_recs,
                )
            else:
                evidence = ResolutionEvidence(
                    evidence_type=EvidenceType.PHONE_CANONICAL,
                    evidence_level=EvidenceLevel.STRONG,
                    matched_value=phone,
                    reason=f"Corrispondenza numero telefonico canonicalizzato tra riferimenti ({', '.join(sorted(sources_involved))})",
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
        Rileva candidati duplicati su base di testo identico, timestamp comparabile
        sulla stessa timeline e compatibilità strutturale di mittente e message type.

        Principi:
        1. DuplicateCandidate rimane NON distruttivo (nessun record eliminato o fuso).
        2. Nessun DuplicateCandidate è automaticamente un duplicate confirmed.
        3. Stesso testo + stesso timestamp da soli NON sono EXACT.
        4. Timestamp ABSENT: text-only duplicate detection è disabilitata.
        5. KNOWN_UTC e NAIVE_UNKNOWN NON vengono confrontati come se appartenessero alla stessa timeline.
        6. Compatibilità mittente e tipo messaggio verificata.
        7. Livelli di confidenza prudenti: STRONG o WEAK, mai EXACT.
        """
        # Raggruppa per (testo_pulito, timeline_key)
        # timeline_key separa rigorosamente KNOWN_UTC da NAIVE_UNKNOWN ed esclude ABSENT
        text_time_map: dict[tuple[str, tuple[str, str]], list[NormalizedRecord]] = defaultdict(list)

        for rec in records:
            if rec.record_type != "message" or not rec.text_content:
                continue

            clean_text = rec.text_content.strip()
            if not clean_text:
                continue

            # C4: Se il timestamp è ABSENT, non creare candidati duplicati sulla sola base del testo
            if rec.timestamp.status == TimestampTzStatus.ABSENT:
                continue

            # C3: Comparabilità temporale rigorosa - KNOWN_UTC e NAIVE_UNKNOWN su timeline distinte
            if rec.timestamp.status == TimestampTzStatus.KNOWN_UTC:
                if rec.timestamp.utc_datetime is None:
                    continue
                timeline_key = ("KNOWN_UTC", rec.timestamp.utc_datetime.isoformat())
            elif rec.timestamp.status == TimestampTzStatus.NAIVE_UNKNOWN:
                if rec.timestamp.naive_datetime is None:
                    continue
                timeline_key = ("NAIVE_UNKNOWN", rec.timestamp.naive_datetime.isoformat())
            else:
                continue

            text_time_map[(clean_text, timeline_key)].append(rec)

        duplicates: list[DuplicateCandidate] = []
        dup_counter = 1

        for (text_val, (tz_kind, ts_val)), group in text_time_map.items():
            if len(group) < 2:
                continue

            # Raggruppa in cluster reciprocamente compatibili (mittente e tipo messaggio)
            clusters: list[list[NormalizedRecord]] = []
            for rec in group:
                placed = False
                for cluster in clusters:
                    if all(self._records_are_compatible(rec, c_rec) for c_rec in cluster):
                        cluster.append(rec)
                        placed = True
                        break
                if not placed:
                    clusters.append([rec])

            for cluster in clusters:
                if len(cluster) < 2:
                    continue

                sources = set(r.source_name for r in cluster)
                rec_tuples = tuple((r.source_name, r.source_record_id) for r in cluster)
                is_cross_source = len(sources) > 1

                # Verifica compatibilità attori e tipi su tutte le coppie del cluster
                all_actors_match = True
                for i in range(len(cluster)):
                    for j in range(i + 1, len(cluster)):
                        if self._check_actor_compatibility(
                            cluster[i].actor_from, cluster[j].actor_from,
                            cluster[i].source_name, cluster[j].source_name
                        ) != ActorCompatibility.MATCH:
                            all_actors_match = False
                            break
                    if not all_actors_match:
                        break

                all_types_match = all(
                    self._check_type_compatibility(cluster[0].message_type, r.message_type) == "MATCH"
                    for r in cluster[1:]
                )

                # Requisiti per EvidenceLevel.STRONG (A2, A3, A4):
                # 1. Deve essere cross-source
                # 2. Timeline KNOWN_UTC (per NAIVE_UNKNOWN il fuso potrebbe differire -> al massimo WEAK)
                # 3. Mittente positivamente compatibile (MATCH) su tutto il cluster
                # 4. Message type positivamente compatibile (stesso tipo canonico noto)
                if is_cross_source and tz_kind == "KNOWN_UTC" and all_actors_match and all_types_match:
                    confidence = EvidenceLevel.STRONG
                    reason = (
                        f"Candidato duplicato cross-source confermato da evidenza positiva in {', '.join(sorted(sources))}: "
                        f"stesso testo ('{text_val[:30]}...'), timestamp comparabile '{ts_val}' ({tz_kind}), "
                        f"mittente coincidente e tipo messaggio identico"
                    )
                else:
                    confidence = EvidenceLevel.WEAK
                    if not is_cross_source:
                        reason = (
                            f"Messaggi identici ripetuti all'interno della medesima sorgente {list(sources)[0]}: "
                            f"stesso testo e timestamp compatibile '{ts_val}' ({tz_kind})"
                        )
                    else:
                        weak_factors = []
                        if tz_kind != "KNOWN_UTC":
                            weak_factors.append(f"timeline con timestamp non certo UTC ({tz_kind}, naive timestamp)")
                        if not all_actors_match:
                            weak_factors.append("incertezza sul segnale mittente (actor signal uncertainty, non coincidente positivamente)")
                        if not all_types_match:
                            weak_factors.append("tipo messaggio non positivamente coincidente")
                        factors_str = "; ".join(weak_factors) if weak_factors else "segnale non completamente confermato"
                        reason = (
                            f"Candidato duplicato cross-source con evidenza parziale in {', '.join(sorted(sources))}: "
                            f"stesso testo, timestamp '{ts_val}' ({tz_kind}), confidenza limitata a WEAK ({factors_str})"
                        )

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

    @classmethod
    def _check_actor_compatibility(
        cls,
        a1: NormalizedActor | None,
        a2: NormalizedActor | None,
        s1: str,
        s2: str,
    ) -> ActorCompatibility:
        """
        Verifica la compatibilità semantica tra gli attori mittenti di due record.
        Distingue:
        - MATCH: evidenza positiva di corrispondenza
        - UNKNOWN: informazione insufficiente (NON costituisce prova di identità)
        - INCOMPATIBLE: incompatibilità dimostrata
        """
        # 1. Attore mancante vs noto o mancante -> UNKNOWN
        if a1 is None or a2 is None:
            return ActorCompatibility.UNKNOWN

        # 2. LOCAL_USER
        is_lu1 = a1.actor_type == "local_user" or a1.raw_value == "LOCAL_USER"
        is_lu2 = a2.actor_type == "local_user" or a2.raw_value == "LOCAL_USER"
        if is_lu1 and is_lu2:
            return ActorCompatibility.MATCH
        if is_lu1 != is_lu2:
            # Un attore è LOCAL_USER e l'altro è un attore remoto noto -> INCOMPATIBLE
            if a1.normalized_phone or a2.normalized_phone or a1.jid_local or a2.jid_local:
                return ActorCompatibility.INCOMPATIBLE
            if a1.actor_type in ("phone", "jid", "alias") or a2.actor_type in ("phone", "jid", "alias"):
                return ActorCompatibility.INCOMPATIBLE
            return ActorCompatibility.UNKNOWN

        # 3. Attore con actor_type sconosciuto
        if a1.actor_type == "unknown" or a2.actor_type == "unknown":
            return ActorCompatibility.UNKNOWN

        # 4. Telefoni canonicalizzati
        p1 = a1.normalized_phone
        p2 = a2.normalized_phone
        if p1 and p2:
            return ActorCompatibility.MATCH if p1 == p2 else ActorCompatibility.INCOMPATIBLE

        # 5. JID individuali completi
        j1 = a1.raw_value.strip() if a1.actor_type == "jid" and a1.raw_value else None
        j2 = a2.raw_value.strip() if a2.actor_type == "jid" and a2.raw_value else None
        if j1 and j2:
            return ActorCompatibility.MATCH if j1 == j2 else ActorCompatibility.INCOMPATIBLE

        # 6. Incrocio telefono canonicalizzato vs JID local part
        if p1 and a2.jid_local:
            return ActorCompatibility.MATCH if p1 == a2.jid_local else ActorCompatibility.INCOMPATIBLE
        if p2 and a1.jid_local:
            return ActorCompatibility.MATCH if p2 == a1.jid_local else ActorCompatibility.INCOMPATIBLE

        # 7. Alias raw
        if a1.actor_type == "alias" and a2.actor_type == "alias":
            if a1.raw_value != a2.raw_value:
                return ActorCompatibility.INCOMPATIBLE
            # Stesso alias raw identico:
            # MATCH a livello di stessa sorgente (s1 == s2); cross-source non possiamo assumere identità -> UNKNOWN
            return ActorCompatibility.MATCH if s1 == s2 else ActorCompatibility.UNKNOWN

        # 8. Uno con telefono/JID e l'altro con solo alias -> UNKNOWN (non collegabili senza evidenza)
        if (p1 or j1) and a2.actor_type == "alias":
            return ActorCompatibility.UNKNOWN
        if (p2 or j2) and a1.actor_type == "alias":
            return ActorCompatibility.UNKNOWN

        return ActorCompatibility.UNKNOWN

    @staticmethod
    def _check_type_compatibility(
        t1: CanonicalMessageType,
        t2: CanonicalMessageType,
    ) -> str:
        """
        Verifica compatibilità del tipo di messaggio:
        - MATCH: stesso tipo canonico noto (evidenza positiva)
        - INCOMPATIBLE: tipi noti distinti
        - UNKNOWN: informazione insufficiente (uno o entrambi UNKNOWN/OTHER)
        """
        is_known_1 = t1 not in (CanonicalMessageType.UNKNOWN, CanonicalMessageType.OTHER)
        is_known_2 = t2 not in (CanonicalMessageType.UNKNOWN, CanonicalMessageType.OTHER)

        if is_known_1 and is_known_2:
            return "MATCH" if t1 == t2 else "INCOMPATIBLE"
        return "UNKNOWN"

    @classmethod
    def _records_are_compatible(cls, r1: NormalizedRecord, r2: NormalizedRecord) -> bool:
        """
        Verifica compatibilità semantica tra due record candidati duplicati:
        mittente compatibile e message type compatibile.
        """
        # 1. Verifica compatibilità tipo messaggio
        if cls._check_type_compatibility(r1.message_type, r2.message_type) == "INCOMPATIBLE":
            return False

        # 2. Verifica compatibilità attori mittenti
        actor_comp = cls._check_actor_compatibility(
            r1.actor_from, r2.actor_from, r1.source_name, r2.source_name
        )
        if actor_comp == ActorCompatibility.INCOMPATIBLE:
            return False

        return True

