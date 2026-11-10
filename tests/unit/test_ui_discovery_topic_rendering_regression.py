"""
Test di regressione completi (Attività F.1–F.8) per l'interazione Streamlit tra:
- Topic Detection normale (`origin_flow == 'MANUAL_DETECTION'`, inserita dall'operatore),
- Topic Discovery (`ui/presentation.py:render_topics`),
- Prova del Nove (`origin_flow == 'DISCOVERY_VERIFICATION'`, verifica indipendente da card Discovery),
- Invarianti fail-closed di provenienza e ancoraggio (`ui/verification.py`).

Nessun test contatta LM Studio reale né tocca `.checkpoints/` o `output/` del repository.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
import re
from typing import Any
import warnings

import pytest
from streamlit.testing.v1 import AppTest

from ai.backend import BaseLlmClient, LlmCompletionResponse
from ai.models import (
    ConversationEvidenceDocument,
    DiscoveredTopic,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
)
from analysis.export import validate_semantic_verification_report
from analysis.models import (
    AnalysisOrigin,
    ConsistencyResult,
    ConsistencyStatus,
    InsufficientContextHeadroomError,
    OperationalProfile,
)
from analysis.verification import compute_discovered_topic_uid
from multimodal.evidence import MessageEvidenceBundle
from multimodal.models import (
    ImageOcrResult,
    MediaKind,
    MediaResolutionStatus,
    OcrStatus,
    ResolvedMediaAsset,
)
from normalization.models import CanonicalMessageType
from ui import presentation, state
from ui.application import (
    build_topic_query,
    execute_manual_topic_detection,
    execute_topic_discovery,
)
from ui.demo import _create_synthetic_message
from ui.models import DatasetMode
from ui.verification import (
    build_manual_detection_correlation_note,
    evaluate_verification_preconditions,
    export_semantic_verification_json,
    find_discovery_run,
    has_unverifiable_verification_records,
    select_verification_results,
)

APP_PATH = str(Path(__file__).resolve().parents[2] / "app.py")
MODEL_ID = "qwen2.5-7b-instruct"


def _build_synthetic_photo_chat(
    *,
    doc_id: str = "doc::synthetic_photo_chat_36",
    message_count: int = 36,
    ocr_at_index: int = 5,
) -> ConversationEvidenceDocument:
    """
    Costruisce una conversazione sintetica (priva di dati reali) con `message_count` messaggi
    e un allegato fotografico sottoposto a OCR locale all'indice `ocr_at_index`.
    """
    source_name = "whatsapp_synthetic_txt"
    bundles: list[MessageEvidenceBundle] = []

    for idx in range(1, message_count + 1):
        msg_id = f"msg::{doc_id}::{idx}"
        rec_id = str(1000 + idx)
        if idx == ocr_at_index:
            msg = _create_synthetic_message(
                msg_id=msg_id,
                source_record_id=rec_id,
                text_content=f"Invio fotografia allegata del prospetto sintetico #{idx}.",
                source_name=source_name,
                message_type=CanonicalMessageType.IMAGE,
                media_reference="media/synthetic_photo_05.jpg",
            )
            asset = ResolvedMediaAsset(
                message_id=msg.message_id,
                source_name=source_name,
                source_record_id=rec_id,
                raw_reference="media/synthetic_photo_05.jpg",
                resolved_path="/synthetic/media/synthetic_photo_05.jpg",
                media_kind=MediaKind.IMAGE,
                status=MediaResolutionStatus.RESOLVED,
                file_size_bytes=65536,
                sha256="a" * 64,
                provenance_message=msg,
            )
            ocr = ImageOcrResult(
                message_id=msg.message_id,
                source_name=source_name,
                source_record_id=rec_id,
                status=OcrStatus.SUCCESS,
                full_text="MODULO SINTETICO NUMERO 405 - CONFERMA PRENOTAZIONE TURNO MATTUTINO",
                language_config="ita",
                engine="tesseract",
                provenance_asset=asset,
            )
            bundles.append(MessageEvidenceBundle(message=msg, image_ocr=ocr))
        else:
            msg = _create_synthetic_message(
                msg_id=msg_id,
                source_record_id=rec_id,
                text_content=f"Messaggio sintetico di coordinamento #{idx} sul tema operativo {((idx - 1) % 10) + 1}.",
                source_name=source_name,
            )
            bundles.append(MessageEvidenceBundle(message=msg))

    return ConversationEvidenceDocument(
        document_id=doc_id,
        bundles=tuple(bundles),
        chat_id="chat_synthetic_photo",
        source_name=source_name,
    )


class _DeterministicMockClient(BaseLlmClient):
    """Client deterministico offline per test unitari e AppTest (nessuna connessione a LM Studio)."""

    model_id = MODEL_ID

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.call_count = 0
        self.prompts: list[str] = []

    def is_available(self) -> bool:
        return True

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        model_id: str | None = None,
        temperature: float = 0.0,
        seed: int | None = 42,
        timeout_seconds: float = 30.0,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LlmCompletionResponse:
        self.call_count += 1
        self.prompts.append(json.dumps(messages, ensure_ascii=False))
        payload = self._responses.pop(0) if self._responses else {
            "decision": "ABSENT",
            "evidence_ids": [],
            "rationale": "Nessuna risposta residua in coda.",
        }
        return LlmCompletionResponse(
            content=json.dumps(payload, ensure_ascii=False),
            model=model_id or self.model_id,
            prompt_tokens=32,
            completion_tokens=24,
            total_tokens=56,
            latency_seconds=0.05,
        )


class _ForbidLlmCallsClient(BaseLlmClient):
    """Client sentinella che fallisce immediatamente se viene tentata una chiamata LLM non prevista."""

    model_id = MODEL_ID

    def __init__(self) -> None:
        self.call_count = 0

    def is_available(self) -> bool:
        return True

    def chat_completion(self, messages: list[dict[str, str]], **kwargs: Any) -> LlmCompletionResponse:
        self.call_count += 1
        raise AssertionError("Nessuna chiamata LLM doveva essere eseguita (atteso recupero da checkpoint).")


def _extract_first_prompt_alias(user_text: str) -> str:
    """Estrae il primo ID/alias di evidenza dal blocco JSON o JSONL del prompt di Detection/Discovery."""
    start_marker = "=== BEGIN FORENSIC EVIDENCE DATA"
    end_marker = "=== END FORENSIC EVIDENCE DATA ==="
    if start_marker in user_text and end_marker in user_text:
        body = user_text.split(start_marker, 1)[1].split("===\n", 1)[-1].split(end_marker, 1)[0].strip()
        if body.startswith("{") and '"evidence_sections"' in body:
            parsed = json.loads(body)
            secs = parsed.get("evidence_sections", ())
            if secs:
                return str(secs[0]["evidence_id"])
        for line in body.splitlines():
            s = line.strip()
            if s.startswith("{"):
                row = json.loads(s)
                return str(row.get("id") or row.get("evidence_id"))
    for line in user_text.splitlines():
        s = line.strip()
        if s.startswith('{"id":'):
            return str(json.loads(s)["id"])
    return "E001"


def _setup_fresh_apptest_for_document(doc: ConversationEvidenceDocument) -> AppTest:
    """Inizializza una sessione AppTest pulita con il solo documento caricato (come dopo riavvio Streamlit)."""
    at = AppTest.from_file(APP_PATH, default_timeout=25)
    at.run()
    at.session_state["dataset_loaded"] = True
    at.session_state["dataset_mode"] = DatasetMode.FILE
    at.session_state["conversation_document"] = doc
    at.session_state["available_documents"] = {doc.document_id: doc}
    at.session_state["selected_document_id"] = doc.document_id
    at.session_state["analysis_by_document"] = {
        doc.document_id: state.DocumentAnalysisState(document_id=doc.document_id)
    }
    return at


def _button_by_key(at: AppTest, key: str) -> Any:
    matches = [b for b in at.button if b.key == key]
    assert len(matches) == 1, f"Pulsante '{key}' non trovato o duplicato: {[b.key for b in at.button]}"
    return matches[0]


def _text_input_by_key(at: AppTest, key: str) -> Any:
    matches = [t for t in at.text_input if t.key == key]
    assert len(matches) == 1, f"TextInput '{key}' non trovato: {[t.key for t in at.text_input]}"
    return matches[0]


def _text_area_by_key(at: AppTest, key: str) -> Any:
    matches = [t for t in at.text_area if t.key == key]
    assert len(matches) == 1, f"TextArea '{key}' non trovato: {[t.key for t in at.text_area]}"
    return matches[0]


# ==============================================================================
# Test unitari puri su `ui/verification.py` (separazione Detection normale / Prova del Nove)
# ==============================================================================

def test_unit_correlation_note_never_rendered_for_normal_detection_without_prova_del_nove() -> None:
    """
    Attività C & Vincolo 1: una Topic Detection normale (`MANUAL_DETECTION`), anche con stessa label
    di un topic Discovery sullo stesso documento, NON deve MAI produrre messaggi di verifica ancorata
    quando non esiste una Prova del Nove validata sulla card (`topic_verifications == ()`).
    """
    doc_id = "doc::unit_manual_only"
    disc_run_id = "persisted_11223344"
    for decision in (TopicDecision.PRESENT, TopicDecision.ABSENT, TopicDecision.UNCERTAIN):
        manual_det = TopicDetectionResult(
            topic=build_topic_query(label="Pianificazione Consegne", description="Dettagli orari consegne"),
            decision=decision,
            evidence_ids=("msg::1::ORIGINAL_TEXT",) if decision == TopicDecision.PRESENT else (),
            rationale="Inferenza manuale operatore.",
            provenance_document_id=doc_id,
            metadata={
                "run_id": f"det_manual_{decision.value.lower()}",
                "origin_flow": "MANUAL_DETECTION",
                "query_mode": "LABEL_AND_DESCRIPTION",
            },
        )
        note = build_manual_detection_correlation_note(
            document_id=doc_id,
            topic_label="pianificazione consegne",
            detection_runs=(manual_det,),
            card_discovery_run_id=disc_run_id,
            topic_verifications=(),
        )
        assert note is None


def test_unit_correlation_note_requires_valid_prova_del_nove_and_same_document_manual_detection() -> None:
    """
    La nota di correlazione compare SOLO quando coesistono:
    1) una Prova del Nove (`DISCOVERY_VERIFICATION`) con `ConsistencyResult` e `TopicDetectionResult`
       validati per lo stesso `document_id` e `card_discovery_run_id`;
    2) una Detection normale (`MANUAL_DETECTION`) distinta sullo stesso documento e stessa label.
    """
    doc_id = "doc::unit_corr_verified"
    disc_run_id = "persisted_9f8e7d6c"
    topic = DiscoveredTopic(
        label="Turni e Prospetto Fotografico",
        short_description="Conferma del turno mattutino estratta dal prospetto OCR",
        evidence_ids=("msg::doc::unit_corr_verified::5::OCR_TEXT",),
    )
    verif_det = TopicDetectionResult(
        topic=build_topic_query(label=topic.label, description=topic.short_description),
        decision=TopicDecision.PRESENT,
        evidence_ids=topic.evidence_ids,
        rationale="Seconda inferenza indipendente Prova del Nove.",
        provenance_document_id=doc_id,
        metadata={
            "run_id": "det_verif_002",
            "origin_flow": "DISCOVERY_VERIFICATION",
            "verified_discovery_run_id": disc_run_id,
            "query_mode": "LABEL_AND_DESCRIPTION",
        },
    )
    manual_det = TopicDetectionResult(
        topic=build_topic_query(label="Turni e Prospetto Fotografico"),
        decision=TopicDecision.ABSENT,
        evidence_ids=(),
        rationale="Ricerca manuale a sola label.",
        provenance_document_id=doc_id,
        metadata={
            "run_id": "det_manual_001",
            "origin_flow": "MANUAL_DETECTION",
            "query_mode": "LABEL_ONLY",
        },
    )
    other_doc_det = dataclasses.replace(
        manual_det,
        provenance_document_id="doc::altro_documento",
        metadata={
            "run_id": "det_other_doc",
            "origin_flow": "MANUAL_DETECTION",
            "query_mode": "LABEL_ONLY",
        },
    )
    cons = ConsistencyResult(
        document_id=doc_id,
        topic_label=topic.label,
        discovery_run_id=disc_run_id,
        detection_run_id="det_verif_002",
        status=ConsistencyStatus.CONSISTENT,
        discovery_evidence_ids=topic.evidence_ids,
        detection_evidence_ids=topic.evidence_ids,
        overlap_evidence_ids=topic.evidence_ids,
        rationale="Coerenza confermata.",
        metadata={"discovery_topic_uid": compute_discovered_topic_uid(disc_run_id, topic)},
    )

    # Nessuna detection manuale sullo stesso documento -> nessuna nota superflua
    assert (
        build_manual_detection_correlation_note(
            document_id=doc_id,
            topic_label=topic.label,
            detection_runs=(verif_det, other_doc_det),
            card_discovery_run_id=disc_run_id,
            topic_verifications=(cons,),
        )
        is None
    )

    # Se la Detection della Prova del Nove manca dalla sessione o ha origin_flow manuale -> fail-closed (None)
    assert (
        build_manual_detection_correlation_note(
            document_id=doc_id,
            topic_label=topic.label,
            detection_runs=(manual_det,),
            card_discovery_run_id=disc_run_id,
            topic_verifications=(cons,),
        )
        is None
    )
    forged_manual_as_verif = dataclasses.replace(
        verif_det,
        metadata={"run_id": "det_verif_002", "origin_flow": "MANUAL_DETECTION"},
    )
    assert (
        build_manual_detection_correlation_note(
            document_id=doc_id,
            topic_label=topic.label,
            detection_runs=(forged_manual_as_verif, manual_det),
            card_discovery_run_id=disc_run_id,
            topic_verifications=(cons,),
        )
        is None
    )

    # Caso autentico completo: Prova del Nove validata + Detection manuale separata sullo stesso documento
    note = build_manual_detection_correlation_note(
        document_id=doc_id,
        topic_label=topic.label,
        detection_runs=(other_doc_det, verif_det, manual_det),
        card_discovery_run_id=disc_run_id,
        topic_verifications=(cons,),
    )
    assert note is not None
    assert "det_manual_001" in note
    assert "Sola label" in note
    assert f"Run `{disc_run_id}`" in note
    assert "La verifica sopra visualizzata è ancorata unicamente alla specifica Discovery" in note
    assert "det_other_doc" not in note


def test_unit_missing_or_blank_provenance_never_uses_fake_ids_or_matches_empty_runs() -> None:
    doc = _build_synthetic_photo_chat(doc_id="doc::prov_guard", message_count=4, ocr_at_index=2)
    eid = doc.all_evidence_sections[0].evidence_id
    topic = DiscoveredTopic(label="Tema A", short_description="Sintesi A", evidence_ids=(eid,))

    run_without_id = TopicDiscoveryResult(
        topics=(topic,),
        provenance_document_id=doc.document_id,
        metadata={"run_id": "", "origin": AnalysisOrigin.LIVE_LM_STUDIO.value, "status": "COMPLETE"},
    )
    assert find_discovery_run(None, doc.document_id, [run_without_id]) is None
    assert find_discovery_run("", doc.document_id, [run_without_id]) is None
    assert find_discovery_run("   ", doc.document_id, [run_without_id]) is None
    assert select_verification_results((), doc.document_id, "   ", topic) == ()

    pre_no_prov = evaluate_verification_preconditions(
        doc,
        run_without_id,
        topic_provenance_document_id=None,
        topic_discovery_run_id="run_ok",
        discovered_topic=topic,
    )
    assert pre_no_prov.can_verify is False and pre_no_prov.block_code == "PROVENANCE_MISSING"

    pre_no_run_id = evaluate_verification_preconditions(
        doc,
        run_without_id,
        topic_provenance_document_id=doc.document_id,
        topic_discovery_run_id="   ",
        discovered_topic=topic,
    )
    assert pre_no_run_id.can_verify is False and pre_no_run_id.block_code == "RUN_ID_MISSING"

    run_no_origin = TopicDiscoveryResult(
        topics=(topic,),
        provenance_document_id=doc.document_id,
        metadata={"run_id": "run_no_origin", "status": "COMPLETE"},
    )
    pre_no_origin = evaluate_verification_preconditions(
        doc,
        run_no_origin,
        topic_provenance_document_id=doc.document_id,
        topic_discovery_run_id="run_no_origin",
        discovered_topic=topic,
    )
    assert pre_no_origin.can_verify is False and pre_no_origin.block_code == "ORIGIN_MISSING"

    run_synth = TopicDiscoveryResult(
        topics=(topic,),
        provenance_document_id=doc.document_id,
        metadata={
            "run_id": "run_synth_ref",
            "origin": AnalysisOrigin.SYNTHETIC_REFERENCE.value,
            "status": "COMPLETE",
        },
    )
    pre_synth = evaluate_verification_preconditions(
        doc,
        run_synth,
        topic_provenance_document_id=doc.document_id,
        topic_discovery_run_id="run_synth_ref",
        discovered_topic=topic,
    )
    assert pre_synth.can_verify is False and pre_synth.block_code == "SYNTHETIC_REFERENCE_ORIGIN"


def test_operational_profile_selector_handles_insufficient_context_headroom_without_name_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc = _build_synthetic_photo_chat(doc_id="doc::headroom_guard", message_count=3, ocr_at_index=2)
    at = _setup_fresh_apptest_for_document(doc)

    def _raise_headroom(*args: Any, **kwargs: Any) -> Any:
        raise InsufficientContextHeadroomError(8192, 8500, -308)

    monkeypatch.setattr(presentation, "resolve_operational_profile", _raise_headroom)
    at.sidebar.radio[0].set_value("5. Analisi topic")
    at.run()

    assert not at.exception
    assert any("Spazio di contesto insufficiente" in err.value for err in at.error)
    assert state.get_operational_profile(at.session_state) == OperationalProfile.PRUDENT


# ==============================================================================
# Attività F.1 — Solo Topic Discovery con più topic: renderizza tutti senza crash
# ==============================================================================

def test_f1_discovery_only_with_multiple_topics_renders_all_without_crash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CHATANALYSIS_CHECKPOINT_DIR", str(tmp_path / "chk_f1"))
    doc = _build_synthetic_photo_chat(doc_id="doc::f1_disc_only", message_count=6, ocr_at_index=2)
    labels = [
        "Coordinamento Turni Mattutini",
        "Gestione Consegne Magazzino",
        "Revisione Moduli Fotografici",
        "Riepilogo Presenze Settimanali",
    ]
    client = _DeterministicMockClient(
        responses=[
            {
                "topics": [
                    {"label": lbl, "short_description": f"Sintesi operativa {i}", "evidence_ids": [f"E00{i}"]}
                    for i, lbl in enumerate(labels, start=1)
                ]
            }
        ]
    )

    at = _setup_fresh_apptest_for_document(doc)
    at.session_state["lm_studio_client"] = client
    at.sidebar.radio[0].set_value("5. Analisi topic")
    at.run()

    _button_by_key(at, "btn_run_live_discovery").click()
    at.run()

    assert not at.exception
    assert not list(at.error)
    mds = [m.value for m in at.markdown]
    for lbl in labels:
        assert any(f"### 💡 {lbl}" in md for md in mds), f"Topic mancante: {lbl}"

    verify_btns = [b for b in at.button if (b.key or "").startswith("verify_disc_topic_")]
    assert len(verify_btns) == len(labels)
    assert all(not b.disabled for b in verify_btns)
    assert not any("Risultato Verifica Indipendente" in md for md in mds)
    assert not any("Nota di correlazione indipendente" in i.value for i in at.info)


# ==============================================================================
# Attività F.2 & F.3 — Discovery preesistente + Topic Detection NORMALE
# (PRESENT, ABSENT, UNCERTAIN): nessun crash in Discovery, nessun finto legame
# ==============================================================================

@pytest.mark.parametrize("normal_decision", ["PRESENT", "ABSENT", "UNCERTAIN"])
def test_f2_f3_preexisting_discovery_plus_normal_detection_present_absent_uncertain_no_crash_no_fake_link(
    normal_decision: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    Riproduce il flusso reale dell'utente in Windows:
    1) Topic Discovery preesistente con 4 argomenti;
    2) Esecuzione di una Topic Detection NORMALE dalla UI (`btn_run_manual_detection`) sullo stesso
       titolo del primo argomento di Discovery, con esito PRESENT, ABSENT o UNCERTAIN;
    3) Verifica che la pagina non vada in crash, tutti i 4 topic di Discovery restino visibili,
       la Detection normale compaia nella scheda Detection (`🎯 Topic Detection manuale`) e
       NESSUN banner/esito di Prova del Nove o di ancoraggio compaia nella scheda Discovery.
    """
    monkeypatch.setenv("CHATANALYSIS_CHECKPOINT_DIR", str(tmp_path / f"chk_f23_{normal_decision}"))
    doc = _build_synthetic_photo_chat(doc_id=f"doc::f23_{normal_decision}", message_count=6, ocr_at_index=2)
    labels = [
        "Accettazione Turno Mattutino",
        "Controllo Giacenze",
        "Pianificazione Trasporti",
        "Verifica Documentale",
    ]
    det_payload: dict[str, Any] = {
        "decision": normal_decision,
        "evidence_ids": ["E001"] if normal_decision == "PRESENT" else [],
        "rationale": f"Esito manuale {normal_decision} formulato su richiesta dell'operatore.",
    }
    client = _DeterministicMockClient(
        responses=[
            {
                "topics": [
                    {"label": lbl, "short_description": f"Descrizione {lbl}", "evidence_ids": [f"E00{i}"]}
                    for i, lbl in enumerate(labels, start=1)
                ]
            },
            det_payload,
        ]
    )

    at = _setup_fresh_apptest_for_document(doc)
    at.session_state["lm_studio_client"] = client
    at.sidebar.radio[0].set_value("5. Analisi topic")
    at.run()

    # 1. Esegui Discovery
    _button_by_key(at, "btn_run_live_discovery").click()
    at.run()
    assert not at.exception

    # 2. Inserisci il titolo del primo topic nella Topic Detection NORMALE e clicca «🚀 Esegui Topic Detection»
    _text_input_by_key(at, "manual_topic_input").set_value(labels[0])
    _text_area_by_key(at, "manual_desc_input").set_value("")
    _button_by_key(at, "btn_run_manual_detection").click()
    at.run()

    assert not at.exception, f"Crash su Detection normale ({normal_decision}): {at.exception}"
    assert not list(at.error), [e.value for e in at.error]

    # La Detection normale è mostrata regolarmente nella propria sezione (`tab_det`)
    mds = [m.value for m in at.markdown]
    captions = [c.value for c in at.caption]
    infos = [i.value for i in at.info]
    assert any(f"### Risultato Verifica: {labels[0]}" in md for md in mds)
    assert any("🎯 Topic Detection manuale" in cap for cap in captions)

    # Nessun ConsistencyResult generato e nessun finto legame con Prova del Nove in `tab_disc`
    assert state.get_consistency_results(doc.document_id, state=at.session_state) == ()
    assert not any("Risultato Verifica Indipendente" in md for md in mds)
    assert not any("Nota di correlazione indipendente" in inf for inf in infos)
    assert not any("ancorata unicamente alla specifica Discovery" in inf for inf in infos)

    # Tutti i 4 topic della Discovery restano visibili e navigabili anche dopo ulteriore rerun UI
    for lbl in labels:
        assert any(f"### 💡 {lbl}" in md for md in mds), f"Topic Discovery '{lbl}' scomparso dopo Detection normale"
    at.run()
    assert not at.exception
    mds_rerun = [m.value for m in at.markdown]
    for lbl in labels:
        assert any(f"### 💡 {lbl}" in md for md in mds_rerun)


