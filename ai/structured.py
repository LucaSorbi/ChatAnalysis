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
import re
from typing import Any, Mapping, NamedTuple, Sequence

from ai.backend import (
    AiInvalidEvidenceCitationError,
    AiStructuredOutputError,
)
from ai.json_failure import describe_json_failure
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
            "description": "Elenco degli identificativi esatti delle evidenze solo se PRESENT (lista vuota [] se ABSENT).",
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


# Tipi di innesco del controllo di degenerazione (enum chiuso, solo etichette: nessun testo del modello).
DEGENERATION_TRIGGER_CHAR_RUN = "CHAR_RUN"
DEGENERATION_TRIGGER_TAB_RUN = "TAB_RUN"
DEGENERATION_TRIGGER_PATTERN_LOOP = "PATTERN_LOOP"


class DegenerationScan(NamedTuple):
    """
    Esito del controllo di ripetizione sul testo grezzo: SOLO etichette e numeri.
    `message` è la stringa storica (può contenere un frammento del testo del modello) e serve
    esclusivamente a comporre il messaggio d'errore storico; NON va mai esportata né mostrata.
    """

    trigger_kind: str
    message: str
    pattern_length: int | None
    repetition_count: int | None
    run_length: int | None


def scan_degenerate_repetition(text: str, threshold: int = 15) -> DegenerationScan | None:
    """
    Stesso predicato storico di `detect_degenerate_repetition` (stesse regex, stessa precedenza, stessi
    messaggi), ma restituisce anche i numeri che lo descrivono, senza memorizzare il contenuto del pattern.
    NON cambia nulla sulla scelta di accettare o rifiutare una risposta.
    """
    if not text:
        return None
    # 1. Carattere non-spazio singolo ripetuto
    m_char = re.search(r'([^\s])\1{' + str(threshold - 1) + r',}', text)
    if m_char:
        char_repr = repr(m_char.group(1))
        run = len(m_char.group(0))
        return DegenerationScan(
            DEGENERATION_TRIGGER_CHAR_RUN,
            f"Ripetizione ciclica anomala del carattere {char_repr} ({run} volte consecutive)",
            1,
            run,
            run,
        )
    # Tabulazioni ripetute anomale
    m_tab = re.search(r'(\t)\1{11,}', text)
    if m_tab:
        run = len(m_tab.group(0))
        return DegenerationScan(
            DEGENERATION_TRIGGER_TAB_RUN,
            f"Ripetizione ciclica di caratteri tabulari ({run} volte consecutive)",
            1,
            run,
            run,
        )
    # Rimuove temporaneamente sequenze di alias JSON ("E001", "E002", ...) che vengono deduplicate semanticamente
    text_no_aliases = re.sub(r'(?:["\']E\d{3,4}["\']\s*,\s*){2,}', "", text)
    # 2. Pattern di 2-25 caratteri (con almeno un carattere non di spaziatura) ripetuto >= 6 volte consecutive
    for m in re.finditer(r"(.{2,25})\1{5,}", text_no_aliases):
        pat = m.group(1)
        if pat.strip():
            pat_repr = repr(pat)
            reps = len(m.group(0)) // len(pat)
            return DegenerationScan(
                DEGENERATION_TRIGGER_PATTERN_LOOP,
                f"Loop di attrattore su sequenza {pat_repr} ({reps} ripetizioni)",
                len(pat),
                reps,
                len(m.group(0)),
            )
    return None


def detect_degenerate_repetition(text: str, threshold: int = 15) -> str | None:
    """
    Rileva anomalie di degenerazione ciclica o loop di attrattore nei modelli LLM:
    - Ripetizione consecutiva dello stesso carattere >= threshold (es. 'GGGGGGGGGGGGGGGG').
    - Ripetizione ciclica di caratteri tabulari >= 12 volte.
    - Sequenze ripetitive cicliche di 2-25 caratteri ripetute >= 6 volte (esclusi array di alias JSON validi).
    Restituisce una stringa descrittiva se degenerato, altrimenti None.
    """
    scan = scan_degenerate_repetition(text, threshold)
    return scan.message if scan is not None else None


