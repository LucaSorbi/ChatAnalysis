"""
tests/unit/test_ui_smoke.py
---------------------------
Test smoke e headless per l'applicazione Streamlit (app.py) e i componenti UI.

Verifiche:
1. Entrypoint importabile senza errori;
2. Esecuzione headless iniziale;
3. Navigazione tra tutte le pagine principali;
4. Rendering REAL FILE riuscito con selezione conversazione (Gate G.2);
5. Panoramica con label pseudonimizzata e assenza chat_id grezzo nei top-level input (Gate F, G.8);
6. Assenza di richieste 'message_count' su codice UI (Gate A, G.3);
7. Gestione sicura degli errori senza AttributeError né leak di dati sensibili (Gate B, C, G.4, G.5, G.6);
8. Gestione Real File in Analisi Topic senza esecuzione AI.
"""
from __future__ import annotations

import ast
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest

from ai.models import ConversationEvidenceDocument
from multimodal.evidence import MessageEvidenceBundle
from ui import presentation
from ui.demo import _create_synthetic_message
from ui.ingestion import (
    IngestionError,
    InvalidUploadedFileError,
    PipelineStageError,
    UnsupportedSourceFormatError,
)
from ui.models import (
    DatasetMode,
    ImportedConversationInfo,
    IngestionResult,
    IngestionStatus,
    IngestionSummary,
    SourceFormat,
)

APP_PATH = str(Path(__file__).resolve().parents[2] / "app.py")