# ==============================================================================
# Attività F.4 — Topic Detection normale PRIMA della Discovery e più Detection
# normali ripetute in sequenza: nessun crash o stato spurio
# ==============================================================================

def test_f4_normal_detections_before_discovery_and_repeated_in_sequence_no_crash_or_spurious_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CHATANALYSIS_CHECKPOINT_DIR", str(tmp_path / "chk_f4"))
    doc = _build_synthetic_photo_chat(doc_id="doc::f4_seq", message_count=6, ocr_at_index=2)
    disc_labels = ["Tema Alfa Operativo", "Tema Beta Logistico", "Tema Gamma Contabile"]

    client = _DeterministicMockClient(
        responses=[
            # 1) Detection normale #1 PRIMA della Discovery (PRESENT su "Tema Alfa Operativo")
            {"decision": "PRESENT", "evidence_ids": ["E001"], "rationale": "Prima ricerca manuale."},
            # 2) Detection normale #2 PRIMA della Discovery (ABSENT su label estranea)
            {"decision": "ABSENT", "evidence_ids": [], "rationale": "Argomento estraneo non presente."},
            # 3) Detection normale #3 ripetuta su "Tema Alfa Operativo" con descrizione diversa -> UNCERTAIN
            {"decision": "UNCERTAIN", "evidence_ids": [], "rationale": "Seconda ricerca manuale incerta."},
            # 4) Topic Discovery LIVE (3 topic, incluso "Tema Alfa Operativo")
            {
                "topics": [
                    {"label": disc_labels[0], "short_description": "Sintesi alfa", "evidence_ids": ["E001"]},
                    {"label": disc_labels[1], "short_description": "Sintesi beta", "evidence_ids": ["E002"]},
                    {"label": disc_labels[2], "short_description": "Sintesi gamma", "evidence_ids": ["E003"]},
                ]
            },
            # 5) Detection normale #4 DOPO la Discovery su "Tema Beta Logistico" -> ABSENT
            {"decision": "ABSENT", "evidence_ids": [], "rationale": "Quarta ricerca manuale dopo Discovery."},
        ]
    )

    at = _setup_fresh_apptest_for_document(doc)
    at.session_state["lm_studio_client"] = client
    at.sidebar.radio[0].set_value("5. Analisi topic")
    at.run()

    # Detection normale #1 (prima della Discovery)
    _text_input_by_key(at, "manual_topic_input").set_value("Tema Alfa Operativo")
    _text_area_by_key(at, "manual_desc_input").set_value("")
    _button_by_key(at, "btn_run_manual_detection").click()
    at.run()
    assert not at.exception

    # Detection normale #2 (label diversa)
    _text_input_by_key(at, "manual_topic_input").set_value("Tema Estraneo Non Emerso")
    _button_by_key(at, "btn_run_manual_detection").click()
    at.run()
    assert not at.exception

    # Detection normale #3 (ripetuta su "Tema Alfa Operativo" con descrizione)
    _text_input_by_key(at, "manual_topic_input").set_value("Tema Alfa Operativo")
    _text_area_by_key(at, "manual_desc_input").set_value("Dettaglio specifico richiesto dall'operatore")
    _button_by_key(at, "btn_run_manual_detection").click()
    at.run()
    assert not at.exception

    assert len(state.get_detection_results(at.session_state)) == 3
    assert state.get_discovery_results(at.session_state) == ()
    assert any(
        "Nessuna Topic Discovery registrata o salvata per questa conversazione" in i.value
        for i in at.info
    )

    # Ora esegui Topic Discovery
    _button_by_key(at, "btn_run_live_discovery").click()
    at.run()
    assert not at.exception

    # Seguita da una quarta Detection normale su "Tema Beta Logistico"
    _text_input_by_key(at, "manual_topic_input").set_value("Tema Beta Logistico")
    _text_area_by_key(at, "manual_desc_input").set_value("")
    _button_by_key(at, "btn_run_manual_detection").click()
    at.run()
    assert not at.exception
    assert not list(at.error)

    # Verifica integrità: 4 detection manuali, 1 discovery con 3 topic, 0 consistency_results, 0 finti banner
    det_runs = state.get_detection_results(at.session_state)
    assert len(det_runs) == 4
    assert all(r.metadata.get("origin_flow") == "MANUAL_DETECTION" for r in det_runs)
    assert state.get_consistency_results(doc.document_id, state=at.session_state) == ()

    mds = [m.value for m in at.markdown]
    for lbl in disc_labels:
        assert any(f"### 💡 {lbl}" in md for md in mds)
    assert not any("Risultato Verifica Indipendente" in md for md in mds)
    assert not any("Nota di correlazione indipendente" in i.value for i in at.info)