# ---------------------------------------------------------------------------------------------------------
# Policy di accettazione per la degenerazione (VERSIONATA: cambia l'esito scientifico, quindi l'identità della run).
#   degeneration_v1_raw        storica: regex di ripetizione sul testo GREZZO prima di json.loads (default invariato).
#   degeneration_v2_structural nuova: se la risposta è JSON STRETTO (json.loads senza riparazioni) ed è un oggetto,
#                              la ripetizione è giudicata sulla struttura (chiavi/ID/enum) con la regola storica e
#                              sui soli campi di testo libero con una soglia di estensione (vedi sotto).
#                              Se il JSON NON è strettamente valido si applica la regola storica sul testo grezzo.
# ---------------------------------------------------------------------------------------------------------
DEGENERATION_POLICY_HISTORICAL = "degeneration_v1_raw"
DEGENERATION_POLICY_STRUCTURAL = "degeneration_v2_structural"
ALLOWED_DEGENERATION_POLICIES = (DEGENERATION_POLICY_HISTORICAL, DEGENERATION_POLICY_STRUCTURAL)

# Campi di testo libero (descrittivi, non portano identificativi né struttura).
FREE_TEXT_KEYS = frozenset({"label", "short_description", "rationale"})
# In un campo di testo libero una ripetizione è innocua se copre meno di questi caratteri consecutivi.
# Un vero attrattore consuma il budget di token (centinaia di caratteri) e di norma lascia la stringa non chiusa
# (JSON non valido → regola storica); 64 è ben sotto qualunque ciclo reale e sopra le risate/punteggiatura umane.
FREE_TEXT_MAX_BENIGN_REPETITION_SPAN = 64


def _blank_free_text(node: Any) -> Any:
    """Copia della struttura con i valori stringa dei campi di testo libero sostituiti da '' (chiavi e ID restano)."""
    if isinstance(node, dict):
        return {k: ("" if (k in FREE_TEXT_KEYS and isinstance(v, str)) else _blank_free_text(v)) for k, v in node.items()}
    if isinstance(node, list):
        return [_blank_free_text(v) for v in node]
    return node


