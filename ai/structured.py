"""
ai/structured.py
----------------
Schemi JSON rigorosi per structured output e validatore deterministico lato Python.

Principi architetturali (Fasi D, H):
1. NESSUN PARSING FRAGILE DI TESTO LIBERO:
   Tutte le interazioni LLM (Topic Detection, Topic Discovery, Traduzione) utilizzano JSON Schema.
2. RI-VALIDAZIONE RIGOROSA LATO PYTHON (FASE D):
   Il client non si fida ciecamente dell'output del modello. Ogni payload JSON viene decodificato e verificato:
   - Rispetto di tipi e strutture ESATTI senza coercizioni forzate (es. int -> str);
   - Rifiuto tassativo di chiavi extra (additionalProperties=False controllato);
   - Enum validi ('PRESENT', 'ABSENT', 'UNCERTAIN');
   - RIFIUTO DI ALLUCINAZIONI: ogni evidence_id citato deve appartenere all'insieme reale di evidenze inviate;
   - Invarianti logiche:
     * PRESENT: almeno 1 evidence_id valido, univoco;
     * ABSENT: lista evidence_ids rigorosamente vuota (nessun ID consentito);
     * UNCERTAIN: evidenze opzionali, univoche;
     * rationale: stringa non vuota;
   - Nessun troncamento silente in Topic Discovery: se topics > max_topics, solleva errore;
   - Preservazione dei metadati di lingua originale e rigetto di traduzioni vuote indebite.
3. CATTURA ECCEZIONI MIRATA:
   parse_and_validate_json cattura esclusivamente json.JSONDecodeError; bug programmatici propagano.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from ai.backend import (
    AiInvalidEvidenceCitationError,
    AiStructuredOutputError,
)
from ai.models import (
    DiscoveredTopic,
    EvidenceTranslationItem,
    TopicDecision,
)
from multimodal.evidence import TextEvidenceSection

TOPIC_DETECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["PRESENT", "ABSENT", "UNCERTAIN"],
            "description": "Verdetto sulla presenza del topic nella conversazione.",
        },
        "evidence_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Elenco degli identificativi esatti delle evidenze che motivano la decisione.",
        },
        "rationale": {
            "type": "string",
            "description": "Spiegazione sintetica ed oggettiva del risultato (non chain-of-thought prolisso).",
        },
    },
    "required": ["decision", "evidence_ids", "rationale"],
    "additionalProperties": False,
}

TOPIC_DISCOVERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "Etichetta sintetica dell'argomento emerso.",
                    },
                    "short_description": {
                        "type": "string",
                        "description": "Breve descrizione oggettiva dell'argomento (nessuna inferenza su identità o dati sensibili).",
                    },
                    "evidence_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Evidenze che supportano l'argomento (almeno una richiesta).",
                    },
                },
                "required": ["label", "short_description", "evidence_ids"],
                "additionalProperties": False,
            },
            "description": "Elenco dei temi discussi nella conversazione.",
        }
    },
    "required": ["topics"],
    "additionalProperties": False,
}

TRANSLATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "evidence_id": {
                        "type": "string",
                        "description": "Identificativo esatto dell'evidenza originale.",
                    },
                    "translated_text": {
                        "type": "string",
                        "description": "Testo fedelmente tradotto nella lingua target.",
                    },
                },
                "required": ["evidence_id", "translated_text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["translations"],
    "additionalProperties": False,
}


def parse_and_validate_json(raw_text: str) -> dict[str, Any]:
    """
    Esegue il parsing JSON del testo grezzo. Cattura esclusivamente json.JSONDecodeError.
    Errori di tipo o bug programmatici propagano senza mascheramento.
    """
    clean_text = raw_text.strip()
    # Supporto trasparente per eventuali markdown code blocks ```json ... ``` generati dal modello
    if clean_text.startswith("```"):
        lines = clean_text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        clean_text = "\n".join(lines).strip()

    try:
        data = json.loads(clean_text)
    except json.JSONDecodeError as e:
        raise AiStructuredOutputError(f"Risposta del modello non conforme a JSON valido: {e}") from e

    if not isinstance(data, dict):
        raise AiStructuredOutputError(f"L'output deve essere un oggetto JSON, ricevuto {type(data).__name__}")
    return data


def validate_topic_detection_payload(
    raw_text: str,
    valid_evidence_ids: set[str],
) -> tuple[TopicDecision, tuple[str, ...], str]:
    """
    Valida la risposta per Topic Detection.
    Verifica chiavi esatte, tipi rigidi, enum e che le citazioni appartengano a valid_evidence_ids.
    """
    data = parse_and_validate_json(raw_text)

    # D2: Verifica chiavi esatte (nessuna chiave extra ammessa)
    expected_keys = {"decision", "evidence_ids", "rationale"}
    actual_keys = set(data.keys())
    if actual_keys != expected_keys:
        missing = expected_keys - actual_keys
        extra = actual_keys - expected_keys
        errs = []
        if missing:
            errs.append(f"chiavi mancanti: {sorted(missing)}")
        if extra:
            errs.append(f"chiavi extra non consentite: {sorted(extra)}")
        raise AiStructuredOutputError(f"Schema Topic Detection violato ({'; '.join(errs)})")

    # Validazione tipo 'decision'
    if not isinstance(data["decision"], str):
        raise AiStructuredOutputError(f"'decision' deve essere stringa, ricevuto {type(data['decision']).__name__}")

    raw_decision = data["decision"].strip()
    try:
        decision = TopicDecision(raw_decision)
    except ValueError:
        raise AiStructuredOutputError(
            f"Decisione '{raw_decision}' non valida. Ammessi: {[d.value for d in TopicDecision]}"
        )

    # Validazione tipo 'evidence_ids'
    raw_eids = data["evidence_ids"]
    if not isinstance(raw_eids, list):
        raise AiStructuredOutputError(f"'evidence_ids' deve essere una lista JSON, ricevuto {type(raw_eids).__name__}")

    for idx, item in enumerate(raw_eids):
        if not isinstance(item, str):
            raise AiStructuredOutputError(
                f"Elemento evidence_ids[{idx}] non è una stringa (ricevuto {type(item).__name__}). Nessuna coercizione permessa."
            )

    # Rifiuto duplicati
    if len(raw_eids) != len(set(raw_eids)):
        raise AiStructuredOutputError(f"evidence_ids contiene elementi duplicati: {raw_eids}")

    # Validazione tipo 'rationale'
    raw_rationale = data["rationale"]
    if not isinstance(raw_rationale, str):
        raise AiStructuredOutputError(f"'rationale' deve essere stringa, ricevuto {type(raw_rationale).__name__}")
    rationale = raw_rationale.strip()
    if not rationale:
        raise AiStructuredOutputError("'rationale' non può essere una stringa vuota")

    # Verifica allucinazioni: ogni evidence_id citato deve esistere nel documento
    hallucinated = set(raw_eids) - valid_evidence_ids
    if hallucinated:
        raise AiInvalidEvidenceCitationError(
            f"Il modello ha citato evidence_id inesistenti o allucinati: {sorted(hallucinated)}",
            invalid_evidence_ids=tuple(sorted(hallucinated)),
        )

    # Invarianti per decisione
    if decision == TopicDecision.PRESENT and len(raw_eids) == 0:
        raise AiStructuredOutputError(
            "Il modello ha restituito decision=PRESENT ma la lista evidence_ids è vuota"
        )
    if decision == TopicDecision.ABSENT and len(raw_eids) != 0:
        raise AiStructuredOutputError(
            f"Il modello ha restituito decision=ABSENT ma ha allegato evidence_ids: {raw_eids}. Per ABSENT deve essere vuota."
        )

    return decision, tuple(raw_eids), rationale


def validate_topic_discovery_payload(
    raw_text: str,
    valid_evidence_ids: set[str],
    max_topics: int = 10,
) -> tuple[DiscoveredTopic, ...]:
    """
    Valida la risposta per Open Topic Discovery.
    Rifiuta output che superano max_topics senza alcun troncamento silente.
    """
    data = parse_and_validate_json(raw_text)

    expected_keys = {"topics"}
    actual_keys = set(data.keys())
    if actual_keys != expected_keys:
        raise AiStructuredOutputError(f"Schema Topic Discovery violato: attese chiavi {expected_keys}, ricevute {actual_keys}")

    raw_topics = data["topics"]
    if not isinstance(raw_topics, list):
        raise AiStructuredOutputError(f"'topics' deve essere una lista JSON, ricevuto {type(raw_topics).__name__}")

    # D5: Divieto di silent truncation: se eccede max_topics, solleva errore!
    if len(raw_topics) > max_topics:
        raise AiStructuredOutputError(
            f"Il modello ha restituito {len(raw_topics)} argomenti, superando il limite consentito di {max_topics}. "
            f"Nessun troncamento silente consentito."
        )

    discovered: list[DiscoveredTopic] = []
    expected_topic_keys = {"label", "short_description", "evidence_ids"}

    for idx, item in enumerate(raw_topics):
        if not isinstance(item, dict):
            raise AiStructuredOutputError(f"Elemento topic[{idx}] deve essere un oggetto JSON")

        item_keys = set(item.keys())
        if item_keys != expected_topic_keys:
            raise AiStructuredOutputError(f"topic[{idx}] presenta chiavi non conformi: {item_keys} != {expected_topic_keys}")

        if not isinstance(item["label"], str) or not item["label"].strip():
            raise AiStructuredOutputError(f"topic[{idx}].label deve essere una stringa non vuota")
        if not isinstance(item["short_description"], str) or not item["short_description"].strip():
            raise AiStructuredOutputError(f"topic[{idx}].short_description deve essere una stringa non vuota")

        eids_raw = item["evidence_ids"]
        if not isinstance(eids_raw, list) or len(eids_raw) == 0:
            raise AiStructuredOutputError(f"topic[{idx}] ('{item['label']}') deve contenere almeno un evidence_id")

        for e_idx, eid_val in enumerate(eids_raw):
            if not isinstance(eid_val, str) or not eid_val.strip():
                raise AiStructuredOutputError(f"topic[{idx}].evidence_ids[{e_idx}] non è una stringa valida")

        if len(eids_raw) != len(set(eids_raw)):
            raise AiStructuredOutputError(f"topic[{idx}] contiene evidence_ids duplicati: {eids_raw}")

        hallucinated = set(eids_raw) - valid_evidence_ids
        if hallucinated:
            raise AiInvalidEvidenceCitationError(
                f"topic[{idx}] ('{item['label']}') cita evidence_id inesistenti: {sorted(hallucinated)}",
                invalid_evidence_ids=tuple(sorted(hallucinated)),
            )

        discovered.append(
            DiscoveredTopic(
                label=item["label"].strip(),
                short_description=item["short_description"].strip(),
                evidence_ids=tuple(eids_raw),
            )
        )

    return tuple(discovered)


def validate_translation_payload(
    raw_text: str,
    expected_sections: Mapping[str, TextEvidenceSection] | Sequence[TextEvidenceSection],
    target_language: str = "it",
) -> tuple[EvidenceTranslationItem, ...]:
    """
    Valida la risposta della traduzione strutturata.
    Verifica che tutti gli evidence_ids attesi siano presenti, senza duplicati o omissioni.
    Preserva l'original_language della sezione originale e rigetta traduzioni vuote indebite.
    """
    data = parse_and_validate_json(raw_text)

    if isinstance(expected_sections, (list, tuple)):
        sections_map: Mapping[str, TextEvidenceSection] = {s.evidence_id: s for s in expected_sections}
    else:
        sections_map = expected_sections

    expected_evidence_ids = set(sections_map.keys())

    expected_keys = {"translations"}
    actual_keys = set(data.keys())
    if actual_keys != expected_keys:
        raise AiStructuredOutputError(f"Schema Translation violato: attese chiavi {expected_keys}, ricevute {actual_keys}")

    translations_raw = data["translations"]
    if not isinstance(translations_raw, list):
        raise AiStructuredOutputError(f"'translations' deve essere una lista JSON, ricevuto {type(translations_raw).__name__}")

    items: list[EvidenceTranslationItem] = []
    seen_eids: set[str] = set()
    expected_item_keys = {"evidence_id", "translated_text"}

    for idx, item in enumerate(translations_raw):
        if not isinstance(item, dict):
            raise AiStructuredOutputError(f"translations[{idx}] deve essere un oggetto JSON")

        item_keys = set(item.keys())
        if item_keys != expected_item_keys:
            raise AiStructuredOutputError(f"translations[{idx}] presenta chiavi non conformi: {item_keys} != {expected_item_keys}")

        if not isinstance(item["evidence_id"], str) or not item["evidence_id"].strip():
            raise AiStructuredOutputError(f"translations[{idx}].evidence_id deve essere una stringa non vuota")
        if not isinstance(item["translated_text"], str):
            raise AiStructuredOutputError(f"translations[{idx}].translated_text deve essere una stringa")

        eid = item["evidence_id"].strip()
        trans_text = item["translated_text"]

        if eid in seen_eids:
            raise AiStructuredOutputError(f"Traduzione duplicata per evidence_id '{eid}'")

        if eid not in expected_evidence_ids:
            raise AiInvalidEvidenceCitationError(
                f"Traduzione per evidence_id non richiesto o inesistente: '{eid}'",
                invalid_evidence_ids=(eid,),
            )

        # D6: Controllo traduzione vuota: ammessa solo se il testo sorgente era vuoto
        original_sec = sections_map[eid]
        if trans_text.strip() == "" and original_sec.text.strip() != "":
            raise AiStructuredOutputError(
                f"Traduzione vuota non ammessa per evidence_id '{eid}' con testo originale non vuoto."
            )

        seen_eids.add(eid)
        items.append(
            EvidenceTranslationItem(
                original_evidence_id=eid,
                original_language=original_sec.language,
                translated_text=trans_text,
                target_language=target_language,
            )
        )

    # Verifica che nessuna evidenza attesa sia stata omessa
    missing = expected_evidence_ids - seen_eids
    if missing:
        raise AiStructuredOutputError(f"Evidenze mancanti nella risposta di traduzione: {sorted(missing)}")

    return tuple(items)