# ==============================================================================
# Attività F.5 — Prova del Nove autentica su un topic: corretta visualizzazione
# dell'ancoraggio quando gli ID di run sono validi (sia sola, sia con Detection normale)
# ==============================================================================

def test_f5_authentic_prova_del_nove_displays_valid_anchoring_alone_and_with_separate_normal_detection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CHATANALYSIS_CHECKPOINT_DIR", str(tmp_path / "chk_f5"))
    doc = _build_synthetic_photo_chat(doc_id="doc::f5_verif", message_count=6, ocr_at_index=2)
    labels = ["Conferma Turno OCR", "Logistica Consegne", "Rendicontazione Finale"]

    client = _DeterministicMockClient(
        responses=[
            # Discovery con 3 topic
            {
                "topics": [
                    {"label": labels[0], "short_description": "Conferma turno da prospetto OCR", "evidence_ids": ["E002"]},
                    {"label": labels[1], "short_description": "Coordinamento consegne", "evidence_ids": ["E003"]},
                    {"label": labels[2], "short_description": "Chiusura conteggi", "evidence_ids": ["E004"]},
                ]
            },
            # Prova del Nove su Topic #1 -> PRESENT con overlap su E002
            {"decision": "PRESENT", "evidence_ids": ["E002"], "rationale": "Verifica indipendente coerente."},
            # Successiva Detection NORMALE a sola label su Topic #1 -> ABSENT
            {"decision": "ABSENT", "evidence_ids": [], "rationale": "Ricerca manuale a sola label negativa."},
        ]
    )

    at = _setup_fresh_apptest_for_document(doc)
    at.session_state["lm_studio_client"] = client
    at.sidebar.radio[0].set_value("5. Analisi topic")
    at.run()

    # 1. Esegui Topic Discovery
    _button_by_key(at, "btn_run_live_discovery").click()
    at.run()
    assert not at.exception
    disc_run_id = state.get_discovery_results(at.session_state)[0].metadata["run_id"]

    # 2. Avvia la Prova del Nove autentica sul Topic #1
    verify_btns = [b for b in at.button if (b.key or "").startswith("verify_disc_topic_")]
    assert len(verify_btns) == 3
    verify_btns[0].click()
    at.run()

    assert not at.exception
    assert not list(at.error)
    cons_list = state.get_consistency_results(doc.document_id, state=at.session_state)
    assert len(cons_list) == 1
    assert cons_list[0].discovery_run_id == disc_run_id
    assert cons_list[0].status == ConsistencyStatus.CONSISTENT

    mds = [m.value for m in at.markdown]
    assert sum(1 for md in mds if "Risultato Verifica Indipendente" in md) == 1
    # Finché non c'è una Detection manuale omonima, non compare la nota di disambiguazione manuale
    assert not any("Nota di correlazione indipendente" in i.value for i in at.info)

    verif_json, _ = export_semantic_verification_json(cons_list[0], doc, session_state=at.session_state)
    assert validate_semantic_verification_report(json.loads(verif_json)) == []

    # 3. Ora esegui una Topic Detection NORMALE separata con la stessa label del Topic #1
    _text_input_by_key(at, "manual_topic_input").set_value(labels[0])
    _text_area_by_key(at, "manual_desc_input").set_value("")
    _button_by_key(at, "btn_run_manual_detection").click()
    at.run()

    assert not at.exception
    assert not list(at.error)
    # La Prova del Nove resta intatta e ora compare la nota di ancoraggio solo sul Topic #1 verificato
    cons_after = state.get_consistency_results(doc.document_id, state=at.session_state)
    assert len(cons_after) == 1
    corr_notes = [i.value for i in at.info if "Nota di correlazione indipendente" in i.value]
    assert len(corr_notes) == 1
    assert f"La verifica sopra visualizzata è ancorata unicamente alla specifica Discovery (Run `{disc_run_id}`)" in corr_notes[0]
    for lbl in labels:
        assert any(f"### 💡 {lbl}" in md for md in [m.value for m in at.markdown])


