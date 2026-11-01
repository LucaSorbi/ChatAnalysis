"""
tests/unit/test_discovery_acceptance_policy.py
----------------------------------------------
Topic Discovery su chat brevi: policy di accettazione della degenerazione (v2 strutturale, versionata),
diagnostica sanificata delle citazioni invalide, identità della run e isolamento dei checkpoint.

SOLO dati sintetici, ZIP WhatsApp temporanei e client LLM finto: esito massimo MOCK.
NON dimostrano nulla su Qwen/LM Studio/Windows né sulle chat reali dell'utente. Il "prima" è ottenuto forzando
la policy storica (stessa funzione, parametro diverso), non confrontando con dati reali.
"""
from __future__ import annotations

import io
import json
import re
import sqlite3
import zipfile
from typing import Any

import pytest

pytest.importorskip("PIL", reason="Pillow necessario per generare JPEG sintetici")
from PIL import Image  # noqa: E402

import analysis.service as service_mod  # noqa: E402
import ai.topics as topics_mod  # noqa: E402
from ai.backend import AiInvalidEvidenceCitationError, AiStructuredOutputError, LlmCompletionResponse  # noqa: E402
from ai.structured import (  # noqa: E402
    ALLOWED_CITATION_SHAPES,
    DEGENERATION_POLICY_HISTORICAL,
    DEGENERATION_POLICY_STRUCTURAL,
    FREE_TEXT_MAX_BENIGN_REPETITION_SPAN,
    classify_citation_shape,
    parse_and_validate_json,
    validate_topic_detection_payload,
    validate_topic_discovery_payload,
)
from analysis.checkpoint import AnalysisCheckpointStore, compute_document_fingerprint  # noqa: E402
from analysis.export import build_topic_discovery_export_report, serialize_report_to_json  # noqa: E402
from multimodal.asset_access import MediaAssetRegistry  # noqa: E402
from multimodal.models import AudioTranscriptSegment  # noqa: E402
from multimodal.ocr import FakeImageTextExtractor  # noqa: E402
from multimodal.transcriber import FakeAudioTranscriber  # noqa: E402
from tests.unit.test_detection_microchunk_checkpoint_resume import (  # noqa: E402,F401  (fixture autouse inclusa)
    MODEL,
    FakeClient,
    _doc,
    _no_sleep,
    _service,
)
from ui.application import execute_local_media_processing  # noqa: E402
from ui.ingestion import ingest_file_payload  # noqa: E402
from ui.models import SourceFormat  # noqa: E402

SECRET = "ZXQSEGRETO"          # marcatore: testo di chat / STT / OCR che NON deve mai uscire
INVENTED = "ZXQALIASINVENTATO"  # marcatore: valore citato dal modello che NON deve mai uscire
V1, V2 = DEGENERATION_POLICY_HISTORICAL, DEGENERATION_POLICY_STRUCTURAL
LAUGH = "ahahahahahahahahah"    # 18 caratteri: innocuo, ma rifiutato dalla regola storica


# ============================================================================
# 1. ai/structured.py — la policy v2 accetta SOLO ripetizioni innocue in testo libero valido
# ============================================================================

def _disc(label: str = "Tema", desc: str = "Descrizione", eids: tuple[str, ...] = ("E001",)) -> str:
    return json.dumps({"topics": [{"label": label, "short_description": desc, "evidence_ids": list(eids)}]})


def _det(rationale: str, decision: str = "ABSENT") -> str:
    return json.dumps({"decision": decision, "evidence_ids": [], "rationale": rationale})


def _rejected_as_degenerate(raw: str, policy: str) -> bool:
    try:
        parse_and_validate_json(raw, policy)
    except AiStructuredOutputError as exc:
        return "degenerazione ciclica" in str(exc)
    return False


@pytest.mark.parametrize("text", [
    LAUGH, "ahahahahahahahahahah che ridere", "Bellissimo!!!!!!!!!!!!!!!!!!", "ok.................", "ha ha ha ha ha ha ha ha",
    "Riunione\\n\\n\\n\\n\\n\\n\\n\\n finita",
])
def test_innocuous_repetition_in_free_text_was_rejected_and_is_now_accepted(text):
    raw = _disc(label=text)
    assert _rejected_as_degenerate(raw, V1), "il falso positivo storico deve restare riproducibile con la policy v1"
    assert not _rejected_as_degenerate(raw, V2)
    assert validate_topic_discovery_payload(raw, {"id_1"}, alias_map={"E001": "id_1"}, degeneration_policy=V2)[0].label == text


