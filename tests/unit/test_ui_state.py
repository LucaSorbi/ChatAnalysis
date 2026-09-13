"""
tests/unit/test_ui_state.py
---------------------------
Test unitari per la gestione dello stato della UI (ui/state.py).
"""
from __future__ import annotations

import pytest

from ui import state
from ui.models import DatasetMode


@pytest.mark.unit
class TestUiState:

    def test_init_session_state_defaults(self):
        s: dict = {}
        state.init_session_state(s)

        assert state.is_dataset_loaded(s) is False
        assert state.get_dataset_mode(s) == DatasetMode.NONE
        assert state.get_conversation_document(s) is None
        assert state.get_detection_results(s) == ()
        assert state.get_discovery_results(s) == ()
        assert state.get_search_service(s) is None
        assert state.get_selected_evidence_id(s) is None
        assert state.get_error_message(s) is None

    def test_load_and_reset_dataset(self):
        s: dict = {}
        state.init_session_state(s)

        # Caricamento demo
        state.load_demo_dataset(s)
        assert state.is_dataset_loaded(s) is True
        assert state.get_dataset_mode(s) == DatasetMode.DEMO

        doc = state.get_conversation_document(s)
        assert doc is not None
        assert doc.document_id == "doc::demo_forensic_chat"

        det = state.get_detection_results(s)
        assert len(det) >= 3

        disc = state.get_discovery_results(s)
        assert len(disc) >= 1

        service = state.get_search_service(s)
        assert service is not None
        assert len(service.index) == len(doc.all_evidence_sections)

        # Reset sessione
        state.reset_dataset(s)
        assert state.is_dataset_loaded(s) is False
        assert state.get_dataset_mode(s) == DatasetMode.NONE
        assert state.get_conversation_document(s) is None
        assert state.get_detection_results(s) == ()
        assert state.get_discovery_results(s) == ()
        assert state.get_search_service(s) is None

    def test_selected_evidence_id(self):
        s: dict = {}
        state.init_session_state(s)
        assert state.get_selected_evidence_id(s) is None

        state.set_selected_evidence_id("msg::1::ORIGINAL_TEXT", s)
        assert state.get_selected_evidence_id(s) == "msg::1::ORIGINAL_TEXT"

        state.set_selected_evidence_id(None, s)
        assert state.get_selected_evidence_id(s) is None

    def test_error_message_handling(self):
        s: dict = {}
        state.init_session_state(s)
        assert state.get_error_message(s) is None

        state.set_error_message("Errore di validazione", s)
        assert state.get_error_message(s) == "Errore di validazione"

        state.clear_error_message(s)
        assert state.get_error_message(s) is None