# ==============================================================================
# Attività F.6 — Provenienza della verifica mancante o incompatibile:
# nessuna conferma finta, comportamento fail-closed non distruttivo
# ==============================================================================

def test_f6_missing_or_incompatible_verification_provenance_fail_closed_no_fake_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CHATANALYSIS_CHECKPOINT_DIR", str(tmp_path / "chk_f6"))
    doc = _build_synthetic_photo_chat(doc_id="doc::f6_fail_closed", message_count=8, ocr_at_index=3)
    eids = [s.evidence_id for s in doc.all_evidence_sections]

    at = _setup_fresh_apptest_for_document(doc)
    client = _DeterministicMockClient(
        responses=[
            {
                "topics": [
                    {"label": "Topic Con Verifica Incompatibile", "short_description": "Topic valido C", "evidence_ids": [eids[0]]},
                    {"label": "Topic Valido Pulito", "short_description": "Topic valido D", "evidence_ids": [eids[1]]},
                ]
            },
            {
                "decision": "PRESENT",
                "evidence_ids": ["E001"],
                "rationale": "Detection manuale che non deve mai fingere una Prova del Nove.",
            },
        ]
    )
    at.session_state["lm_studio_client"] = client

    valid_disc = execute_topic_discovery(
        document=doc,
        client=client,
        model_id=MODEL_ID,
        max_topics=5,
        session_state=at.session_state,
        enable_checkpointing=False,
    )
    state.add_discovery_result(valid_disc, state=at.session_state)
    valid_run_id = valid_disc.metadata["run_id"]
    topic_c = next(t for t in valid_disc.topics if t.label == "Topic Con Verifica Incompatibile")
    topic_d = next(t for t in valid_disc.topics if t.label == "Topic Valido Pulito")

    # Card A: Discovery priva di `run_id`
    broken_no_run = TopicDiscoveryResult(
        topics=(DiscoveredTopic(label="Topic Senza RunId", short_description="Senza run_id", evidence_ids=(eids[2],)),),
        provenance_document_id=doc.document_id,
        metadata={"run_id": None, "status": "COMPLETE"},
    )
    # Card B: Discovery con origine `SYNTHETIC_REFERENCE` (non verificabile con Prova del Nove)
    broken_synth_origin = TopicDiscoveryResult(
        topics=(DiscoveredTopic(label="Topic Sintetico Non Verificabile", short_description="Origine sintetica", evidence_ids=(eids[3],)),),
        provenance_document_id=doc.document_id,
        metadata={"run_id": "disc_synth_01", "origin": AnalysisOrigin.SYNTHETIC_REFERENCE.value, "status": "COMPLETE"},
    )
    # Verifica unitaria esplicita anche del blocco `DOCUMENT_MISMATCH` su precondizioni
    foreign_disc = TopicDiscoveryResult(
        topics=(DiscoveredTopic(label="Topic Altro Doc", short_description="Doc incoerente", evidence_ids=(eids[3],)),),
        provenance_document_id="doc::altro_documento_esterno",
        metadata={"run_id": "disc_foreign_01", "origin": AnalysisOrigin.LIVE_LM_STUDIO.value, "status": "COMPLETE"},
    )
    pre_foreign = evaluate_verification_preconditions(
        doc,
        foreign_disc,
        topic_provenance_document_id="doc::altro_documento_esterno",
        topic_discovery_run_id="disc_foreign_01",
        discovered_topic=foreign_disc.topics[0],
    )
    assert pre_foreign.can_verify is False and pre_foreign.block_code == "DOCUMENT_MISMATCH"

    doc_analysis = state.get_document_analysis_state(doc.document_id, state=at.session_state)
    doc_analysis.discovery_runs = (broken_no_run, broken_synth_origin, valid_disc)

    # Esegui una Detection MANUALE e inietta quattro record `ConsistencyResult` incompatibili/manomessi per `topic_c`:
    # 1) punta al `run_id` di una `MANUAL_DETECTION` anziché `DISCOVERY_VERIFICATION`;
    # 2) ha `detection_run_id` vuoto;
    # 3) dichiara `discovery_run_id` vuoto;
    # 4) dichiara `document_id` appartenente a un altro documento pur citando `valid_run_id`.
    manual_det = execute_manual_topic_detection(
        document=doc,
        topic=build_topic_query(label=topic_c.label, description=topic_c.short_description),
        client=client,
        model_id=MODEL_ID,
        session_state=at.session_state,
        enable_checkpointing=False,
    )
    state.add_detection_result(manual_det, state=at.session_state)
    manual_run_id = manual_det.metadata["run_id"]

    uid_c = compute_discovered_topic_uid(valid_run_id, topic_c)
    forged_manual_link = ConsistencyResult(
        document_id=doc.document_id,
        topic_label=topic_c.label,
        discovery_run_id=valid_run_id,
        detection_run_id=manual_run_id,
        status=ConsistencyStatus.CONSISTENT,
        discovery_evidence_ids=topic_c.evidence_ids,
        detection_evidence_ids=manual_det.evidence_ids,
        overlap_evidence_ids=topic_c.evidence_ids,
        rationale="Record spurio agganciato a una Detection manuale.",
        metadata={"discovery_topic_uid": uid_c},
    )
    forged_empty_det = dataclasses.replace(forged_manual_link, detection_run_id="   ")
    forged_empty_disc = dataclasses.replace(forged_manual_link, discovery_run_id="")
    forged_wrong_doc = dataclasses.replace(forged_manual_link, document_id="doc::altro_documento_esterno")
    doc_analysis.consistency_runs = (forged_manual_link, forged_empty_det, forged_empty_disc, forged_wrong_doc)

    assert (
        select_verification_results(
            doc_analysis.consistency_runs,
            doc.document_id,
            valid_run_id,
            topic_c,
            detection_runs=doc_analysis.detection_runs,
        )
        == ()
    )
    assert has_unverifiable_verification_records(
        doc_analysis.consistency_runs,
        doc.document_id,
        valid_run_id,
        topic_c,
        detection_runs=doc_analysis.detection_runs,
    ) is True
    assert has_unverifiable_verification_records(
        doc_analysis.consistency_runs,
        doc.document_id,
        valid_run_id,
        topic_d,
        detection_runs=doc_analysis.detection_runs,
    ) is False

    at.sidebar.radio[0].set_value("5. Analisi topic")
    at.run()

    assert not at.exception, f"Crash su metadati/provenienza incompatibili: {at.exception}"

    mds = [m.value for m in at.markdown]
    for expected in (
        "Topic Senza RunId",
        "Topic Sintetico Non Verificabile",
        "Topic Con Verifica Incompatibile",
        "Topic Valido Pulito",
    ):
        assert any(f"### 💡 {expected}" in md for md in mds), f"Card mancante per '{expected}'"

    verify_buttons = [b for b in at.button if (b.key or "").startswith("verify_disc_topic_")]
    assert len(verify_buttons) == 4
    # I due topic non verificabili (senza run_id e sintetico) hanno pulsante disabilitato; i due validi restano abilitati
    disabled_count = sum(1 for b in verify_buttons if b.disabled)
    enabled_count = sum(1 for b in verify_buttons if not b.disabled)
    assert disabled_count == 2 and enabled_count == 2

    # Nessuna conferma finta di Prova del Nove e nessuna nota di verifica ancorata!
    assert not any("Risultato Verifica Indipendente" in md for md in mds)
    assert not any("Nota di correlazione indipendente" in i.value for i in at.info)

    # Avviso esplicito fail-closed di verifica non attestabile sul solo Topic C (1 occorrenza)
    captions = [c.value for c in at.caption]
    unverifiable_notices = [c for c in captions if "Verifica non attestabile" in c]
    assert len(unverifiable_notices) == 1
    assert any("Identificativo della run di Discovery mancante nei metadati" in c for c in captions)
    assert any("riferimento sintetico dimostrativo" in c for c in captions)
    assert not any("Discovery Run ID: `None`" in c for c in captions)