@pytest.mark.parametrize("text", [
    "la " * 80,                      # ciclo reale ben formato (240 caratteri)
    "x" * FREE_TEXT_MAX_BENIGN_REPETITION_SPAN,
    "ab" * 40,
    "Il tema è " + "tutto uguale " * 20,
    "a\t" * 12 + "\t" * 12,         # tabulazioni ripetute
])
def test_real_loops_in_free_text_stay_rejected_under_v2(text):
    assert _rejected_as_degenerate(_disc(desc=text), V2)
    assert _rejected_as_degenerate(_det(text), V2)


def test_boundary_of_free_text_threshold_is_exact():
    below = "x" * (FREE_TEXT_MAX_BENIGN_REPETITION_SPAN - 1)
    assert not _rejected_as_degenerate(_det(below), V2)
    assert _rejected_as_degenerate(_det("x" * FREE_TEXT_MAX_BENIGN_REPETITION_SPAN), V2)


def test_second_longer_loop_after_a_short_one_is_not_masked():
    # La prima ripetizione è innocua, la seconda (più lunga) no: deve vincere la più estesa.
    text = "ahahahahahah e poi " + "la " * 60
    assert _rejected_as_degenerate(_det(text), V2)


@pytest.mark.parametrize("bad", [
    '{"topics": [{"label": "' + "ab" * 200,                       # stringa non chiusa (troncata)
    '{"topics": [{"label": "Tema", "short_description": "x"',      # risposta non terminata
    "non è JSON " + "x" * 40,                                      # JSON invalido con ripetizione
    "```json\n" + '{"topics": []' + "\n```",                       # recinto con JSON incompleto
    "[" + "1," * 50 + "]",                                        # non un oggetto
])
def test_invalid_or_truncated_json_follows_the_historical_rule_under_both_policies(bad):
    for pol in (V1, V2):
        try:
            parse_and_validate_json(bad, pol)
            raised = None
        except AiStructuredOutputError as exc:
            raised = str(exc)
        try:
            parse_and_validate_json(bad, V1)
            hist = None
        except AiStructuredOutputError as exc:
            hist = str(exc)
        assert raised is not None and raised == hist


def test_repetition_in_structure_is_still_rejected_even_when_json_is_valid():
    # Chiave sconosciuta con valore ripetuto: non è "testo libero" ammesso → regola storica sulla struttura.
    raw = json.dumps({"topics": [], "note": "z" * 30})
    assert _rejected_as_degenerate(raw, V1) and _rejected_as_degenerate(raw, V2)
    # ID citato con ripetizione: rifiutato come prima (o per degenerazione o per citazione inesistente, mai accettato)
    raw2 = _disc(eids=("E" + "0" * 40,))
    with pytest.raises((AiStructuredOutputError, AiInvalidEvidenceCitationError)):
        validate_topic_discovery_payload(raw2, {"id_1"}, alias_map={"E001": "id_1"}, degeneration_policy=V2)


def test_v2_never_relaxes_schema_alias_or_evidence_validation():
    ok_alias = {"E001": "id_1"}
    # chiavi extra / mancanti
    with pytest.raises(AiStructuredOutputError):
        validate_topic_discovery_payload(json.dumps({"topics": [], "extra": 1}), {"id_1"}, alias_map=ok_alias, degeneration_policy=V2)
    with pytest.raises(AiStructuredOutputError):
        validate_topic_discovery_payload(json.dumps({"topics": [{"label": LAUGH}]}), {"id_1"}, alias_map=ok_alias, degeneration_policy=V2)
    # alias inventato: rifiutato, mai corretto né completato
    with pytest.raises(AiInvalidEvidenceCitationError) as ei:
        validate_topic_discovery_payload(_disc(label=LAUGH, eids=("E777",)), {"id_1"}, alias_map=ok_alias, degeneration_policy=V2)
    assert ei.value.invalid_evidence_ids == ("E777",)
    # troppi topic
    many = json.dumps({"topics": [{"label": f"t{i}", "short_description": "d", "evidence_ids": ["E001"]} for i in range(4)]})
    with pytest.raises(AiStructuredOutputError):
        validate_topic_discovery_payload(many, {"id_1"}, max_topics=3, alias_map=ok_alias, degeneration_policy=V2)
    # topic senza evidenze (modalità senza alias): rifiutato
    with pytest.raises(AiStructuredOutputError):
        validate_topic_discovery_payload(_disc(eids=()), {"id_1"}, degeneration_policy=V2)


