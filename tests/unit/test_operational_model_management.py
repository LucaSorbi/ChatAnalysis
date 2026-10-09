"""
tests/unit/test_operational_model_management.py
------------------------------------------------
Suite di test esaustiva per la conformità bloccante finale di ChatAnalysis:

SEZIONE WHATSAPP EXPORT (1-19):
1. SourceFormat.WHATSAPP_EXPORT esiste con label corretta;
2. get_importer_for_format restituisce WhatsAppExportImporter;
3. Transcript TXT Android parsato con successo;
4. Transcript TXT iOS parsato con successo;
5. Messaggi multilinea con newline preservati;
6. Messaggi di sistema identificati e separati;
7. Unicode ed emoji preservati;
8. URL e nomi contenenti ':' gestiti correttamente;
9. Archivio ZIP con transcript estratto in memoria;
10. Archivio ZIP con allegati multimediali collegati;
11. Archivio ZIP ambiguo rifiutato con errore controllato;
12. Protezione anti-path traversal ('..') su archivio ZIP;
13. Protezione anti-zip bomb su archivio ZIP;
14. File TXT generico non transcript rifiutato;
15. Archivio ZIP privo di transcript rifiutato;
16. Pipeline integrata TXT/ZIP -> ConversationEvidenceDocument -> SearchService;
17. AppTest Streamlit: 'WhatsApp export chat (TXT / ZIP)' compare nell'uploader;
18. AppTest Streamlit: file uploader accetta estensioni .txt e .zip;
19. Zero chiamate AI durante l'intero ciclo di ingestion.

SEZIONE LM STUDIO NATIVE V1 E AUTO-LOAD (20-39):
20. Parsing modelli nativi v1 (GET /api/v1/models);
21. Modello già caricato -> nessun nuovo load POST;
22. Modello installato ma non caricato -> esecuzione POST /api/v1/models/load;
23. Payload load contiene 'model';
24. Payload load contiene 'context_length';
25. Payload load NON contiene 'identifier';
26. Payload load NON contiene 'contextLength';
27. Payload load NON contiene 'gpu_offload';
28. Payload load NON contiene 'gpuOffload';
29. Modello assente -> AiModelNotInstalledError controllato;
30. Modelli ambigui -> AiModelAmbiguousError senza selezione arbitraria;
31. Server LM Studio offline -> LmStudioUnavailableError controllato;
32. HTTP 400 su load -> AiModelLoadError con dettagli tecnici preservati;
33. Timeout durante load -> AiBackendTimeoutError;
34. Timeout durante inferenza -> AiBackendTimeoutError;
35. model_id inferenza corrisponde realmente all'istanza caricata;
36. Timeout operativo configurato a 240 secondi;
37. Nessun download automatico da Internet;
38. Rifiuto connessioni a endpoint esterni (loopback-only security);
39. Ingestion reale continua a non invocare client LLM.

SEZIONE SISTEMA / STATO (40-43):
40. Nessun riferimento ad AMD A8-7410 hardcoded nella UI o diagnostica;
41. Nessun 'Hardware Block' a schermo nella pagina Sistema/Stato;
42. Nessun 'Rinvio Benchmark' a schermo;
43. Stato benchmark neutro e portabile esposto dall'application layer.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any
import urllib.error
import urllib.request
import zipfile

import pytest
from streamlit.testing.v1 import AppTest

from ai.backend import (
    AiBackendRequestError,
    AiBackendTimeoutError,
    AiModelAmbiguousError,
    AiModelLoadError,
    AiModelNotInstalledError,
    AiModelNotSpecifiedError,
    BaseLlmClient,
    FakeLocalLlmClient,
    LlmCompletionResponse,
    LmStudioUnavailableError,
)
from ai.lmstudio import (
    DEFAULT_OPERATIONAL_CONTEXT_LENGTH,
    DEFAULT_OPERATIONAL_MAX_TOKENS,
    DEFAULT_OPERATIONAL_MODEL,
    DEFAULT_OPERATIONAL_TEMPERATURE,
    DEFAULT_OPERATIONAL_TIMEOUT_SECONDS,
    LmStudioClient,
    LocalModelMetadata,
    get_model_display_name,
    resolve_installed_model_id,
    resolve_operational_model_from_metadata,
    resolve_qwen_operational_model,
)
from ai.models import (
    ConversationEvidenceDocument,
    TopicDecision,
    TopicDetectionResult,
    TopicQuery,
)
from importer.whatsapp_export import WhatsAppExportImporter
from search.models import EvidenceSearchQuery
from search.service import SearchService
from ui.application import (
    build_search_service_for_conversation,
    build_topic_query,
    execute_manual_topic_detection,
    execute_real_ingestion,
    get_system_status_info,
    prepare_operational_model,
)
from ui.demo import build_synthetic_demo_dataset
from ui.ingestion import get_importer_for_format
from ui.models import (
    IngestionRequest,
    IngestionStatus,
    SourceFormat,
)

APP_PATH = str(Path(__file__).resolve().parents[2] / "app.py")


def _get_test_doc() -> ConversationEvidenceDocument:
    doc, _, _ = build_synthetic_demo_dataset()
    return doc


# =============================================================================
# SEZIONE WHATSAPP EXPORT (1 - 19)
# =============================================================================

def test_01_source_format_whatsapp_export_exists():
    assert hasattr(SourceFormat, "WHATSAPP_EXPORT")
    assert SourceFormat.WHATSAPP_EXPORT.value == "WhatsApp export chat (TXT / ZIP)"


def test_02_dispatch_returns_whatsapp_export_importer():
    imp = get_importer_for_format(SourceFormat.WHATSAPP_EXPORT)
    assert isinstance(imp, WhatsAppExportImporter)
    assert imp.source_name == "whatsapp_export"


def test_03_txt_android_parsing(tmp_path: Path):
    txt_file = tmp_path / "chat_android.txt"
    txt_file.write_text(
        "12/09/2026, 14:35 - Mario Rossi: Ciao, come stai?\n"
        "12/09/2026, 14:36 - Luca Bianchi: Tutto bene!\n",
        encoding="utf-8",
    )
    imp = WhatsAppExportImporter()
    assert imp.can_import(txt_file) is True
    records = list(imp.import_records(txt_file))
    assert len(records) == 2
    assert records[0].raw_fields["sender"] == "Mario Rossi"
    assert records[0].raw_fields["text"] == "Ciao, come stai?"
    assert records[1].raw_fields["sender"] == "Luca Bianchi"


def test_04_txt_ios_parsing(tmp_path: Path):
    txt_file = tmp_path / "chat_ios.txt"
    txt_file.write_text(
        "[12/09/2026, 14:35:10] Mario Rossi: Saluti da iOS\n"
        "[12/09/2026, 14:36:00] Luca Bianchi: Ricevuto da iPhone\n",
        encoding="utf-8",
    )
    imp = WhatsAppExportImporter()
    assert imp.can_import(txt_file) is True
    records = list(imp.import_records(txt_file))
    assert len(records) == 2
    assert records[0].raw_fields["sender"] == "Mario Rossi"
    assert records[0].raw_fields["text"] == "Saluti da iOS"


def test_05_multiline_messages_preserved(tmp_path: Path):
    txt_file = tmp_path / "chat_multiline.txt"
    txt_file.write_text(
        "12/09/2026, 14:35 - Mario Rossi: Prima riga\nSeconda riga\nTerza riga\n"
        "12/09/2026, 14:36 - Luca: Ok\n",
        encoding="utf-8",
    )
    imp = WhatsAppExportImporter()
    records = list(imp.import_records(txt_file))
    assert len(records) == 2
    assert records[0].raw_fields["text"] == "Prima riga\nSeconda riga\nTerza riga"


def test_06_system_messages_identified(tmp_path: Path):
    txt_file = tmp_path / "chat_system.txt"
    txt_file.write_text(
        "12/09/2026, 14:30 - I messaggi e le chiamate sono crittografati end-to-end.\n"
        "12/09/2026, 14:35 - Mario: Ciao\n",
        encoding="utf-8",
    )
    imp = WhatsAppExportImporter()
    records = list(imp.import_records(txt_file))
    assert len(records) == 2
    assert records[0].raw_fields["is_system"] is True
    assert records[0].raw_fields["sender"] is None
    assert records[1].raw_fields["is_system"] is False


def test_07_unicode_and_emojis_preserved(tmp_path: Path):
    txt_file = tmp_path / "chat_emoji.txt"
    txt_file.write_text(
        "12/09/2026, 14:35 - Mario: Ciao! 🔍 🎉 🇮🇹\n",
        encoding="utf-8",
    )
    imp = WhatsAppExportImporter()
    records = list(imp.import_records(txt_file))
    assert len(records) == 1
    assert "🔍 🎉 🇮🇹" in records[0].raw_fields["text"]


def test_08_url_and_colon_in_sender_or_text(tmp_path: Path):
    txt_file = tmp_path / "chat_url.txt"
    txt_file.write_text(
        "12/09/2026, 14:35 - Mario: Visita http://127.0.0.1:8080/test\n"
        "12/09/2026, 14:36 - Rossi:Mario: Confermo ricezione\n",
        encoding="utf-8",
    )
    imp = WhatsAppExportImporter()
    records = list(imp.import_records(txt_file))
    assert len(records) == 2
    assert records[0].raw_fields["text"] == "Visita http://127.0.0.1:8080/test"
    assert records[1].raw_fields["sender"] == "Rossi:Mario"


def test_09_zip_transcript_in_memory(tmp_path: Path):
    zip_path = tmp_path / "export.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("_chat.txt", "12/09/2026, 14:35 - Mario: Da zip\n12/09/2026, 14:36 - Luca: Ok\n")

    imp = WhatsAppExportImporter()
    assert imp.can_import(zip_path) is True
    records = list(imp.import_records(zip_path))
    assert len(records) == 2
    assert records[0].raw_fields["text"] == "Da zip"


def test_10_zip_media_references(tmp_path: Path):
    zip_path = tmp_path / "export_media.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("_chat.txt", "12/09/2026, 14:35 - Mario: <allegato: photo.jpg>\n")
        zf.writestr("photo.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 20)

    imp = WhatsAppExportImporter()
    records = list(imp.import_records(zip_path))
    assert len(records) == 1
    assert records[0].media_reference is not None
    assert "photo.jpg" in records[0].media_reference


def test_11_zip_ambiguous_transcript_rejected(tmp_path: Path):
    zip_path = tmp_path / "ambiguous.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("chat1.txt", "12/09/2026, 14:35 - Mario: Primo\n12/09/2026, 14:36 - Luca: Ok\n")
        zf.writestr("chat2.txt", "12/09/2026, 14:35 - Mario: Secondo\n12/09/2026, 14:36 - Luca: Ok\n")

    imp = WhatsAppExportImporter()
    assert imp.can_import(zip_path) is False
    with pytest.raises(ValueError, match="Ambiguità"):
        list(imp.import_records(zip_path))


def test_12_zip_path_traversal_protection(tmp_path: Path):
    zip_path = tmp_path / "traversal.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("../evil.txt", "attack")

    imp = WhatsAppExportImporter()
    assert imp.can_import(zip_path) is False
    with pytest.raises(ValueError, match="path traversal"):
        list(imp.import_records(zip_path))


def test_13_zip_bomb_protection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import importer.whatsapp_export as we_module
    monkeypatch.setattr(we_module, "_MAX_ZIP_ENTRIES", 2)

    zip_path = tmp_path / "bomb.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("1.txt", "a")
        zf.writestr("2.txt", "b")
        zf.writestr("3.txt", "c")

    imp = WhatsAppExportImporter()
    assert imp.can_import(zip_path) is False
    with pytest.raises(ValueError, match="membri"):
        list(imp.import_records(zip_path))


def test_14_generic_txt_rejected(tmp_path: Path):
    txt_file = tmp_path / "generic.txt"
    txt_file.write_text("Questo è un file di testo non correlato a WhatsApp.\nNessun timestamp.\n", encoding="utf-8")
    imp = WhatsAppExportImporter()
    assert imp.can_import(txt_file) is False


def test_15_generic_zip_rejected(tmp_path: Path):
    zip_path = tmp_path / "no_txt.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("data.csv", "id,val\n1,2\n")
    imp = WhatsAppExportImporter()
    assert imp.can_import(zip_path) is False


def test_16_integration_pipeline_txt_to_search_service():
    chat_content = (
        "10/05/2024, 15:00 - Alice: Incontro fissato per la transazione\n"
        "10/05/2024, 15:01 - Bob: Ricevuto, ci vediamo lì.\n"
    ).encode("utf-8")
    req = IngestionRequest(
        source_format=SourceFormat.WHATSAPP_EXPORT,
        filename="export.txt",
        file_bytes=chat_content,
    )
    res = execute_real_ingestion(req)
    assert res.status == IngestionStatus.SUCCESS
    assert len(res.documents) == 1
    doc = next(iter(res.documents.values()))
    service = build_search_service_for_conversation(doc)
    results = service.search_evidence(EvidenceSearchQuery(query_text="transazione"))
    assert results.total_hits >= 1


def test_17_streamlit_apptest_whatsapp_export_displayed():
    at = AppTest.from_file(APP_PATH, default_timeout=15)
    at.run()
    at.sidebar.radio[0].set_value("2. Importazione")
    at.run()
    assert not at.exception
    sb = at.selectbox[0]
    assert "WhatsApp export chat (TXT / ZIP)" in sb.options


def test_18_streamlit_apptest_uploader_accepts_txt_and_zip():
    at = AppTest.from_file(APP_PATH, default_timeout=15)
    at.run()
    at.sidebar.radio[0].set_value("2. Importazione")
    at.run()
    at.selectbox[0].set_value("WhatsApp export chat (TXT / ZIP)")
    at.run()
    assert not at.exception
    uploader = at.file_uploader[0]
    accepted = [t.lstrip(".") for t in uploader.proto.type]
    assert "txt" in accepted
    assert "zip" in accepted


def test_19_zero_ai_during_ingestion(monkeypatch: pytest.MonkeyPatch):
    import ai.lmstudio

    def fail_if_called(*args, **kwargs):
        pytest.fail("LM Studio invocato illegittimamente durante l'ingestion!")

    monkeypatch.setattr(ai.lmstudio.LmStudioClient, "chat_completion", fail_if_called)

    chat_content = (
        "10/05/2024, 15:00 - Alice: Test offline\n"
        "10/05/2024, 15:01 - Bob: Nessuna AI durante upload\n"
    ).encode("utf-8")
    req = IngestionRequest(
        source_format=SourceFormat.WHATSAPP_EXPORT,
        filename="chat.txt",
        file_bytes=chat_content,
    )
    res = execute_real_ingestion(req)
    assert res.status == IngestionStatus.SUCCESS


# =============================================================================
# SEZIONE LM STUDIO NATIVE V1 E AUTO-LOAD (20 - 39)
# =============================================================================

def test_20_get_api_v1_models_parsing():
    raw_json = {
        "data": [
            {
                "key": "qwen2.5-7b-instruct",
                "display_name": "Qwen 2.5 7B Instruct",
                "quantization": "Q4_K_M",
                "loaded_instances": [{"id": "inst-1", "context_length": 8192}],
                "max_context_length": 32768,
            }
        ]
    }
    class MockResp:
        def __init__(self):
            self.status = 200
        def read(self):
            return json.dumps(raw_json).encode("utf-8")
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    client = LmStudioClient()
    client._get_headers = lambda: {}
    import urllib.request
    orig_urlopen = urllib.request.urlopen

    try:
        urllib.request.urlopen = lambda req, timeout=10.0: MockResp()
        models = client.get_native_models()
        assert len(models) == 1
        assert models[0].key == "qwen2.5-7b-instruct"
        assert models[0].display_name == "Qwen 2.5 7B Instruct"
        assert models[0].quantization == "Q4_K_M"
        assert len(models[0].loaded_instances) == 1
    finally:
        urllib.request.urlopen = orig_urlopen


def test_21_model_already_loaded_skips_post_load(monkeypatch: pytest.MonkeyPatch):
    post_calls = []

    def mock_urlopen(req, timeout=10.0):
        url = req.full_url
        if req.get_method() == "POST" and "/load" in url:
            post_calls.append(url)
            raise AssertionError("POST /load non doveva essere invocato per modello già caricato!")
        if "/api/v1/models" in url:
            class NativeResp:
                def __init__(self): self.status = 200
                def read(self):
                    return json.dumps({
                        "data": [{
                            "key": "qwen2.5-7b-instruct",
                            "display_name": "Qwen 2.5 7B Instruct",
                            "loaded_instances": [{"id": "inst-ready"}],
                        }]
                    }).encode("utf-8")
                def __enter__(self): return self
                def __exit__(self, *args): pass
            return NativeResp()
        if "/v1/models" in url:
            class V1Resp:
                def __init__(self): self.status = 200
                def read(self): return json.dumps({"data": [{"id": "qwen2.5-7b-instruct"}]}).encode("utf-8")
                def __enter__(self): return self
                def __exit__(self, *args): pass
            return V1Resp()
        raise ValueError(f"URL non atteso: {url}")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()
    resolved = client.ensure_model_loaded("qwen2.5-7b-instruct")
    assert resolved == "inst-ready"
    assert len(post_calls) == 0


def test_22_to_28_model_unloaded_sends_post_load_with_exact_payload(monkeypatch: pytest.MonkeyPatch):
    posted_payloads = []

    def mock_urlopen(req, timeout=10.0):
        url = req.full_url
        if req.get_method() == "POST" and "/api/v1/models/load" in url:
            body = json.loads(req.data.decode("utf-8"))
            posted_payloads.append(body)
            class LoadResp:
                def __init__(self): self.status = 200
                def read(self):
                    return json.dumps({
                        "status": "loaded",
                        "instance_id": "inst-loaded-123",
                        "model": "qwen2.5-7b-instruct",
                    }).encode("utf-8")
                def __enter__(self): return self
                def __exit__(self, *args): pass
            return LoadResp()
        if "/api/v1/models" in url:
            class NativeResp:
                def __init__(self): self.status = 200
                def read(self):
                    return json.dumps({
                        "data": [{
                            "key": "qwen2.5-7b-instruct",
                            "display_name": "Qwen 2.5 7B Instruct",
                            "loaded_instances": [],
                        }]
                    }).encode("utf-8")
                def __enter__(self): return self
                def __exit__(self, *args): pass
            return NativeResp()
        if "/v1/models" in url:
            class V1Resp:
                def __init__(self): self.status = 200
                def read(self): return json.dumps({"data": [{"id": "qwen2.5-7b-instruct"}]}).encode("utf-8")
                def __enter__(self): return self
                def __exit__(self, *args): pass
            return V1Resp()
        raise ValueError(f"URL non atteso: {url}")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()
    resolved = client.ensure_model_loaded("qwen2.5-7b-instruct", context_length=8192)

    # 22. POST eseguito
    assert len(posted_payloads) == 1
    p = posted_payloads[0]

    # 23. Payload usa 'model'
    assert p["model"] == "qwen2.5-7b-instruct"
    # 24. Payload usa 'context_length'
    assert p["context_length"] == 8192
    assert p["echo_load_config"] is True

    # 25. NON contiene 'identifier'
    assert "identifier" not in p
    # 26. NON contiene 'contextLength'
    assert "contextLength" not in p
    # 27. NON contiene 'gpu_offload'
    assert "gpu_offload" not in p
    # 28. NON contiene 'gpuOffload'
    assert "gpuOffload" not in p

    assert resolved == "inst-loaded-123"


def test_29_model_absent_raises_controlled_error(monkeypatch: pytest.MonkeyPatch):
    def mock_urlopen(req, timeout=10.0):
        class NativeResp:
            def __init__(self): self.status = 200
            def read(self): return json.dumps({"data": [{"key": "other-model"}]}).encode("utf-8")
            def __enter__(self): return self
            def __exit__(self, *args): pass
        return NativeResp()

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()
    with pytest.raises(AiModelNotInstalledError) as exc_info:
        client.ensure_model_loaded("qwen2.5-7b-instruct")
    assert "Qwen2.5-7B-Instruct non è installato" in str(exc_info.value)


def test_30_ambiguous_models_raises_controlled_error():
    models = [
        LocalModelMetadata(key="qwen2.5-7b-instruct-v1", display_name="Qwen 2.5 7B v1", quantization="Q4_K_M"),
        LocalModelMetadata(key="qwen2.5-7b-instruct-v2", display_name="Qwen 2.5 7B v2", quantization="Q4_K_M"),
    ]
    with pytest.raises(AiModelAmbiguousError) as exc_info:
        resolve_qwen_operational_model(models)
    assert "Rilevateplici versioni compatibili" in str(exc_info.value)


def test_31_server_offline_raises_controlled_error(monkeypatch: pytest.MonkeyPatch):
    def mock_urlopen(req, timeout=1.5):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()
    with pytest.raises(LmStudioUnavailableError) as exc_info:
        client.ensure_model_loaded("qwen2.5-7b-instruct")
    assert "non raggiungibile" in str(exc_info.value).lower()


def test_32_http_400_during_load_captured(monkeypatch: pytest.MonkeyPatch):
    def mock_urlopen(req, timeout=10.0):
        url = req.full_url
        if req.get_method() == "POST" and "/load" in url:
            fp = io.BytesIO(b'{"error": "Unrecognized key(s) in object"}')
            raise urllib.error.HTTPError(url, 400, "Bad Request", {}, fp)
        if "/api/v1/models" in url:
            class NativeResp:
                def __init__(self): self.status = 200
                def read(self):
                    return json.dumps({"data": [{"key": "qwen2.5-7b-instruct", "loaded_instances": []}]}).encode("utf-8")
                def __enter__(self): return self
                def __exit__(self, *args): pass
            return NativeResp()
        if "/v1/models" in url:
            class V1Resp:
                def __init__(self): self.status = 200
                def read(self): return json.dumps({"data": [{"id": "qwen2.5-7b-instruct"}]}).encode("utf-8")
                def __enter__(self): return self
                def __exit__(self, *args): pass
            return V1Resp()
        raise ValueError(f"URL: {url}")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()
    with pytest.raises(AiModelLoadError) as exc_info:
        client.ensure_model_loaded("qwen2.5-7b-instruct")
    assert exc_info.value.status_code == 400
    assert "Unrecognized key(s)" in exc_info.value.technical_details


def test_33_load_timeout_raises_ai_backend_timeout_error(monkeypatch: pytest.MonkeyPatch):
    import socket
    def mock_urlopen(req, timeout=10.0):
        url = req.full_url
        if req.get_method() == "POST" and "/load" in url:
            raise urllib.error.URLError(socket.timeout("The read operation timed out"))
        class NativeResp:
            def __init__(self): self.status = 200
            def read(self): return json.dumps({"data": [{"key": "qwen2.5-7b-instruct", "loaded_instances": []}]}).encode("utf-8")
            def __enter__(self): return self
            def __exit__(self, *args): pass
        return NativeResp()

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()
    with pytest.raises(AiBackendTimeoutError) as exc_info:
        client.load_model("qwen2.5-7b-instruct", timeout_seconds=240.0)
    assert "Timeout" in str(exc_info.value)


def test_34_inference_timeout_raises_ai_backend_timeout_error(monkeypatch: pytest.MonkeyPatch):
    import socket
    def mock_urlopen(req, timeout=10.0):
        raise urllib.error.URLError(socket.timeout("The read operation timed out"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient(model_id="qwen2.5-7b-instruct")
    with pytest.raises(AiBackendTimeoutError) as exc_info:
        client.chat_completion(messages=[{"role": "user", "content": "test"}], timeout_seconds=240.0)
    assert "Timeout" in str(exc_info.value)


def test_35_model_id_inference_matches_loaded_instance(monkeypatch: pytest.MonkeyPatch):
    captured_chat_model = []
    doc = _get_test_doc()
    ev_id = doc.all_evidence_sections[0].evidence_id

    def mock_urlopen(req, timeout=10.0):
        url = req.full_url
        if "/chat/completions" in url:
            body = json.loads(req.data.decode("utf-8"))
            captured_chat_model.append(body["model"])
            class ChatResp:
                def __init__(self): self.status = 200
                def read(self):
                    return json.dumps({
                        "id": "c1",
                        "model": body["model"],
                        "choices": [{"index": 0, "message": {"content": json.dumps({"decision": "PRESENT", "rationale": "OK", "evidence_ids": [ev_id]})}, "finish_reason": "stop"}],
                        "usage": {"total_tokens": 10},
                    }).encode("utf-8")
                def __enter__(self): return self
                def __exit__(self, *args): pass
            return ChatResp()
        if "/api/v1/models/load" in url:
            class LoadResp:
                def __init__(self): self.status = 200
                def read(self):
                    return json.dumps({"status": "loaded", "instance_id": "real-instance-uuid-42"}).encode("utf-8")
                def __enter__(self): return self
                def __exit__(self, *args): pass
            return LoadResp()
        if "/api/v1/models" in url:
            class NativeResp:
                def __init__(self): self.status = 200
                def read(self): return json.dumps({"data": [{"key": "qwen2.5-7b-instruct", "loaded_instances": []}]}).encode("utf-8")
                def __enter__(self): return self
                def __exit__(self, *args): pass
            return NativeResp()
        if "/v1/models" in url:
            class V1Resp:
                def __init__(self): self.status = 200
                def read(self): return json.dumps({"data": [{"id": "qwen2.5-7b-instruct"}]}).encode("utf-8")
                def __enter__(self): return self
                def __exit__(self, *args): pass
            return V1Resp()
        raise ValueError(f"URL: {url}")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    client = LmStudioClient()
    doc = _get_test_doc()
    topic = build_topic_query("Argomento test")

    res = execute_manual_topic_detection(document=doc, topic=topic, client=client, timeout_seconds=240.0)
    assert res.metadata["model"] == "real-instance-uuid-42"
    assert captured_chat_model[0] == "real-instance-uuid-42"


def test_36_operational_timeout_configuration_is_240s():
    assert DEFAULT_OPERATIONAL_TIMEOUT_SECONDS == 240.0


def test_37_no_automatic_downloads_local_only():
    available = ("qwen2.5-7b-instruct",)
    assert resolve_installed_model_id("qwen2.5-7b-instruct", available) == "qwen2.5-7b-instruct"
    assert resolve_installed_model_id("uninstalled-model", available) is None


def test_38_no_external_endpoints_loopback_only():
    with pytest.raises(ValueError, match="esclusivamente host loopback"):
        LmStudioClient(base_url="http://192.168.1.50:1234")
    with pytest.raises(ValueError, match="esclusivamente host loopback"):
        LmStudioClient(base_url="https://api.openai.com/v1")


def test_39_ingestion_continues_zero_ai_invocations():
    chat_text = (
        "10/05/2024, 15:00 - Alice: Ciao, verifichiamo la perizia?\n"
        "10/05/2024, 15:01 - Bob: Ricevuto, procedo con la validazione hash.\n"
    ).encode("utf-8")
    req = IngestionRequest(
        source_format=SourceFormat.WHATSAPP_EXPORT,
        filename="_chat.txt",
        file_bytes=chat_text,
    )
    res = execute_real_ingestion(req)
    assert res.status == IngestionStatus.SUCCESS
    assert len(res.documents) == 1


# =============================================================================
# SEZIONE SISTEMA / STATO (40 - 43)
# =============================================================================

def test_40_no_amd_a8_hardcoded_in_system_status():
    status = get_system_status_info("1.63.0")
    for key, val in status.items():
        assert "AMD A8-7410" not in str(val)
        assert "DEFERRED" not in str(val)


def test_41_no_hardware_block_in_system_status_page():
    at = AppTest.from_file(APP_PATH, default_timeout=15)
    at.run()
    at.sidebar.radio[0].set_value("6. Sistema / Stato")
    at.run()
    assert not at.exception
    subheaders = [sh.value for sh in at.subheader]
    assert "Nota Rinvio Benchmark LM Studio (Hardware Block)" not in subheaders
    assert "LM Studio / Benchmark" in subheaders


def test_42_no_rinvio_benchmark_in_system_status_page():
    at = AppTest.from_file(APP_PATH, default_timeout=15)
    at.run()
    at.sidebar.radio[0].set_value("6. Sistema / Stato")
    at.run()
    all_text = " ".join([m.value for m in at.markdown] + [i.value for i in at.info] + [w.value for w in at.warning])
    assert "Nota Rinvio Benchmark" not in all_text
    assert "AMD A8-7410" not in all_text


def test_43_portable_system_status_contract():
    status = get_system_status_info("1.63.0")
    assert status["search_layer"] == "READY"
    assert status["local_ai_architecture"] == "READY - LOCAL LM STUDIO"
    assert "EXTERNAL" in status["real_lm_studio_benchmark"] or "BENCHMARK RUNNER" in status["real_lm_studio_benchmark"]
    assert "WhatsApp msgstore" in status["real_file_ingestion"]
    assert "TXT/ZIP" in status["real_file_ingestion"]
    assert "LOCAL ONLY / LOOPBACK FOR LM STUDIO" in status["network_status"]