# ==============================================================================
# Attività F.7 — Risultati di Discovery e Detection recuperati da checkpoint SQLite
# seguiti da rerun della UI: zero chiamate LLM, nessuna rietichettatura, nessuna regressione
# ==============================================================================

def test_f7_checkpoint_recovered_discovery_and_detection_across_ui_reruns_without_llm_reexecution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    chk_dir = tmp_path / "chk_f7_sqlite"
    monkeypatch.setenv("CHATANALYSIS_CHECKPOINT_DIR", str(chk_dir))
    doc = _build_synthetic_photo_chat(doc_id="doc::f7_checkpoint", message_count=6, ocr_at_index=2)

    labels = ["Coordinamento Turni", "Gestione Fotografie OCR", "Riepilogo Consegne"]
    seed_client = _DeterministicMockClient(
        responses=[
            {
                "topics": [
                    {"label": labels[0], "short_description": "Turni operativi", "evidence_ids": ["E001"]},
                    {"label": labels[1], "short_description": "Modulo fotografico", "evidence_ids": ["E002"]},
                    {"label": labels[2], "short_description": "Consegne giornaliere", "evidence_ids": ["E003"]},
                ]
            },
            {
                "decision": "PRESENT",
                "evidence_ids": ["E001"],
                "rationale": "Detection manuale salvata nel checkpoint SQLite.",
            },
        ]
    )

    # Fase 1: Popola il checkpoint SQLite isolato sia per Discovery sia per Detection normale
    seed_state: dict[str, Any] = {}
    seeded_disc = execute_topic_discovery(
        document=doc,
        client=seed_client,
        model_id=MODEL_ID,
        max_topics=10,
        session_state=seed_state,
        profile=OperationalProfile.BALANCED,
    )
    seeded_det = execute_manual_topic_detection(
        document=doc,
        topic=build_topic_query(label=labels[0]),
        client=seed_client,
        model_id=MODEL_ID,
        session_state=seed_state,
        profile=OperationalProfile.BALANCED,
    )
    assert len(seeded_disc.topics) == 3
    assert seeded_det.decision == TopicDecision.PRESENT
    assert seed_client.call_count == 2

    # Fase 2: Nuova sessione Streamlit pulita con client sentinella che VIETA qualsiasi chiamata LLM
    forbid_client = _ForbidLlmCallsClient()
    at = _setup_fresh_apptest_for_document(doc)
    at.session_state["lm_studio_client"] = forbid_client
    at.sidebar.radio[0].set_value("5. Analisi topic")
    at.run()

    assert not at.exception
    assert forbid_client.call_count == 0

    # La Discovery è stata recuperata da SQLite con prefisso `persisted_` e NON è marcata come run live
    disc_runs = state.get_discovery_results(at.session_state)
    assert len(disc_runs) == 1
    recovered_disc_id = disc_runs[0].metadata["run_id"]
    assert recovered_disc_id.startswith("persisted_")
    assert recovered_disc_id not in state.get_authoritative_live_run_ids(at.session_state)

    # Ora esegui la stessa Topic Detection normale dalla UI: deve recuperare da SQLite con 0 chiamate LLM!
    _text_input_by_key(at, "manual_topic_input").set_value(labels[0])
    _text_area_by_key(at, "manual_desc_input").set_value("")
    _button_by_key(at, "btn_run_manual_detection").click()
    at.run()

    assert not at.exception
    assert not list(at.error)
    assert forbid_client.call_count == 0
    det_runs = state.get_detection_results(at.session_state)
    assert len(det_runs) == 1
    assert det_runs[0].metadata.get("origin_flow") == "MANUAL_DETECTION"
    assert det_runs[0].metadata.get("recovered_chunk_count", 0) >= 1

    # Rerun multipli e interazione col filtro di ricerca Discovery: nessun crash, nessun finto legame
    _text_input_by_key(at, "disc_query_input").set_value("Fotografie")
    at.run()
    assert not at.exception
    assert sum(1 for m in at.markdown if m.value.startswith("### 💡 ")) == 1

    _text_input_by_key(at, "disc_query_input").set_value("")
    at.run()
    assert not at.exception
    mds_final = [m.value for m in at.markdown]
    for lbl in labels:
        assert any(f"### 💡 {lbl}" in md for md in mds_final)
    assert not any("Risultato Verifica Indipendente" in md for md in mds_final)
    assert not any("Nota di correlazione indipendente" in i.value for i in at.info)