def test_default_policy_is_historical_so_detection_and_old_callers_are_unchanged():
    raw = _det(LAUGH)
    with pytest.raises(AiStructuredOutputError, match="degenerazione ciclica"):
        validate_topic_detection_payload(raw, set())
    with pytest.raises(AiStructuredOutputError, match="degenerazione ciclica"):
        validate_topic_discovery_payload(_disc(label=LAUGH), {"id_1"}, alias_map={"E001": "id_1"})
    with pytest.raises(ValueError):
        parse_and_validate_json("{}", "policy_inesistente")


def test_degeneration_diagnostics_keep_numeric_only_metadata_under_v2():
    with pytest.raises(AiStructuredOutputError) as ei:
        parse_and_validate_json(_disc(desc="la " * 80), V2)
    deg = ei.value.degeneration
    assert deg["trigger_kind"] in {"CHAR_RUN", "TAB_RUN", "PATTERN_LOOP"}
    assert all(isinstance(v, int) or v is None for k, v in deg.items() if k != "trigger_kind")


def test_token_and_finish_reason_missing_do_not_change_the_acceptance_decision():
    # La policy decide solo sul contenuto: finish_reason/token assenti non entrano nella validazione del payload.
    assert not _rejected_as_degenerate(_disc(label=LAUGH), V2)
    assert _rejected_as_degenerate(_disc(label="la " * 80), V2)


@pytest.mark.parametrize("value, shape", [
    ("E999", "ALIAS_FORMAT_NOT_IN_BLOCK"), ("e1234", "ALIAS_FORMAT_NOT_IN_BLOCK"), ("17", "NUMERIC_ONLY"),
    ("unified:x::ORIGINAL_TEXT", "FORENSIC_ID_NOT_IN_BLOCK"), (f"{INVENTED} testo libero", "OTHER"),
])
def test_citation_shapes_are_a_closed_enum_without_the_value(value, shape):
    assert classify_citation_shape(value) == shape and shape in ALLOWED_CITATION_SHAPES


# ============================================================================
# 2. Scenari MOCK sul servizio Discovery
# ============================================================================

VOICE = "PTT-20261101-WA0001.opus"
PHOTO = "IMG-20261101-WA0001.jpg"


def _chat(n: int, voice_at: int | None = None, photo_at: int | None = None) -> str:
    lines = []
    for i in range(n):
        ts = f"[01/11/2026, {10 + i // 50:02d}:{i % 50:02d}:00]"
        who = "Mario" if i % 2 == 0 else "Anna"
        if i == voice_at:
            lines.append(f"{ts} {who}: {VOICE} (file allegato)")
        elif i == photo_at:
            lines.append(f"{ts} {who}: {PHOTO} (file allegato)")
        else:
            lines.append(f"{ts} {who}: Messaggio sintetico numero {i} per la verifica delle evidenze")
    return "\n".join(lines) + "\n"