def _build_test_real_file_result() -> IngestionResult:
    msg1 = _create_synthetic_message("msg1", "1", "Ciao Mondo", "msgstore_db")
    msg2 = _create_synthetic_message("msg2", "2", "Come stai?", "msgstore_db")
    doc1 = ConversationEvidenceDocument(
        document_id="doc::msgstore_db::abcdef123456::conv1",
        bundles=(MessageEvidenceBundle(message=msg1),),
        chat_id="393330000001@s.whatsapp.net",
        source_name="msgstore_db",
        metadata={"sha256": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"},
    )
    doc2 = ConversationEvidenceDocument(
        document_id="doc::msgstore_db::abcdef123456::conv2",
        bundles=(MessageEvidenceBundle(message=msg2),),
        chat_id="393330000002@s.whatsapp.net",
        source_name="msgstore_db",
        metadata={"sha256": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"},
    )
    info1 = ImportedConversationInfo(
        document_id=doc1.document_id,
        chat_id=doc1.chat_id,
        display_label="Conversazione 1 — 1 messaggi — [a1b2c3d4]",
        bundle_count=1,
        section_count=1,
        languages=("it",),
        source_name="msgstore_db",
    )
    info2 = ImportedConversationInfo(
        document_id=doc2.document_id,
        chat_id=doc2.chat_id,
        display_label="Conversazione 2 — 1 messaggi — [e5f6g7h8]",
        bundle_count=1,
        section_count=1,
        languages=("it",),
        source_name="msgstore_db",
    )
    summary = IngestionSummary(
        source_format=SourceFormat.WHATSAPP_MSGSTORE,
        original_filename="msgstore.db",
        sha256="abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
        file_size_bytes=2048,
        raw_record_count=2,
        validation_issue_count=0,
        normalized_record_count=2,
        unified_message_count=2,
        conversation_count=2,
        auxiliary_record_count=0,
        warnings=(),
        available_conversations=(info1, info2),
        selected_document_id=doc1.document_id,
        companion_filename=None,
        companion_sha256=None,
        status=IngestionStatus.SUCCESS,
    )
    return IngestionResult(
        summary=summary,
        documents={doc1.document_id: doc1, doc2.document_id: doc2},
    )


@pytest.mark.unit
class TestUiSmoke:

    def test_app_entrypoint_importable(self):
        """Verifica che app.py sia importabile direttamente senza crash."""
        import app
        assert app is not None

    def test_app_headless_initial_run(self):
        """Verifica l'esecuzione headless iniziale dell'applicazione Streamlit."""
        at = AppTest.from_file(APP_PATH, default_timeout=15)
        at.run()
        assert not at.exception
        assert len(at.sidebar.radio) >= 1
        assert at.sidebar.radio[0].value == "1. Panoramica"

    def test_app_headless_navigation(self):
        """Verifica la navigazione tra le sezioni principali dell'app."""
        at = AppTest.from_file(APP_PATH, default_timeout=15)
        at.run()
        assert not at.exception

        pages = [
            "1. Panoramica",
            "2. Importazione",
            "3. Esplora conversazione",
            "4. Ricerca",
            "5. Analisi topic",
            "6. Sistema / Stato",
        ]

        for p in pages:
            at.sidebar.radio[0].set_value(p)
            at.run()
            assert not at.exception, f"Eccezione rilevata durante la navigazione su {p}"

    def test_import_page_shows_real_mode_options(self):
        """Verifica la presenza dei controlli di importazione reale e il comportamento senza file."""
        at = AppTest.from_file(APP_PATH, default_timeout=15)
        at.run()
        at.sidebar.radio[0].set_value("2. Importazione")
        at.run()
        assert not at.exception

        import_buttons = [b for b in at.button if "Importa e analizza struttura" in b.label]
        assert len(import_buttons) == 1
        import_buttons[0].click()
        at.run()
        assert not at.exception
        assert len(at.warning) >= 1
        assert "Selezionare un file primario" in at.warning[0].value

    def test_topics_page_in_real_file_mode_without_ai(self):
        """Verifica che la pagina topic gestisca correttamente l'assenza di risultati AI in modalità REAL FILE."""
        at = AppTest.from_file(APP_PATH, default_timeout=15)
        at.run()

        at.session_state["dataset_loaded"] = True
        at.session_state["dataset_mode"] = DatasetMode.FILE
        at.session_state["detection_results"] = ()
        at.session_state["discovery_results"] = ()

        at.sidebar.radio[0].set_value("5. Analisi topic")
        at.run()
        assert not at.exception
        assert len(at.info) >= 1
        assert "differito alla fase sperimentale finale" in at.info[0].value

    def test_real_file_rendering_success_with_conversations_selection(self):
        """Verifica rendering REAL FILE con IngestionResult SUCCESS, 2 conversazioni e selectbox attiva (Gate G.2)."""
        res = _build_test_real_file_result()
        from ui import state as ui_state

        at = AppTest.from_file(APP_PATH, default_timeout=15)
        at.run()

        # Iniezione stato REAL FILE tramite setter autoritativo
        ui_state.set_real_ingestion_result(res, state=at.session_state)

        at.sidebar.radio[0].set_value("2. Importazione")
        at.run()

        assert not at.exception, f"Eccezione rilevata in Importazione: {at.exception}"

        # Verifica selectbox conversazione presente
        selectboxes = [sb for sb in at.selectbox if sb.key == "conv_selectbox"]
        assert len(selectboxes) == 1, "La selectbox di selezione conversazione deve apparire"
        sb = selectboxes[0]
        assert len(sb.options) == 2
        # Streamlit AppTest restituisce le opzioni formattate da format_func
        assert sb.options[0] == res.summary.conversations[0].display_label
        assert sb.options[1] == res.summary.conversations[1].display_label

        # Verifica click su pulsante Apri conversazione
        open_btns = [b for b in at.button if b.key == "open_conv_btn"]
        assert len(open_btns) == 1
        open_btns[0].click()
        at.run()
        assert not at.exception

    def test_real_file_panoramica_pseudonymized_label_and_no_raw_chat_id_in_top_inputs(self):
        """Verifica che la Panoramica mostri la label pseudonimizzata e NON mostri il chat_id grezzo nei top text_input (Gate F, G.8)."""
        res = _build_test_real_file_result()
        from ui import state as ui_state

        at = AppTest.from_file(APP_PATH, default_timeout=15)
        at.run()

        ui_state.set_real_ingestion_result(res, state=at.session_state)

        at.sidebar.radio[0].set_value("1. Panoramica")
        at.run()

        assert not at.exception

        # I text_input visibili nei metadati principali
        text_inputs = at.text_input
        values = [ti.value for ti in text_inputs]

        # Nessun valore deve contenere il JID raw (chat_id)
        for v in values:
            assert "393330000001@s.whatsapp.net" not in v, "JID grezzo non deve apparire nei top-level text_input"
            assert "393330000002@s.whatsapp.net" not in v

        # Deve essere presente la label pseudonimizzata
        conv_inputs = [ti for ti in text_inputs if ti.label == "Conversazione"]
        assert len(conv_inputs) == 1
        assert conv_inputs[0].value == "Conversazione 1 — 1 messaggi — [a1b2c3d4]"

    def test_no_ui_code_requires_message_count(self):
        """Verifica sintattica/AST che nessun file in ui/ richieda 'message_count' su ImportedConversationInfo (Gate G.3)."""
        ui_dir = Path(__file__).resolve().parents[2] / "ui"
        for py_file in ui_dir.glob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "message_count":
                    # In ui/, l'uso di message_count su oggetti di conversazione/presentation è vietato
                    pytest.fail(f"Trovato uso non consentito di 'message_count' in {py_file.name}:{node.lineno}")

    def test_invalid_uploaded_file_error_rendering_in_ui(self, monkeypatch):
        """Verifica gestione controllata di InvalidUploadedFileError nella UI senza AttributeError (Gate B, G.4)."""
        at = AppTest.from_file(APP_PATH, default_timeout=15)
        at.run()
        at.sidebar.radio[0].set_value("2. Importazione")
        at.run()

        at.file_uploader[0].upload("invalid.db", b"bad_content")

        def mock_failing_ingestion(req):
            raise InvalidUploadedFileError("Il file fornito non possiede una firma valida di database SQLite 3.")

        monkeypatch.setattr("ui.presentation.execute_real_ingestion", mock_failing_ingestion)

        import_btns = [b for b in at.button if "Importa e analizza struttura" in b.label]
        import_btns[0].click()
        at.run()

        assert not at.exception, "Nessun AttributeError o eccezione non gestita"
        assert len(at.error) >= 1
        err_text = at.error[0].value
        assert "InvalidUploadedFileError" in err_text
        assert "firma valida di database SQLite 3" in err_text

    def test_unsupported_source_format_error_rendering_in_ui(self, monkeypatch):
        """Verifica gestione controllata di UnsupportedSourceFormatError nella UI (Gate B, G.5)."""
        at = AppTest.from_file(APP_PATH, default_timeout=15)
        at.run()
        at.sidebar.radio[0].set_value("2. Importazione")
        at.run()

        at.file_uploader[0].upload("unsupported.db", b"some_bytes")

        def mock_unsupported_ingestion(req):
            raise UnsupportedSourceFormatError("Formato sorgente non supportato: CUSTOM_FORMAT")

        monkeypatch.setattr("ui.presentation.execute_real_ingestion", mock_unsupported_ingestion)

        import_btns = [b for b in at.button if "Importa e analizza struttura" in b.label]
        import_btns[0].click()
        at.run()

        assert not at.exception
        assert len(at.error) >= 1
        err_text = at.error[0].value
        assert "UnsupportedSourceFormatError" in err_text
        assert "Formato sorgente non supportato" in err_text

    def test_pipeline_stage_error_rendering_in_ui_no_raw_message_leakage(self, monkeypatch):
        """Verifica gestione controllata di PipelineStageError senza esposizione di raw message/path (Gate B, C, G.6)."""
        at = AppTest.from_file(APP_PATH, default_timeout=15)
        at.run()
        at.sidebar.radio[0].set_value("2. Importazione")
        at.run()

        at.file_uploader[0].upload("corrupted.db", b"sqlite_data")

        # Causa originale che conterrebbe dettagli interni/percorsi sensibili
        raw_cause = RuntimeError("Internal sqlite3.OperationalError: no such table: messages at C:/temp/secret/primary.bin")

        def mock_stage_failure(req):
            err = PipelineStageError(
                stage="importer",
                safe_message="Il file non è compatibile con il formato selezionato o presenta una struttura corrotta.",
            )
            raise err from raw_cause

        monkeypatch.setattr("ui.presentation.execute_real_ingestion", mock_stage_failure)

        import_btns = [b for b in at.button if "Importa e analizza struttura" in b.label]
        import_btns[0].click()
        at.run()

        assert not at.exception
        assert len(at.error) >= 1
        err_text = at.error[0].value
        assert "[importer] PipelineStageError" in err_text
        assert "non è compatibile con il formato selezionato" in err_text

        # Assoluta privacy: nessun leak della causa interna
        assert "OperationalError" not in err_text
        assert "secret" not in err_text
        assert "primary.bin" not in err_text

    def test_generic_ingestion_error_rendering_in_ui(self, monkeypatch):
        """Verifica gestione controllata di IngestionError generico nella UI (Gate B)."""
        at = AppTest.from_file(APP_PATH, default_timeout=15)
        at.run()
        at.sidebar.radio[0].set_value("2. Importazione")
        at.run()

        at.file_uploader[0].upload("file.db", b"data")

        def mock_generic_failure(req):
            raise IngestionError("Errore generico sanitizzato.")

        monkeypatch.setattr("ui.presentation.execute_real_ingestion", mock_generic_failure)

        import_btns = [b for b in at.button if "Importa e analizza struttura" in b.label]
        import_btns[0].click()
        at.run()

        assert not at.exception
        assert len(at.error) >= 1
        assert "IngestionError: Errore generico sanitizzato." in at.error[0].value

    def test_system_status_page_portable_no_hardware_block(self):
        """Verifica che la pagina Sistema / Stato sia portabile, priva di vecchi hardware block e mostri LM Studio / Benchmark."""
        at = AppTest.from_file(APP_PATH, default_timeout=15)
        at.run()
        at.sidebar.radio[0].set_value("6. Sistema / Stato")
        at.run()

        assert not at.exception
        subheaders = [sh.value for sh in at.subheader]
        assert "LM Studio / Benchmark" in subheaders
        assert "Nota Rinvio Benchmark LM Studio (Hardware Block)" not in subheaders

        warnings = [w.value for w in at.warning]
        for w in warnings:
            assert "AMD A8-7410" not in w

        all_text = " ".join(
            [sh.value for sh in at.subheader]
            + [m.value for m in at.markdown]
            + [i.value for i in at.info]
            + [w.value for w in at.warning]
        )
        assert "AMD A8-7410" not in all_text
        assert "DEFERRED" not in all_text
        assert "127.0.0.1:1234" in all_text