# ==============================================================================
# Attività F.8 — Discovery con 10 topic (chat foto/OCR da 36 messaggi):
# l'interfaccia NON si ferma dopo il primo, tutti i 10 restano raggiungibili/navigabili
# sia dopo Detection normale sia dopo Prova del Nove separata
# ==============================================================================

def test_f8_photo_chat_36_messages_10_topics_normal_detection_and_prova_del_nove_full_navigation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    chk_dir = tmp_path / "isolated_checkpoints_f8"
    monkeypatch.setenv("CHATANALYSIS_CHECKPOINT_DIR", str(chk_dir))

    doc = _build_synthetic_photo_chat(doc_id="doc::photo36_10topics", message_count=36, ocr_at_index=5)
    sections = doc.all_evidence_sections
    ocr_sections = [s for s in sections if "OCR" in s.source_type.value]
    assert len(ocr_sections) == 1

    expected_labels = [
        "Accettazione Turno Mattutino da Modulo OCR",
        "Aggiornamento Orari Ricevimento",
        "Archiviazione Moduli Firmati",
        "Assegnazione Postazioni Pomeridiane",
        "Chiusura Riepilogo Settimanale",
        "Controllo Bolle di Trasporto",
        "Manutenzione Mezzi Operativi",
        "Pianificazione Consegne Settore Nord",
        "Riconciliazione Giacenze Mensili",
        "Verifica Inventario Scaffalature",
    ]

    class _ChunkAwareDiscoveryClient(BaseLlmClient):
        model_id = MODEL_ID

        def __init__(self) -> None:
            self.call_count = 0
            self._next_topic_idx = 0

        def is_available(self) -> bool:
            return True

        def chat_completion(
            self,
            messages: list[dict[str, str]],
            model_id: str | None = None,
            **kwargs: Any,
        ) -> LlmCompletionResponse:
            self.call_count += 1
            user_text = messages[-1]["content"]
            compact_rows = [
                json.loads(line)
                for line in user_text.splitlines()
                if line.strip().startswith('{"id":')
            ]
            chunk_topics: list[dict[str, Any]] = []
            ocr_row = next((r for r in compact_rows if r.get("source_type") == "OCR_TEXT"), None)
            if ocr_row is not None and self._next_topic_idx == 0:
                chunk_topics.append(
                    {
                        "label": expected_labels[0],
                        "short_description": "Conferma prenotazione turno mattutino rilevata dal prospetto fotografico OCR.",
                        "evidence_ids": [ocr_row["id"]],
                    }
                )
                self._next_topic_idx = 1

            non_ocr_rows = [r for r in compact_rows if r.get("source_type") != "OCR_TEXT"]
            max_m = re.search(r"extract up to (\d+) main topics", user_text)
            per_chunk_cap = int(max_m.group(1)) if max_m else 5
            for row in non_ocr_rows:
                if len(chunk_topics) >= min(3, per_chunk_cap) or self._next_topic_idx >= len(expected_labels):
                    break
                lbl = expected_labels[self._next_topic_idx]
                chunk_topics.append(
                    {
                        "label": lbl,
                        "short_description": f"Attività operativa documentata per {lbl.lower()}.",
                        "evidence_ids": [row["id"]],
                    }
                )
                self._next_topic_idx += 1

            return LlmCompletionResponse(
                content=json.dumps({"topics": chunk_topics}, ensure_ascii=False),
                model=model_id or self.model_id,
                prompt_tokens=40,
                completion_tokens=30,
                total_tokens=70,
                latency_seconds=0.05,
            )

    seed_session: dict[str, Any] = {}
    seed_client = _ChunkAwareDiscoveryClient()
    seeded_disc = execute_topic_discovery(
        document=doc,
        client=seed_client,
        model_id=MODEL_ID,
        max_topics=10,
        session_state=seed_session,
        profile=OperationalProfile.BALANCED,
    )
    assert len(seeded_disc.topics) == 10
    seeded_by_label = {t.label: t for t in seeded_disc.topics}
    assert set(seeded_by_label) == set(expected_labels)
    first_seeded_topic = seeded_by_label[expected_labels[0]]
    last_seeded_topic = seeded_by_label[expected_labels[-1]]

    class _ChunkAwareDetectionClient(BaseLlmClient):
        model_id = MODEL_ID

        def __init__(self, target_evidence_ids_per_run: list[str | None]) -> None:
            self._targets = list(target_evidence_ids_per_run)
            self._active_target: str | None = None
            self.call_count = 0

        def begin_next_run(self) -> None:
            self._active_target = self._targets.pop(0) if self._targets else None

        def is_available(self) -> bool:
            return True

        def chat_completion(
            self,
            messages: list[dict[str, str]],
            model_id: str | None = None,
            **kwargs: Any,
        ) -> LlmCompletionResponse:
            self.call_count += 1
            user_text = messages[-1]["content"]
            if self._active_target is None:
                payload: dict[str, Any] = {
                    "decision": "PRESENT",
                    "evidence_ids": [_extract_first_prompt_alias(user_text)],
                    "rationale": "Detection normale eseguita dall'operatore.",
                }
            else:
                matched_id: str | None = None
                expected_msg_id = "::".join(self._active_target.split("::")[:-1])
                expected_stype = self._active_target.split("::")[-1]
                start_marker = "=== BEGIN FORENSIC EVIDENCE DATA"
                end_marker = "=== END FORENSIC EVIDENCE DATA ==="
                if start_marker in user_text and end_marker in user_text:
                    body = user_text.split(start_marker, 1)[1].split("===\n", 1)[-1].split(end_marker, 1)[0].strip()
                    if body.startswith("{") and '"evidence_sections"' in body:
                        parsed = json.loads(body)
                        for sec in parsed.get("evidence_sections", ()):
                            if sec.get("message_id") == expected_msg_id and sec.get("source_type") == expected_stype:
                                matched_id = sec.get("evidence_id")
                                break
                    else:
                        for line in body.splitlines():
                            s = line.strip()
                            if s.startswith("{"):
                                row = json.loads(s)
                                row_stype = row.get("source_type", "ORIGINAL_TEXT")
                                if row.get("msg_id") == expected_msg_id and row_stype == expected_stype:
                                    matched_id = row.get("id") or row.get("evidence_id")
                                    break
                if matched_id is not None:
                    payload = {
                        "decision": "PRESENT",
                        "evidence_ids": [matched_id],
                        "rationale": "Verifica indipendente confermata sull'evidenza del blocco.",
                    }
                else:
                    payload = {
                        "decision": "ABSENT",
                        "evidence_ids": [],
                        "rationale": "Evidenza non presente in questo singolo blocco.",
                    }
            return LlmCompletionResponse(
                content=json.dumps(payload, ensure_ascii=False),
                model=model_id or self.model_id,
                prompt_tokens=30,
                completion_tokens=20,
                total_tokens=50,
                latency_seconds=0.05,
            )

    at = _setup_fresh_apptest_for_document(doc)
    det_client = _ChunkAwareDetectionClient(
        target_evidence_ids_per_run=[
            None,                               # Run 1: Topic Detection NORMALE dall'interfaccia (`btn_run_manual_detection`)
            first_seeded_topic.evidence_ids[0],  # Run 2: Prova del Nove su Topic #1
            last_seeded_topic.evidence_ids[0],   # Run 3: Prova del Nove su Topic #10
        ]
    )
    at.session_state["lm_studio_client"] = det_client

    # Passo 1: Apri «5. Analisi topic» senza chiamate LLM (recupera i 10 topic da checkpoint SQLite)
    at.sidebar.radio[0].set_value("5. Analisi topic")
    with warnings.catch_warnings(record=True) as caught_warnings:
        warnings.simplefilter("always")
        at.run()

    assert not at.exception
    assert det_client.call_count == 0
    assert not any("use_container_width" in str(w.message) for w in caught_warnings), [
        str(w.message) for w in caught_warnings
    ]
    persisted_run_id = state.get_discovery_results(at.session_state)[0].metadata["run_id"]
    assert persisted_run_id.startswith("persisted_")

    # Passo 2: Esegui una Topic Detection NORMALE tramite il pulsante UI «🚀 Esegui Topic Detection» sul Topic #1
    det_client.begin_next_run()
    _text_input_by_key(at, "manual_topic_input").set_value(first_seeded_topic.label)
    _text_area_by_key(at, "manual_desc_input").set_value("")
    with warnings.catch_warnings(record=True) as caught_det_warnings:
        warnings.simplefilter("always")
        _button_by_key(at, "btn_run_manual_detection").click()
        at.run()

    assert not at.exception, f"La UI si è arrestata dopo Topic Detection normale: {at.exception}"
    assert not list(at.error)
    assert not any("use_container_width" in str(w.message) for w in caught_det_warnings)

    # TUTTI i 10 topic della Discovery sono presenti e raggiungibili (nessun arresto dopo il primo!)
    mds_after_normal = [m.value for m in at.markdown]
    for label in expected_labels:
        assert any(f"### 💡 {label}" in md for md in mds_after_normal), f"Card mancante per '{label}'"
    verify_buttons = [b for b in at.button if (b.key or "").startswith("verify_disc_topic_")]
    assert len(verify_buttons) == 10
    assert all(not b.disabled for b in verify_buttons)

    # NESSUNA Prova del Nove finta né nota di ancoraggio per la semplice Detection normale!
    assert state.get_consistency_results(doc.document_id, state=at.session_state) == ()
    assert not any("Risultato Verifica Indipendente" in md for md in mds_after_normal)
    assert not any("Nota di correlazione indipendente" in i.value for i in at.info)

    # Passo 3: Avvia ora separatamente la Prova del Nove su Topic #1 e poi su Topic #10
    det_client.begin_next_run()
    verify_buttons[0].click()
    at.run()
    assert not at.exception
    assert not list(at.error)

    cons_after_v1 = state.get_consistency_results(doc.document_id, state=at.session_state)
    assert len(cons_after_v1) == 1
    assert cons_after_v1[0].discovery_run_id == persisted_run_id
    corr_after_v1 = [i.value for i in at.info if "Nota di correlazione indipendente" in i.value]
    assert len(corr_after_v1) == 1
    assert f"Run `{persisted_run_id}`" in corr_after_v1[0]

    det_client.begin_next_run()
    verify_buttons_v2 = [b for b in at.button if (b.key or "").startswith("verify_disc_topic_")]
    assert len(verify_buttons_v2) == 10
    verify_buttons_v2[9].click()
    at.run()
    assert not at.exception
    assert not list(at.error)

    cons_final = state.get_consistency_results(doc.document_id, state=at.session_state)
    assert len(cons_final) == 2
    assert {c.topic_label for c in cons_final} == {first_seeded_topic.label, last_seeded_topic.label}
    mds_final = [m.value for m in at.markdown]
    for label in expected_labels:
        assert any(f"### 💡 {label}" in md for md in mds_final)