def _build(n: int, voice_at: int | None = None, photo_at: int | None = None, *, stt: Any = "ok", ocr: Any = "ok"):
    """Documento sintetico da uno ZIP WhatsApp sintetico; i motori STT/OCR sono finti (nessuna inferenza)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("_chat.txt", _chat(n, voice_at, photo_at))
        if voice_at is not None:
            zf.writestr(VOICE, b"OggS\x00\x02\x00\x00OpusHead")
        if photo_at is not None:
            img = io.BytesIO()
            Image.new("RGB", (64, 64), "white").save(img, "JPEG")
            zf.writestr(PHOTO, img.getvalue())
    reg = MediaAssetRegistry()
    res = ingest_file_payload(buf.getvalue(), original_filename="chat.zip", source_format=SourceFormat.WHATSAPP_EXPORT, media_registry=reg)
    doc = res.document_list[0]
    if voice_at is None and photo_at is None:
        return doc
    stt_backend = (
        FakeAudioTranscriber(canned_segments=(AudioTranscriptSegment(start_seconds=0.0, end_seconds=1.0, text=f"{SECRET} trascrizione sintetica"),))
        if stt == "ok" else FakeAudioTranscriber(simulate_failure=True)
    )
    ocr_backend = FakeImageTextExtractor(default_text=f"{SECRET} testo ocr sintetico") if ocr == "ok" else (
        FakeImageTextExtractor(simulate_no_text=True) if ocr == "empty" else FakeImageTextExtractor(simulate_failure=True)
    )
    enriched, _ = execute_local_media_processing(doc, registry=reg, stt_backend=stt_backend, ocr_backend=ocr_backend)
    return enriched


def _positions(user: str, doc: Any) -> set[int]:
    present = set(re.findall(r'"msg_id": "([^"]+)"', user))
    return {i for i, b in enumerate(doc.bundles) if b.message.message_id in present}


def _aliases(user: str) -> list[str]:
    return re.findall(r'"id": "(E\d{3,4})"', user)


def _ok(user: str, label: str = "Tema sintetico") -> LlmCompletionResponse:
    al = _aliases(user)
    return LlmCompletionResponse(
        content=json.dumps({"topics": [{"label": label, "short_description": "Descrizione sintetica", "evidence_ids": al[:1]}]}),
        model=MODEL, raw_response={"finish_reason": "stop"},
    )


def _invented(user: str) -> LlmCompletionResponse:
    return LlmCompletionResponse(
        content=json.dumps({"topics": [{"label": "Tema", "short_description": "Descrizione", "evidence_ids": [f"E9{len(_aliases(user)):02d}", INVENTED]}]}),
        model=MODEL, raw_response={"finish_reason": "stop"},
    )


def _loop(user: str) -> LlmCompletionResponse:  # ciclo reale, stringa non chiusa
    return LlmCompletionResponse(content='{"topics": [{"label": "' + "ab" * 200, model=MODEL, raw_response={"finish_reason": "stop"})


def _run(doc: Any, handler: Any, store: AnalysisCheckpointStore | None = None, **svc_kw: Any):
    client = FakeClient(handler)
    svc = _service(client, store, **svc_kw)
    events: list[Any] = []
    res = svc.run_topic_discovery(document=doc, client=client, model_name=MODEL, progress_callback=events.append)
    return res, client, events


def _cats(res: Any) -> list[tuple[str, str]]:
    return [(d["status"], d["diagnostic_category"]) for d in res.metadata["chunk_diagnostics"]]


def _handler_a(doc: Any, *, micro0: Any = _loop):
    """Microchunk 1 (messaggi 0-7): `micro0`; microchunk 2 (8-15): valido; microchunk 3 (16+): alias inventati."""
    def handler(user: str, n: int):
        pos = _positions(user, doc)
        if pos & set(range(0, 8)):
            return micro0(user)
        if pos & set(range(16, 40)):
            return _invented(user)
        return _ok(user)
    return handler


# ---- Scenario A: 19 messaggi (+ vocale trascritto = 20 sezioni), 3 microchunk ---------------------------------

@pytest.mark.parametrize("voice_at, sections", [(None, 19), (3, 20)])  # vocale nel microchunk 1 (fallito): 8/20 come nel JSON audio
def test_scenario_a_unresolvable_failures_stay_failed_and_partial(voice_at, sections):
    doc = _build(19, voice_at=voice_at)
    assert len(doc.bundles) == 19 and len(doc.all_evidence_sections) == sections
    res, client, _ = _run(doc, _handler_a(doc))
    m = res.metadata
    assert m["status"] == "PARTIAL" and m["completed_chunk_count"] == 1 and m["failed_chunk_count"] == 2
    assert _cats(res) == [("FAILED", "DEGENERATE_OUTPUT"), ("SUCCESS", "NONE"), ("FAILED", "INVALID_EVIDENCE_ID")]
    assert (m["total_evidence_count"], m["analyzed_evidence_count"], m["unanalyzed_evidence_count"]) == (sections, 8, sections - 8)
    # nessuna citazione inventata è diventata un topic; la copertura non è gonfiata
    assert all(set(t.evidence_ids) <= {s.evidence_id for s in doc.all_evidence_sections} for t in res.topics)
    assert all(INVENTED not in json.dumps(t.evidence_ids) for t in res.topics)
    assert m["acceptance_policy"] == V2


def test_scenario_a_invalid_citation_diagnostics_are_numeric_and_schema_valid_is_precise():
    doc = _build(19)
    res, _, _ = _run(doc, _handler_a(doc))
    inv = next(d for d in res.metadata["chunk_diagnostics"] if d["diagnostic_category"] == "INVALID_EVIDENCE_ID")
    # JSON e schema sono strutturalmente validi: a fallire è SOLO la verifica delle citazioni (prima: schema_valid=False)
    assert (inv["json_valid"], inv["schema_valid"], inv["evidence_valid"]) == (True, True, False)
    assert inv["attempts"] >= 2
    dm = inv["diagnostic_metrics"]
    dm = dict(dm)
    assert dm["citations_invalid_count"] == 2 and dm["citations_total_count"] == 2
    assert dict(dm["invalid_citation_shapes"]) == {"ALIAS_FORMAT_NOT_IN_BLOCK": 1, "OTHER": 1}
    assert dm["aliases_presented_count"] is not None and dm["aliases_accepted_count"] >= dm["aliases_presented_count"]
    assert INVENTED not in json.dumps(dm, default=str)


def test_scenario_a_false_positive_degeneration_before_after():
    doc = _build(19)

    def ok_with_laugh(user: str) -> LlmCompletionResponse:
        return _ok(user, label=LAUGH)

    # PRIMA (policy storica forzata): lo stesso risultato valido viene scartato come DEGENERATE_OUTPUT
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(topics_mod, "DEGENERATION_POLICY_STRUCTURAL", V1)
        mp.setattr(service_mod, "DEGENERATION_POLICY_STRUCTURAL", V1)
        before, _, _ = _run(doc, _handler_a(doc, micro0=ok_with_laugh))
    assert _cats(before)[0] == ("FAILED", "DEGENERATE_OUTPUT") and before.metadata["completed_chunk_count"] == 1
    # DOPO (policy v2): il microchunk 1 è validato; il 3 (alias inventati) RESTA fallito → PARTIAL, mai COMPLETE
    after, _, _ = _run(doc, _handler_a(doc, micro0=ok_with_laugh))
    assert _cats(after) == [("SUCCESS", "NONE"), ("SUCCESS", "NONE"), ("FAILED", "INVALID_EVIDENCE_ID")]
    assert after.metadata["status"] == "PARTIAL" and after.metadata["completed_chunk_count"] == 2
    assert after.metadata["analyzed_evidence_count"] == 16 and after.metadata["total_evidence_count"] == 19
    assert after.metadata["acceptance_policy"] == V2 and before.metadata["acceptance_policy"] == V1


def test_scenario_a_all_microchunks_valid_gives_complete_only_when_everything_validates():
    doc = _build(19)
    res, _, _ = _run(doc, lambda u, n: _ok(u, label=LAUGH))
    assert res.metadata["status"] == "COMPLETE" and res.metadata["completed_chunk_count"] == 3
    assert res.metadata["analyzed_evidence_count"] == res.metadata["total_evidence_count"] == 19


# ---- Scenario B: 36 messaggi (+ foto con OCR = 37 sezioni), 2 parent --------------------------------------------

def _handler_b(doc: Any):
    """Parent 1 (messaggi 0-17): alias inventati ovunque; parent 2: validi."""
    def handler(user: str, n: int):
        return _invented(user) if _positions(user, doc) & set(range(0, 18)) else _ok(user)
    return handler


def test_scenario_b_photo_chat_two_parents_first_invalid_second_success_with_saved_checkpoint(tmp_path):
    doc = _build(36, photo_at=30, ocr="ok")
    assert len(doc.bundles) == 36 and len(doc.all_evidence_sections) == 37
    kinds = [s.source_type.value for s in doc.all_evidence_sections]
    assert kinds.count("ORIGINAL_TEXT") == 36 and kinds.count("OCR_TEXT") == 1
    store = AnalysisCheckpointStore(checkpoint_dir=tmp_path / "chk")
    res, client, _ = _run(doc, _handler_b(doc), store, max_chunk_bundles=18)
    m = res.metadata
    assert m["status"] == "PARTIAL"
    assert m["completed_chunk_count"] < m["expected_chunk_count"] and m["failed_chunk_count"] >= 1
    cats = _cats(res)
    assert ("FAILED", "INVALID_EVIDENCE_ID") in cats and ("SUCCESS", "NONE") in cats
    assert "DEGENERATE_OUTPUT" not in {c for _, c in cats}, "nessun DEGENERATE_OUTPUT nello scenario foto"
    assert m["total_evidence_count"] == 37
    parent2_sections = sum(len(b.text_evidence_sections) for b in doc.bundles[18:])
    assert m["analyzed_evidence_count"] == parent2_sections and m["unanalyzed_evidence_count"] == 37 - parent2_sections
    # il parent fallito non cancella il successo del secondo e non diventa COMPLETE
    assert res.topics and all(set(t.evidence_ids) <= {s.evidence_id for s in doc.bundles[18].text_evidence_sections} | {s.evidence_id for b in doc.bundles[18:] for s in b.text_evidence_sections} for t in res.topics)
    # checkpoint: salvato per i blocchi validati, nessun salvataggio per quelli falliti
    failed = [d for d in m["chunk_diagnostics"] if d["status"] == "FAILED"]
    assert failed and all(d["checkpoint_saved"] is False for d in failed)


@pytest.mark.parametrize("ocr, total", [("ok", 37), ("empty", 36), ("fail", 36)])
def test_scenario_b_ocr_section_exists_only_with_success_and_non_empty_text(ocr, total):
    doc = _build(36, photo_at=30, ocr=ocr)
    assert len(doc.all_evidence_sections) == total
    assert sum(1 for s in doc.all_evidence_sections if s.source_type.value == "OCR_TEXT") == (1 if ocr == "ok" else 0)
    res, _, _ = _run(doc, _handler_b(doc), max_chunk_bundles=18)
    m = res.metadata
    assert m["total_evidence_count"] == total == m["analyzed_evidence_count"] + m["unanalyzed_evidence_count"]
    assert m["status"] == "PARTIAL"


def test_failed_stt_or_ocr_never_becomes_evidence_nor_a_false_complete():
    doc = _build(19, voice_at=12, stt="fail")
    assert not any(s.source_type.value == "STT_TRANSCRIPTION" for s in doc.all_evidence_sections)
    assert len(doc.all_evidence_sections) == 19
    res, _, _ = _run(doc, _handler_a(doc))
    assert res.metadata["status"] == "PARTIAL"


# ---- Alias e provenienza ----------------------------------------------------------------------------------------

def test_derived_sections_have_distinct_aliases_and_resolve_to_the_correct_evidence():
    doc = _build(19, voice_at=3, photo_at=10)  # audio + foto nello stesso documento
    secs = list(doc.all_evidence_sections)
    assert len(secs) == 21 and len({s.evidence_id for s in secs}) == 21
    kinds = {s.source_type.value for s in secs}
    assert {"ORIGINAL_TEXT", "STT_TRANSCRIPTION", "OCR_TEXT"} <= kinds
    for b in doc.bundles:  # nessun messaggio duplicato: le derivate stanno nello stesso bundle del messaggio d'origine
        assert all(s.message_id == b.message.message_id for s in b.text_evidence_sections)

    def handler(user: str, n: int):
        # cita l'alias della sezione DERIVATA (STT/OCR) se presente nel blocco, altrimenti l'ultima mostrata
        rows = [json.loads(line) for line in user.splitlines() if line.startswith('{"id"')]
        derived = [r["id"] for r in rows if SECRET in r.get("text", "")]
        cited = (derived or [rows[-1]["id"]])[0]
        return LlmCompletionResponse(
            content=json.dumps({"topics": [{"label": "T", "short_description": "D", "evidence_ids": [cited]}]}),
            model=MODEL, raw_response={"finish_reason": "stop"},
        )

    res, _, _ = _run(doc, handler)
    assert res.metadata["status"] == "COMPLETE"
    valid = {s.evidence_id for s in secs}
    assert all(set(t.evidence_ids) <= valid for t in res.topics)
    derived_ids = {s.evidence_id for s in secs if s.source_type.value in {"STT_TRANSCRIPTION", "OCR_TEXT"}}
    assert any(set(t.evidence_ids) & derived_ids for t in res.topics), "la citazione di una sezione derivata conserva la provenienza"


def _cite_in_block(doc: Any, cited: str) -> Any:
    """Esegue UN microchunk (messaggi 8-15: alias mostrati E001..E008) in cui il modello cita `cited`."""
    from ai.models import ConversationEvidenceDocument
    from ai.topics import TopicDiscoveryAnalyzer

    sub = ConversationEvidenceDocument(document_id="alias::sub", bundles=doc.bundles[8:16], source_name=doc.source_name)
    client = FakeClient(lambda u, n: LlmCompletionResponse(
        content=json.dumps({"topics": [{"label": "T", "short_description": "D", "evidence_ids": [cited]}]}),
        model=MODEL, raw_response={"finish_reason": "stop"}))
    analyzer = TopicDiscoveryAnalyzer(client=client, default_model_id=MODEL, operational_mode=True)
    return sub, analyzer.discover_topics(document=sub, model_id=MODEL, operational_mode=True, max_topics=3, max_tokens=300, context_length=8192)


def test_whatsapp_chats_accept_exactly_the_shown_aliases_and_nothing_else():
    """Per i documenti importati da WhatsApp source_record_id è 'msg_N' (non numerico): lo spazio 'E<record>' è vuoto."""
    doc = _build(19)
    sub, shown = _cite_in_block(doc, "E003")
    assert shown.topics[0].evidence_ids == (sub.all_evidence_sections[2].evidence_id,)
    for not_shown in ("E012", "E010", "E009", "E020", "unified:whatsapp_export:msg_10", "msg_10", "10"):
        with pytest.raises(AiInvalidEvidenceCitationError):
            _cite_in_block(doc, not_shown)


def test_non_whatsapp_numeric_record_ids_keep_the_lenient_alias_namespace_pinned():
    """
    OSSERVAZIONE (MOCK, comportamento ATTUALE, NON modificato): se source_record_id è numerico (sorgenti non
    WhatsApp) il validatore operativo accetta anche alias 'E<numero record>' che il prompt non offre; dove gli spazi
    si sovrappongono vince la posizione. Il test fissa il comportamento perché una restrizione (più rigorosa, quindi
    con effetto sui risultati) sia una decisione esplicita e versionata.
    """
    doc = _doc("alias", 20)
    sub, shown = _cite_in_block(doc, "E003")
    assert shown.topics[0].evidence_ids == (sub.all_evidence_sections[2].evidence_id,)
    _, unshown = _cite_in_block(doc, "E012")
    assert unshown.topics[0].evidence_ids == (doc.bundles[12].text_evidence_sections[0].evidence_id,)
    with pytest.raises(AiInvalidEvidenceCitationError):
        _cite_in_block(doc, "E020")


# ============================================================================
# 3. Identità della run, checkpoint e privacy
# ============================================================================

def _cfg_fp(store: AnalysisCheckpointStore, doc: Any) -> str:
    meta = store.get_latest_checkpoint_metadata_for_document(doc.document_id, compute_document_fingerprint(doc), analysis_type="discovery")
    return meta["config_fingerprint"] if meta else ""


def _chk_rows(path: Any) -> list[tuple]:
    out = []
    for db in sorted(path.glob("*.db")):
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
                out.append((db.name, name, con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]))
        finally:
            con.close()
    return out


def test_old_policy_checkpoints_are_never_reused_and_stay_untouched(tmp_path, monkeypatch):
    doc = _build(19)
    store = AnalysisCheckpointStore(checkpoint_dir=tmp_path / "chk")
    # 1) run "vecchia": identità con policy storica
    monkeypatch.setattr(service_mod, "DEGENERATION_POLICY_STRUCTURAL", V1)
    monkeypatch.setattr(topics_mod, "DEGENERATION_POLICY_STRUCTURAL", V1)
    old, client_old, _ = _run(doc, lambda u, n: _ok(u), store)
    assert old.metadata["status"] == "COMPLETE" and old.metadata["acceptance_policy"] == V1
    fp_old = _cfg_fp(store, doc)
    rows_before = _chk_rows(tmp_path / "chk")
    # 2) run con la policy corrente sullo STESSO store: identità diversa, nessun riuso di checkpoint
    monkeypatch.undo()
    new, client_new, _ = _run(doc, lambda u, n: _ok(u), store)
    assert new.metadata["acceptance_policy"] == V2
    assert _cfg_fp(store, doc) != fp_old
    assert new.metadata["recovered_chunk_count"] == 0 and len(client_new.calls) == len(client_old.calls) > 0
    assert fp_old != ""
    # 3) i dati della run vecchia sono ancora tutti lì (solo aggiunte)
    after = {(d, t): n for d, t, n in _chk_rows(tmp_path / "chk")}
    for d, t, n in rows_before:
        assert after[(d, t)] >= n


def test_same_policy_resumes_from_checkpoint(tmp_path):
    doc = _build(19)
    store = AnalysisCheckpointStore(checkpoint_dir=tmp_path / "chk")
    first, c1, _ = _run(doc, lambda u, n: _ok(u), store)
    second, c2, _ = _run(doc, lambda u, n: _ok(u), store)
    assert _cfg_fp(store, doc) is not None
    assert len(c2.calls) == 0 or second.metadata["recovered_chunk_count"] >= 1


def test_document_fingerprint_is_unchanged_by_the_policy_only_the_run_identity_changes(tmp_path, monkeypatch):
    doc = _build(19)
    fp_before = compute_document_fingerprint(doc)
    store_new = AnalysisCheckpointStore(checkpoint_dir=tmp_path / "new")
    _run(doc, lambda u, n: _ok(u), store_new)
    monkeypatch.setattr(service_mod, "DEGENERATION_POLICY_STRUCTURAL", V1)
    monkeypatch.setattr(topics_mod, "DEGENERATION_POLICY_STRUCTURAL", V1)
    store_old = AnalysisCheckpointStore(checkpoint_dir=tmp_path / "old")
    _run(doc, lambda u, n: _ok(u), store_old)
    assert compute_document_fingerprint(doc) == fp_before, "il fingerprint del documento non dipende dalla policy"
    assert _cfg_fp(store_new, doc) != _cfg_fp(store_old, doc) and _cfg_fp(store_old, doc) != ""


def _find_chunk_records(node: Any) -> list[dict]:
    """Tutti i dict con 'diagnostic_category' e 'diagnostic_metrics' nell'export (indipendente dal nome del contenitore)."""
    found: list[dict] = []
    if isinstance(node, dict):
        if "diagnostic_category" in node and "diagnostic_metrics" in node:
            found.append(node)
        for v in node.values():
            found.extend(_find_chunk_records(v))
    elif isinstance(node, list):
        for v in node:
            found.extend(_find_chunk_records(v))
    return found


