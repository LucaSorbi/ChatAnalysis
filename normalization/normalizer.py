"""
normalization/normalizer.py
---------------------------
Implementazione concreta del normalizzatore source-aware: RecordNormalizer.

Principi:
- NORMALIZZAZIONE != VALIDAZIONE != ENTITY RESOLUTION.
- Funzioni pure, deterministiche, senza side-effect, indipendenti da AI/LLM.
- Provenance: NormalizedRecord mantiene il RawRecord e il ValidationResult originali.
- Gestione rigorosa dei timestamp:
  - msgstore.db: millisecondi Unix convertiti in datetime UTC aware (KNOWN_UTC);
  - JSON / XML: ISO-8601 con offset convertito in datetime UTC aware (KNOWN_UTC);
  - CSV con timestamp naive: datetime naive locale conservato senza offset inventati (NAIVE_UNKNOWN);
  - Timestamp vuoto: esplicitamente ABSENT (nessun epoch 0 o date fittizie).
- Normalizzazione attori source-aware:
  - Numeri internazionali ripuliti da spazi/trattini (es. '+39 000 ...' -> '+39000...');
  - JID WhatsApp decomposti in local_part e domain senza collegamenti a contatti;
  - Alias ('group_participant_A') e ID chat ('chat_1') preservati senza forzature numeriche.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from normalization.base import BaseNormalizer
from normalization.models import (
    CanonicalMessageType,
    NormalizedActor,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from normalization.phone import canonicalize_phone_syntax
from validation.models import ValidationResult


class RecordNormalizer(BaseNormalizer):
    """
    Normalizzatore concreto per record forensi provenienti da sorgenti eterogenee.
    """

    def normalize(self, validation_result: ValidationResult) -> NormalizedRecord:
        """
        Produce un NormalizedRecord a partire da un ValidationResult.
        """
        record = validation_result.record
        raw = record.raw_fields

        # 1. Normalizzazione timestamp
        norm_ts = self._normalize_timestamp(record.source_name, record.record_type, raw)

        # 2. Normalizzazione attori (mittente / destinatario)
        actor_from, actor_to = self._normalize_actors(record.source_name, record.record_type, raw)

        # 3. Estrazione identificativo chat
        chat_id = self._extract_chat_id(record.source_name, record.record_type, raw)

        # 4. Normalizzazione message type
        msg_type, raw_msg_type = self._normalize_message_type(record.source_name, record.record_type, raw)

        # 5. Normalizzazione deleted
        is_del, raw_del = self._normalize_deleted(record.source_name, raw)

        # 6. Estrazione contenuto testuale
        text_content = self._extract_text_content(record.source_name, raw)

        return NormalizedRecord(
            raw_record=record,
            validation_result=validation_result,
            source_name=record.source_name,
            source_record_id=record.source_record_id,
            record_type=record.record_type,
            timestamp=norm_ts,
            actor_from=actor_from,
            actor_to=actor_to,
            chat_id=chat_id,
            message_type=msg_type,
            raw_message_type=raw_msg_type,
            is_deleted=is_del,
            raw_deleted=raw_del,
            text_content=text_content,
            media_reference=record.media_reference,
            metadata=record.metadata,
        )

    # ------------------------------------------------------------------
    # Normalizzazione Timestamp
    # ------------------------------------------------------------------

    def _normalize_timestamp(self, source_name: str, record_type: str, raw: dict[str, Any]) -> NormalizedTimestamp:
        """
        Normalizza il timestamp in base alla sorgente specifica.
        """
        if source_name == "msgstore_db":
            raw_ts = raw.get("timestamp")
            if raw_ts is not None and isinstance(raw_ts, (int, float)) and raw_ts > 0:
                try:
                    dt = datetime.fromtimestamp(raw_ts / 1000.0, tz=timezone.utc)
                    return NormalizedTimestamp(
                        status=TimestampTzStatus.KNOWN_UTC,
                        utc_datetime=dt,
                        iso_string=dt.isoformat(),
                        raw_value=raw_ts,
                    )
                except (ValueError, OSError, OverflowError):
                    return NormalizedTimestamp(status=TimestampTzStatus.ABSENT, raw_value=raw_ts)
            return NormalizedTimestamp(status=TimestampTzStatus.ABSENT, raw_value=raw_ts)

        elif source_name == "cellebrite_csv":
            raw_ts = raw.get("TimeStamp")
            if raw_ts is None or (isinstance(raw_ts, str) and not raw_ts.strip()):
                return NormalizedTimestamp(status=TimestampTzStatus.ABSENT, raw_value=raw_ts)

            ts_str = str(raw_ts).strip()
            try:
                # Il CSV Cellebrite usa tipicamente 'YYYY-MM-DD HH:MM:SS' privo di timezone
                dt = datetime.fromisoformat(ts_str)
                if dt.tzinfo is not None:
                    # Se inatteso offset presente
                    dt_utc = dt.astimezone(timezone.utc)
                    return NormalizedTimestamp(
                        status=TimestampTzStatus.KNOWN_UTC,
                        utc_datetime=dt_utc,
                        iso_string=dt_utc.isoformat(),
                        raw_value=raw_ts,
                    )
                else:
                    # Timezone sconosciuto / locale: NON inventare UTC o Europe/Rome
                    return NormalizedTimestamp(
                        status=TimestampTzStatus.NAIVE_UNKNOWN,
                        naive_datetime=dt,
                        iso_string=dt.isoformat(),
                        raw_value=raw_ts,
                    )
            except ValueError:
                return NormalizedTimestamp(status=TimestampTzStatus.ABSENT, raw_value=raw_ts)

        elif source_name == "cellebrite_json":
            raw_ts = raw.get("timestamp")
            if raw_ts is None or (isinstance(raw_ts, str) and not raw_ts.strip()):
                return NormalizedTimestamp(status=TimestampTzStatus.ABSENT, raw_value=raw_ts)

            ts_str = str(raw_ts).strip()
            try:
                dt = datetime.fromisoformat(ts_str)
                if dt.tzinfo is not None:
                    dt_utc = dt.astimezone(timezone.utc)
                    return NormalizedTimestamp(
                        status=TimestampTzStatus.KNOWN_UTC,
                        utc_datetime=dt_utc,
                        iso_string=dt_utc.isoformat(),
                        raw_value=raw_ts,
                    )
                else:
                    return NormalizedTimestamp(
                        status=TimestampTzStatus.NAIVE_UNKNOWN,
                        naive_datetime=dt,
                        iso_string=dt.isoformat(),
                        raw_value=raw_ts,
                    )
            except ValueError:
                return NormalizedTimestamp(status=TimestampTzStatus.ABSENT, raw_value=raw_ts)

        elif source_name == "cellebrite_xml":
            # Cerca il campo Timestamp (anche con prefisso Clark notation)
            raw_ts: Any = None
            for k, v in raw.items():
                if k == "Timestamp" or k.endswith("}Timestamp"):
                    raw_ts = v
                    break

            if raw_ts is None or (isinstance(raw_ts, str) and not raw_ts.strip()):
                return NormalizedTimestamp(status=TimestampTzStatus.ABSENT, raw_value=raw_ts)

            ts_str = str(raw_ts).strip()
            try:
                dt = datetime.fromisoformat(ts_str)
                if dt.tzinfo is not None:
                    dt_utc = dt.astimezone(timezone.utc)
                    return NormalizedTimestamp(
                        status=TimestampTzStatus.KNOWN_UTC,
                        utc_datetime=dt_utc,
                        iso_string=dt_utc.isoformat(),
                        raw_value=raw_ts,
                    )
                else:
                    return NormalizedTimestamp(
                        status=TimestampTzStatus.NAIVE_UNKNOWN,
                        naive_datetime=dt,
                        iso_string=dt.isoformat(),
                        raw_value=raw_ts,
                    )
            except ValueError:
                return NormalizedTimestamp(status=TimestampTzStatus.ABSENT, raw_value=raw_ts)

        # Sorgenti prive di timestamp per-record (es. wa_db rubrica)
        return NormalizedTimestamp(status=TimestampTzStatus.ABSENT, raw_value=None)

    # ------------------------------------------------------------------
    # Normalizzazione Attori
    # ------------------------------------------------------------------

    def _normalize_actors(
        self, source_name: str, record_type: str, raw: dict[str, Any]
    ) -> tuple[NormalizedActor | None, NormalizedActor | None]:
        """
        Normalizza in modo conservativo e deterministico mittente (from) e destinatario (to).
        Applica la semantica di direzione (key_from_me) e distingue chat da persone.
        """
        # Non estrarre attori persona da record contenitore chat o riferimenti media
        if record_type in ("chat", "media_ref"):
            return None, None

        if source_name == "msgstore_db":
            if record_type != "message":
                return None, None

            key_from_me = raw.get("key_from_me")
            remote_jid = raw.get("key_remote_jid")
            remote_res = raw.get("remote_resource")
            is_group = bool(remote_jid and "@g.us" in str(remote_jid))

            if key_from_me == 1:
                # Inviato dal proprietario del dispositivo (outgoing)
                actor_from = NormalizedActor(raw_value="LOCAL_USER", actor_type="local_user")
                if not is_group and remote_jid:
                    actor_to = self._normalize_single_actor(remote_jid)
                else:
                    actor_to = None
                return actor_from, actor_to
            elif key_from_me == 0:
                # Ricevuto dal proprietario del dispositivo (incoming)
                actor_to = NormalizedActor(raw_value="LOCAL_USER", actor_type="local_user")
                if remote_res and str(remote_res).strip():
                    actor_from = self._normalize_single_actor(remote_res)
                elif not is_group and remote_jid:
                    actor_from = self._normalize_single_actor(remote_jid)
                else:
                    actor_from = None
                return actor_from, actor_to
            else:
                # Tri-state rigoroso: qualsiasi altro valore (None, valore numerico diverso da 0/1, tipo inatteso)
                # NON deve produrre inferenze arbitrarie su LOCAL_USER né interpretare come incoming.
                # Preserva il dato raw in raw_record; i ruoli attore rimangono non valorizzati (None).
                return None, None

        elif source_name == "wa_db":
            actor_from = self._normalize_single_actor(raw.get("jid") or raw.get("phone_number"))
            return actor_from, None

        elif source_name == "cellebrite_csv":
            actor_from = self._normalize_single_actor(raw.get("From"))
            actor_to = self._normalize_single_actor(raw.get("To"))
            return actor_from, actor_to

        elif source_name == "cellebrite_json":
            actor_from = self._normalize_single_actor(raw.get("sender"))
            return actor_from, None

        elif source_name == "cellebrite_xml":
            raw_sender: Any = None
            for k, v in raw.items():
                if k == "Sender" or k.endswith("}Sender"):
                    raw_sender = v
                    break
            actor_from = self._normalize_single_actor(raw_sender)
            return actor_from, None

        return None, None

    def _extract_chat_id(
        self, source_name: str, record_type: str, raw: dict[str, Any]
    ) -> str | None:
        """
        Estrae l'identificatore del contenitore di chat/conversazione se presente.
        Preserva fedelmente il valore source-level (es. 'chat_1', 'chat_2') senza risolverlo.
        """
        if source_name == "msgstore_db":
            val = raw.get("key_remote_jid")
            return str(val).strip() if val is not None and str(val).strip() else None
        elif source_name == "cellebrite_json":
            val = raw.get("chat_id")
            return str(val).strip() if val is not None and str(val).strip() else None
        elif source_name == "cellebrite_csv":
            val = raw.get("ChatId")
            return str(val).strip() if val is not None and str(val).strip() else None
        return None

    def _normalize_single_actor(self, val: Any) -> NormalizedActor | None:
        """
        Classifica e normalizza deterministicamente una stringa attore.
        """
        if val is None or not str(val).strip():
            return None

        s = str(val).strip()

        # 0. Local user (proprietario del dispositivo)
        if s == "LOCAL_USER":
            return NormalizedActor(
                raw_value="LOCAL_USER",
                actor_type="local_user",
            )

        # 1. JID WhatsApp (es. '123456789@s.whatsapp.net', 'group@g.us')
        if "@" in s:
            parts = s.split("@", 1)
            return NormalizedActor(
                raw_value=s,
                actor_type="jid",
                jid_local=parts[0],
                jid_domain=parts[1],
            )

        # 2. Alias di gruppo / pseudonimo (es. 'group_participant_A')
        if s == "group_participant_A" or s.startswith("group_participant_"):
            return NormalizedActor(
                raw_value=s,
                actor_type="alias",
                alias=s,
            )

        # 3. Chat ID (es. 'chat_1')
        if s.startswith("chat_"):
            return NormalizedActor(
                raw_value=s,
                actor_type="chat_id",
                chat_id=s,
            )

        # 4. Numero telefonico internazionale con prefisso '+' (single source of truth)
        canon_phone = canonicalize_phone_syntax(s)
        if canon_phone is not None:
            return NormalizedActor(
                raw_value=s,
                actor_type="phone",
                normalized_phone=canon_phone,
            )

        # 5. Fallback identificativo non classificato
        return NormalizedActor(
            raw_value=s,
            actor_type="unknown",
        )

    # ------------------------------------------------------------------
    # Normalizzazione Deleted
    # ------------------------------------------------------------------

    def _normalize_deleted(self, source_name: str, raw: dict[str, Any]) -> tuple[bool | None, Any]:
        """
        Mappatura conservativa del campo deleted.
        """
        raw_del: Any = None
        if source_name == "msgstore_db":
            raw_del = raw.get("deleted")
        elif source_name == "cellebrite_csv":
            raw_del = raw.get("Deleted")
        elif source_name == "cellebrite_json":
            meta = raw.get("metadata")
            if isinstance(meta, Mapping):
                raw_del = meta.get("deleted")
        elif source_name == "cellebrite_xml":
            for k, v in raw.items():
                if k == "Deleted" or k.endswith("}Deleted"):
                    raw_del = v
                    break

        if raw_del is None:
            return None, None

        if isinstance(raw_del, bool):
            return raw_del, raw_del

        if isinstance(raw_del, (int, float)):
            if raw_del == 1:
                return True, raw_del
            elif raw_del == 0:
                return False, raw_del

        if isinstance(raw_del, str):
            lower = raw_del.strip().lower()
            if lower in ("true", "1"):
                return True, raw_del
            elif lower in ("false", "0"):
                return False, raw_del

        return None, raw_del

    # ------------------------------------------------------------------
    # Normalizzazione Message Type
    # ------------------------------------------------------------------

    def _normalize_message_type(
        self, source_name: str, record_type: str, raw: dict[str, Any]
    ) -> tuple[CanonicalMessageType, Any]:
        """
        Mappatura dei tipi di messaggio osservati verso CanonicalMessageType.
        """
        if record_type != "message":
            return CanonicalMessageType.UNKNOWN, None

        if source_name == "msgstore_db":
            raw_t = raw.get("media_wa_type")
            if raw_t == 0:
                return CanonicalMessageType.TEXT, raw_t
            elif raw_t == 1:
                return CanonicalMessageType.IMAGE, raw_t
            elif raw_t == 2:
                return CanonicalMessageType.AUDIO, raw_t
            elif raw_t == 3:
                return CanonicalMessageType.VIDEO, raw_t
            return CanonicalMessageType.UNKNOWN, raw_t

        elif source_name == "cellebrite_csv":
            raw_t = raw.get("MessageType")
            if isinstance(raw_t, str):
                lower = raw_t.strip().lower()
                if lower == "text":
                    return CanonicalMessageType.TEXT, raw_t
                elif lower == "image":
                    return CanonicalMessageType.IMAGE, raw_t
                elif lower == "audio":
                    return CanonicalMessageType.AUDIO, raw_t
                elif lower == "video":
                    return CanonicalMessageType.VIDEO, raw_t
            return CanonicalMessageType.UNKNOWN, raw_t

        elif source_name == "cellebrite_json":
            raw_t = raw.get("type")
            if isinstance(raw_t, str):
                lower = raw_t.strip().lower()
                if lower == "text":
                    return CanonicalMessageType.TEXT, raw_t
                elif lower == "audio":
                    return CanonicalMessageType.AUDIO, raw_t
                elif lower == "image":
                    return CanonicalMessageType.IMAGE, raw_t
                elif lower == "video":
                    return CanonicalMessageType.VIDEO, raw_t
            return CanonicalMessageType.UNKNOWN, raw_t

        elif source_name == "cellebrite_xml":
            # Nel formato XML UFDR osservato non esiste un tag Type esplicito né indicatore forte.
            # Assenza di evidenza tipologica != TEXT certo -> UNKNOWN, raw_message_type=None.
            return CanonicalMessageType.UNKNOWN, None

        return CanonicalMessageType.UNKNOWN, None

    # ------------------------------------------------------------------
    # Estrazione Contenuto Testuale
    # ------------------------------------------------------------------

    def _extract_text_content(self, source_name: str, raw: dict[str, Any]) -> str | None:
        """
        Estrae fedelmente il corpo del messaggio testuale.
        """
        if source_name == "msgstore_db":
            val = raw.get("data")
            return str(val) if val is not None else None

        elif source_name == "cellebrite_csv":
            val = raw.get("Body")
            return str(val) if val is not None and str(val).strip() else None

        elif source_name == "cellebrite_json":
            content = raw.get("content")
            if isinstance(content, Mapping):
                val = content.get("text")
                return str(val) if val is not None else None
            return None

        elif source_name == "cellebrite_xml":
            for k, v in raw.items():
                if k == "Body" or k.endswith("}Body"):
                    return str(v) if v is not None and str(v).strip() else None
            return None

        elif source_name == "wa_db":
            val = raw.get("status")
            return str(val) if val is not None else None

        return None