def _collect_free_text(node: Any, out: list[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in FREE_TEXT_KEYS and isinstance(v, str):
                out.append(v)
            else:
                _collect_free_text(v, out)
    elif isinstance(node, list):
        for v in node:
            _collect_free_text(v, out)


def _max_repetition_span(text: str) -> tuple[str, int, int | None, int | None] | None:
    """
    Estensione MASSIMA (in caratteri) tra TUTTE le ripetizioni dello stesso tipo di quelle storiche.
    Restituisce (tipo_innesco, estensione, lunghezza_pattern, ripetizioni) oppure None. Nessun testo conservato.
    """
    # Le tabulazioni ripetute non sono mai testo umano: hanno la precedenza assoluta (come nella regola storica).
    tab = re.search(r"(\t)\1{11,}", text)
    if tab:
        return (DEGENERATION_TRIGGER_TAB_RUN, len(tab.group(0)), 1, len(tab.group(0)))
    best: tuple[str, int, int | None, int | None] | None = None
    for m in re.finditer(r"([^\s])\1{14,}", text):
        span = len(m.group(0))
        if best is None or span > best[1]:
            best = (DEGENERATION_TRIGGER_CHAR_RUN, span, 1, span)
    for m in re.finditer(r"(.{2,25})\1{5,}", text):
        pat = m.group(1)
        if pat.strip():
            span = len(m.group(0))
            if best is None or span > best[1]:
                best = (DEGENERATION_TRIGGER_PATTERN_LOOP, span, len(pat), span // len(pat))
    return best


def _raise_degenerate(
    message: str,
    trigger_kind: str,
    pattern_length: int | None,
    repetition_count: int | None,
    run_length: int | None,
    response_length: int,
) -> None:
    exc = AiStructuredOutputError(f"Risposta del modello scartata per degenerazione ciclica: {message}")
    # Metadati numerici per la diagnostica (nessun testo). Il messaggio resta quello storico usato dai retry.
    exc.degeneration = {  # type: ignore[attr-defined]
        "trigger_kind": trigger_kind,
        "pattern_length": pattern_length,
        "repetition_count": repetition_count,
        "run_length": run_length,
        "response_length": response_length,
    }
    raise exc


def _strict_json_object(raw_text: str) -> dict[str, Any] | None:
    """Oggetto JSON ottenuto con json.loads STRETTO (solo rimozione del recinto markdown); None se non valido/non oggetto."""
    clean_text = raw_text.strip()
    if clean_text.startswith("```"):
        lines = clean_text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        clean_text = "\n".join(lines).strip()
    try:
        data = json.loads(clean_text)
    except (json.JSONDecodeError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def _enforce_structural_degeneration_policy(data: dict[str, Any], response_length: int) -> None:
    """
    1) STRUTTURA (chiavi, evidence_ids, enum, campi sconosciuti): regola storica invariata sulla proiezione compatta.
    2) TESTO LIBERO (label/short_description/rationale, già decodificato): rifiuto se una ripetizione copre
       >= FREE_TEXT_MAX_BENIGN_REPETITION_SPAN caratteri consecutivi, o se contiene tabulazioni ripetute.
    Solleva la stessa eccezione storica (stesso prefisso di messaggio, stessa categoria) quando rifiuta.
    """
    projection = json.dumps(_blank_free_text(data), ensure_ascii=False)
    scan = scan_degenerate_repetition(projection)
    if scan is not None:
        _raise_degenerate(scan.message, scan.trigger_kind, scan.pattern_length, scan.repetition_count, scan.run_length, response_length)
    texts: list[str] = []
    _collect_free_text(data, texts)
    for value in texts:
        found = _max_repetition_span(value)
        if found is None:
            continue
        kind, span, plen, reps = found
        if kind == DEGENERATION_TRIGGER_TAB_RUN or span >= FREE_TEXT_MAX_BENIGN_REPETITION_SPAN:
            msg = (
                f"Ripetizione ciclica di caratteri tabulari ({span} volte consecutive)"
                if kind == DEGENERATION_TRIGGER_TAB_RUN
                else f"Ripetizione ciclica anomala in campo di testo libero ({span} caratteri consecutivi)"
            )
            _raise_degenerate(msg, kind, plen, reps, span, response_length)


def parse_and_validate_json(
    raw_text: str,
    degeneration_policy: str = DEGENERATION_POLICY_HISTORICAL,
) -> dict[str, Any]:
    """
    Esegue il parsing JSON del testo grezzo. Cattura esclusivamente json.JSONDecodeError.
    Errori di tipo o bug programmatici propagano senza mascheramento.
    Rileva e rigetta output ciclici degenerati secondo `degeneration_policy` (default: regola storica, invariata).
    """
    if degeneration_policy not in ALLOWED_DEGENERATION_POLICIES:
        raise ValueError(f"degeneration_policy non riconosciuta: {degeneration_policy!r}")
    if degeneration_policy == DEGENERATION_POLICY_STRUCTURAL:
        strict = _strict_json_object(raw_text)
        if strict is not None:
            _enforce_structural_degeneration_policy(strict, len(raw_text))
            return strict
    scan = scan_degenerate_repetition(raw_text)
    if scan is not None:
        degenerate_exc = AiStructuredOutputError(
            f"Risposta del modello scartata per degenerazione ciclica: {scan.message}"
        )
        # Metadati numerici per la diagnostica (nessun testo). Il messaggio resta IDENTICO: è quello usato dai retry.
        degenerate_exc.degeneration = {  # type: ignore[attr-defined]
            "trigger_kind": scan.trigger_kind,
            "pattern_length": scan.pattern_length,
            "repetition_count": scan.repetition_count,
            "run_length": scan.run_length,
            "response_length": len(raw_text),
        }
        raise degenerate_exc

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
        # Fallback di tolleranza sintattica per LLM locali (es. virgole finali, apici ridondanti o spazi nelle chiavi)
        try:
            repaired = re.sub(r'\"+([a-zA-Z0-9_]+)\"+(\s*:)', r'"\1"\2', clean_text)
            repaired = re.sub(r'\"[\t\s]+([a-zA-Z0-9_]+)[\t\s]*\"(\s*:)', r'"\1"\2', repaired)
            repaired = re.sub(r'\"+(\s*,)', r'"\1', repaired)
            repaired = re.sub(r'\"+(\s*\n)', r'"\1', repaired)
            repaired = re.sub(r',\s*([\]}])', r'\1', repaired)
            data = json.loads(repaired)
        except json.JSONDecodeError:
            parse_exc = AiStructuredOutputError(f"Risposta del modello non conforme a JSON valido: {e}")
            # Telemetria numerica additiva (nessun testo, nessuna finestra attorno alla posizione); messaggio IDENTICO.
            parse_exc.json_failure = describe_json_failure(clean_text, e)  # type: ignore[attr-defined]
            raise parse_exc from e

    if not isinstance(data, dict):
        shape_exc = AiStructuredOutputError(f"L'output deve essere un oggetto JSON, ricevuto {type(data).__name__}")
        shape_exc.json_failure = describe_json_failure(clean_text, None, not_an_object=True)  # type: ignore[attr-defined]
        raise shape_exc
    return data


# Forme (enum chiuso) di una citazione rifiutata: SOLO etichette, mai il valore citato.
CITATION_SHAPE_ALIAS_NOT_IN_BLOCK = "ALIAS_FORMAT_NOT_IN_BLOCK"   # es. forma 'E###' ma non tra gli alias ammessi del blocco
CITATION_SHAPE_NUMERIC_ONLY = "NUMERIC_ONLY"                      # solo cifre
CITATION_SHAPE_FORENSIC_ID_NOT_IN_BLOCK = "FORENSIC_ID_NOT_IN_BLOCK"  # identificativo forense ('::' o 'unified:') non del blocco
CITATION_SHAPE_OTHER = "OTHER"
ALLOWED_CITATION_SHAPES = (
    CITATION_SHAPE_ALIAS_NOT_IN_BLOCK,
    CITATION_SHAPE_NUMERIC_ONLY,
    CITATION_SHAPE_FORENSIC_ID_NOT_IN_BLOCK,
    CITATION_SHAPE_OTHER,
)


def classify_citation_shape(value: Any) -> str:
    """Forma di una citazione rifiutata (enum chiuso). Non conserva né restituisce il testo citato."""
    v = str(value).strip()
    if re.fullmatch(r"[A-Za-z]\d{3,4}", v):
        return CITATION_SHAPE_ALIAS_NOT_IN_BLOCK
    if re.fullmatch(r"\d+", v):
        return CITATION_SHAPE_NUMERIC_ONLY
    if "::" in v or v.startswith("unified:"):
        return CITATION_SHAPE_FORENSIC_ID_NOT_IN_BLOCK
    return CITATION_SHAPE_OTHER


def citation_failure_summary(cited_count: int, invalid: Sequence[Any]) -> dict[str, Any]:
    """Conteggi (solo interi/etichette) di una risposta con citazioni rifiutate."""
    shapes: dict[str, int] = {}
    for item in invalid:
        key = classify_citation_shape(item)
        shapes[key] = shapes.get(key, 0) + 1
    return {
        "citations_total_count": int(cited_count),
        "citations_invalid_count": int(len(invalid)),
        "invalid_citation_shapes": shapes,
    }


def validate_topic_detection_payload(
    raw_text: str,
    valid_evidence_ids: set[str],
    alias_map: Mapping[str, str] | None = None,
    degeneration_policy: str = DEGENERATION_POLICY_HISTORICAL,
) -> tuple[TopicDecision, tuple[str, ...], str]:
    """
    Valida la risposta per Topic Detection.
    Verifica chiavi esatte, tipi rigidi, enum e che le citazioni appartengano a valid_evidence_ids
    (o alias_map se in modalità operativa con alias).
    """
    data = parse_and_validate_json(raw_text, degeneration_policy)

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

    # Rifiuto duplicati (in modalita scientifica alias_map is None; in modalita operativa deduplica in modo deterministico)
    if alias_map is None and len(raw_eids) != len(set(raw_eids)):
        raise AiStructuredOutputError(f"evidence_ids contiene elementi duplicati: {raw_eids}")

    # Validazione tipo 'rationale'
    raw_rationale = data["rationale"]
    if not isinstance(raw_rationale, str):
        raise AiStructuredOutputError(f"'rationale' deve essere stringa, ricevuto {type(raw_rationale).__name__}")
    rationale = raw_rationale.strip()
    if not rationale:
        raise AiStructuredOutputError("'rationale' non può essere una stringa vuota")

    # Verifica citazioni ed eventuale rimappatura alias deterministica
    if alias_map is not None:
        remapped_eids_list: list[str] = []
        hallucinated: list[str] = []
        for eid_val in raw_eids:
            if eid_val in alias_map:
                real_id = alias_map[eid_val]
                if real_id in valid_evidence_ids:
                    remapped_eids_list.append(real_id)
                else:
                    hallucinated.append(eid_val)
            elif eid_val in valid_evidence_ids:
                remapped_eids_list.append(eid_val)
            else:
                hallucinated.append(eid_val)

        if hallucinated:
            raise AiInvalidEvidenceCitationError(
                f"Il modello ha citato evidence_id o alias inesistenti o non appartenenti al blocco: {sorted(hallucinated)}",
                invalid_evidence_ids=tuple(sorted(hallucinated)),
            )
        final_eids = tuple(dict.fromkeys(remapped_eids_list))
    else:
        hallucinated = set(raw_eids) - valid_evidence_ids
        if hallucinated:
            raise AiInvalidEvidenceCitationError(
                f"Il modello ha citato evidence_id inesistenti o allucinati: {sorted(hallucinated)}",
                invalid_evidence_ids=tuple(sorted(hallucinated)),
            )
        final_eids = tuple(raw_eids)

    # Invarianti per decisione
    if decision == TopicDecision.PRESENT and len(final_eids) == 0:
        raise AiStructuredOutputError(
            "Il modello ha restituito decision=PRESENT ma la lista evidence_ids è vuota"
        )
    if decision == TopicDecision.ABSENT and len(final_eids) != 0:
        raise AiStructuredOutputError(
            f"Il modello ha restituito decision=ABSENT ma ha allegato evidence_ids: {final_eids}. Per ABSENT deve essere vuota."
        )

    return decision, final_eids, rationale


def _discovery_validation_error(message: str, kind: str, *, topics_returned: int | None = None) -> AiStructuredOutputError:
    """
    Errore di validazione del payload Topic Discovery con la CAUSA in enum chiuso (`ai.json_failure.VALIDATION_KINDS`).
    Il messaggio (e quindi la classificazione storica) resta identico; `validation_kind` e `topics_returned` sono solo
    telemetria numerica/enum e non influenzano alcuna decisione di accettazione.
    """
    exc = AiStructuredOutputError(message)
    setattr(exc, "validation_kind", kind)
    if topics_returned is not None:
        setattr(exc, "topics_returned", int(topics_returned))
    return exc


def validate_topic_discovery_payload(
    raw_text: str,
    valid_evidence_ids: set[str],
    max_topics: int = 10,
    alias_map: Mapping[str, str] | None = None,
    degeneration_policy: str = DEGENERATION_POLICY_HISTORICAL,
) -> tuple[DiscoveredTopic, ...]:
    """
    Valida la risposta per Open Topic Discovery.
    Rifiuta output che superano max_topics senza alcun troncamento silente.
    Se alias_map è fornita (es. {'E001': 'real_id_1', ...}), le citazioni del modello
    possono corrispondere agli alias presentati nel blocco e vengono riconvertite
    deterministicamente negli identificativi forensi reali.
    Citazioni inesistenti, allucinate o appartenenti ad altri blocchi vengono rigorosamente rifiutate.
    """
    data = parse_and_validate_json(raw_text, degeneration_policy)

    expected_keys = {"topics"}
    actual_keys = set(data.keys())
    if actual_keys != expected_keys:
        raise _discovery_validation_error(f"Schema Topic Discovery violato: attese chiavi {expected_keys}, ricevute {actual_keys}", "TOP_KEYS_MISMATCH")

    raw_topics = data["topics"]
    if not isinstance(raw_topics, list):
        raise _discovery_validation_error(f"'topics' deve essere una lista JSON, ricevuto {type(raw_topics).__name__}", "TOPICS_NOT_LIST")

    # D5: Divieto di silent truncation: se eccede max_topics, solleva errore!
    if len(raw_topics) > max_topics:
        raise _discovery_validation_error(
            f"Il modello ha restituito {len(raw_topics)} argomenti, superando il limite consentito di {max_topics}. "
            f"Nessun troncamento silente consentito.",
            "TOPIC_COUNT_EXCEEDS_LIMIT",
            topics_returned=len(raw_topics),
        )

    discovered: list[DiscoveredTopic] = []
    expected_topic_keys = {"label", "short_description", "evidence_ids"}

    for idx, item in enumerate(raw_topics):
        if not isinstance(item, dict):
            raise _discovery_validation_error(f"Elemento topic[{idx}] deve essere un oggetto JSON", "TOPIC_NOT_OBJECT")

        item_keys = set(item.keys())
        if item_keys != expected_topic_keys:
            # In modalita operativa ignora record dummy completamente privi di valore a fine array
            if alias_map is not None and not any(item.values()):
                continue
            raise _discovery_validation_error(f"topic[{idx}] presenta chiavi non conformi: {item_keys} != {expected_topic_keys}", "TOPIC_KEYS_MISMATCH")

        raw_label = item.get("label")
        raw_desc = item.get("short_description")
        eids_raw = item.get("evidence_ids")

        # In modalita operativa ignora record dummy vuoti prodotti a volte dai modelli locali
        if alias_map is not None:
            if (not isinstance(raw_label, str) or not raw_label.strip()) and (not eids_raw or eids_raw == [""]):
                continue

        if not isinstance(raw_label, str) or not raw_label.strip():
            raise _discovery_validation_error(f"topic[{idx}].label deve essere una stringa non vuota", "LABEL_INVALID")
        if not isinstance(raw_desc, str) or not raw_desc.strip():
            raise _discovery_validation_error(f"topic[{idx}].short_description deve essere una stringa non vuota", "DESCRIPTION_INVALID")

        if not isinstance(eids_raw, list):
            raise _discovery_validation_error(f"topic[{idx}] ('{raw_label}') deve contenere una lista per evidence_ids", "EVIDENCE_IDS_NOT_LIST")

        had_duplicate_loop = False
        if alias_map is not None:
            # Pulizia e deduplicazione deterministica alias in modalita operativa
            raw_non_empty = [e.strip() for e in eids_raw if isinstance(e, str) and e.strip()]
            clean_eids = list(dict.fromkeys(raw_non_empty))
            had_duplicate_loop = len(raw_non_empty) > len(clean_eids)
            if had_duplicate_loop and any("::" in e for e in raw_non_empty):
                raise _discovery_validation_error(f"topic[{idx}] contiene evidence_ids duplicati: {eids_raw}", "EVIDENCE_IDS_DUPLICATED")
            if len(clean_eids) == 0:
                if len(raw_topics) == 1:
                    raise _discovery_validation_error(f"topic[{idx}] ('{item['label']}') deve contenere almeno un evidence_id", "EVIDENCE_IDS_EMPTY")
                # Argomento candidato terminale privo di evidenze: viene scartato se vi sono altri topic validi
                continue
            eids_raw = clean_eids
        else:
            if len(eids_raw) == 0:
                raise _discovery_validation_error(f"topic[{idx}] ('{item['label']}') deve contenere almeno un evidence_id", "EVIDENCE_IDS_EMPTY")
            for e_idx, eid_val in enumerate(eids_raw):
                if not isinstance(eid_val, str) or not eid_val.strip():
                    raise _discovery_validation_error(f"topic[{idx}].evidence_ids[{e_idx}] non è una stringa valida", "EVIDENCE_ID_NOT_STRING")
            if len(eids_raw) != len(set(eids_raw)):
                raise _discovery_validation_error(f"topic[{idx}] contiene evidence_ids duplicati: {eids_raw}", "EVIDENCE_IDS_DUPLICATED")


        if alias_map is not None:
            # Rigoroso: l'identificativo citato deve essere un alias valido del blocco
            # oppure un evidence_id forense valido appartenente al blocco corrente.
            remapped_eids_list: list[str] = []
            hallucinated: list[str] = []
            for e_pos, eid_val in enumerate(eids_raw):
                eid_clean = eid_val.strip() if isinstance(eid_val, str) else str(eid_val)
                candidate_alias = eid_clean
                if candidate_alias not in alias_map and candidate_alias not in valid_evidence_ids:
                    # Rimuove prefissi spuri frequenti dei modelli locali (es. 'value: E001', 'id: E001')
                    norm_cand = re.sub(r"^(?:value|id|evidence_id|alias|val)\s*[:=]\s*", "", candidate_alias, flags=re.IGNORECASE).strip()
                    if norm_cand in alias_map or norm_cand in valid_evidence_ids:
                        candidate_alias = norm_cand
                    else:
                        m_alias = re.search(r"\b([A-Za-z]\d{3,4})\b", candidate_alias)
                        if m_alias and m_alias.group(1) in alias_map:
                            candidate_alias = m_alias.group(1)
                        else:
                            m_prefix = re.match(r"^([A-Za-z]\d{3,4})(?!\d)", candidate_alias)
                            if m_prefix and m_prefix.group(1) in alias_map:
                                candidate_alias = m_prefix.group(1)

                if candidate_alias in alias_map:
                    real_id = alias_map[candidate_alias]
                    if real_id in valid_evidence_ids:
                        remapped_eids_list.append(real_id)
                    else:
                        hallucinated.append(eid_val)
                elif candidate_alias in valid_evidence_ids:
                    remapped_eids_list.append(candidate_alias)
                else:
                    is_trailing_breakout = (
                        had_duplicate_loop
                        and len(remapped_eids_list) > 0
                        and e_pos >= len(eids_raw) - 2
                        and not re.search(r"\b[A-Za-z]\d{3,4}\b", candidate_alias)
                        and (
                            not re.search(r"\d", candidate_alias)
                            or not re.match(r"^[A-Za-z0-9_:-]+$", candidate_alias)
                            or (len(candidate_alias) > 20 and " " in candidate_alias and not candidate_alias.startswith("unified:"))
                        )
                    )
                    if is_trailing_breakout:
                        continue
                    hallucinated.append(eid_val)

            if hallucinated:
                _inv_exc = AiInvalidEvidenceCitationError(
                    f"topic[{idx}] ('{item['label']}') cita evidence_id o alias inesistenti o non appartenenti al blocco: {sorted(hallucinated)}",
                    invalid_evidence_ids=tuple(sorted(hallucinated)),
                )
                _inv_exc.citation_summary = citation_failure_summary(len(eids_raw), hallucinated)  # type: ignore[attr-defined]
                raise _inv_exc
            final_eids = tuple(dict.fromkeys(remapped_eids_list))
        else:
            hallucinated = set(eids_raw) - valid_evidence_ids
            if hallucinated:
                _inv_exc = AiInvalidEvidenceCitationError(
                    f"topic[{idx}] ('{item['label']}') cita evidence_id inesistenti: {sorted(hallucinated)}",
                    invalid_evidence_ids=tuple(sorted(hallucinated)),
                )
                _inv_exc.citation_summary = citation_failure_summary(len(eids_raw), list(hallucinated))  # type: ignore[attr-defined]
                raise _inv_exc
            final_eids = tuple(eids_raw)

        clean_lbl = item["label"].strip()
        clean_dsc = item["short_description"].strip()
        if alias_map is not None:
            clean_lbl = re.sub(r"^(?:label|topic|titolo|argomento|in)\s*[:=]\s*", "", clean_lbl, flags=re.IGNORECASE).strip().strip("'\"")
            clean_dsc = re.sub(r"^(?:short_description|description|descrizione|in)\s*[:=]\s*", "", clean_dsc, flags=re.IGNORECASE).strip()
            if not clean_lbl or not clean_dsc:
                continue

        discovered.append(
            DiscoveredTopic(
                label=clean_lbl,
                short_description=clean_dsc,
                evidence_ids=final_eids,
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