def test_export_is_safe_additive_and_declares_the_policy():
    doc = _build(19, voice_at=12, photo_at=14)
    res, _, events = _run(doc, _handler_a(doc))
    report = build_topic_discovery_export_report(res, doc)
    text = serialize_report_to_json(report) + "\n" + "\n".join(f"{e.status_message} {dict(e.metadata)}" for e in events)
    text += "\n" + json.dumps([dict(d) for d in res.metadata["chunk_diagnostics"]], default=str)
    assert report["analysis_policy"] == {"acceptance_policy": V2}
    for forbidden in (SECRET, INVENTED, "Messaggio sintetico", VOICE, PHOTO, "ab" * 20):
        assert forbidden not in text, forbidden
    exported = json.loads(serialize_report_to_json(report))
    inv = [c for c in _find_chunk_records(exported) if c.get("diagnostic_category") == "INVALID_EVIDENCE_ID"]
    assert inv, "il record INVALID_EVIDENCE_ID deve comparire nell'export"
    for c in inv:
        assert set(c["diagnostic_metrics"]["invalid_citation_shapes"]) <= set(ALLOWED_CITATION_SHAPES)
        assert c["diagnostic_metrics"]["citations_invalid_count"] == 2


def test_logs_do_not_contain_model_cited_values(caplog):
    doc = _build(19)
    with caplog.at_level("DEBUG"):
        _run(doc, _handler_a(doc))
    assert INVENTED not in caplog.text and SECRET not in caplog.text


def test_historical_export_without_policy_field_stays_backward_compatible():
    doc = _build(19)
    res, _, _ = _run(doc, lambda u, n: _ok(u))
    meta = dict(res.metadata)
    meta.pop("acceptance_policy")
    from ai.models import TopicDiscoveryResult
    legacy = TopicDiscoveryResult(topics=res.topics, provenance_document_id=res.provenance_document_id, metadata=meta)
    assert "analysis_policy" not in build_topic_discovery_export_report(legacy, doc)
