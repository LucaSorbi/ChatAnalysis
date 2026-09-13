"""
tests/integration/test_real_ingestion.py
----------------------------------------
Integration test end-to-end per la REAL FILE INGESTION INTEGRATION (Fasi W, X, Y).

Verifiche:
1. WhatsApp msgstore SQLite (622 raw, 504 msgs, 3 chats, 115 media_refs);
2. WhatsApp msgstore + wa.db companion (enrichment contatti);
3. WhatsApp wa.db standalone (auxiliary-only, 4 contatti, zero documenti conversazione);
4. Cellebrite CSV (302 raw, 302 msgs, 3 chats);
5. Cellebrite JSON (100 raw, 100 msgs, 3 chats);
6. Cellebrite XML (50 raw, 50 msgs, raggruppamento UNRESOLVED);
7. Piena provenance forense, evidence_id univoci, e SearchService operativo su chat reale;
8. NO NETWORK / NO AI: Nessun socket, nessun LmStudioClient, nessun FakeLocalLlmClient.
"""
from __future__ import annotations

from pathlib import Path
import socket
import pytest

from ai.models import ConversationEvidenceDocument
from search.models import EvidenceSearchQuery, MatchMode
from search.service import SearchService
from ui.application import (
    build_search_service_for_conversation,
    execute_evidence_search,
    execute_real_ingestion,
)
from ui.models import (
    IngestionRequest,
    IngestionStatus,
    SourceFormat,
)

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "test_data"
MSGSTORE_FILE = FIXTURES_DIR / "whatsapp_export" / "msgstore.db"
WA_FILE = FIXTURES_DIR / "whatsapp_export" / "wa.db"
CELLEBRITE_CSV_FILE = FIXTURES_DIR / "cellebrite_export" / "messages.csv"
CELLEBRITE_JSON_FILE = FIXTURES_DIR / "cellebrite_export" / "messages.json"
CELLEBRITE_XML_FILE = FIXTURES_DIR / "cellebrite_export" / "report.xml"


@pytest.mark.integration
class TestRealIngestionIntegration:

    def test_whatsapp_msgstore_ingestion(self):
        """Test integrazione reale su WhatsApp msgstore.db."""
        assert MSGSTORE_FILE.exists()
        req = IngestionRequest(
            source_format=SourceFormat.WHATSAPP_MSGSTORE,
            filename=MSGSTORE_FILE.name,
            file_bytes=MSGSTORE_FILE.read_bytes(),
        )

        res = execute_real_ingestion(req)
        assert res.status == IngestionStatus.SUCCESS
        summary = res.summary

        # Regression counts approvati del progetto
        assert summary.raw_record_count == 622
        assert summary.unified_message_count == 504
        assert summary.conversation_count == 3
        assert summary.auxiliary_record_count == 118  # 115 media_refs + 3 chat_list
        assert summary.validation_issue_count == 2  # 2 record con timestamp anomalo (ts=0, ts=-1000)

        assert len(res.documents) == 3
        for doc_id, doc in res.documents.items():
            assert isinstance(doc, ConversationEvidenceDocument)
            assert doc.source_name == "msgstore_db"
            assert doc.document_id.startswith("doc::msgstore_db::")

            # Verifica unicità evidence_id all'interno del documento
            eids = [sec.evidence_id for sec in doc.all_evidence_sections]
            assert len(eids) == len(set(eids))

        # Verifica label pseudonimizzate e assenza message_count (Gate A, E, G.1, G.8)
        assert len(summary.conversations) == 3
        for idx, c in enumerate(summary.conversations, start=1):
            assert hasattr(c, "bundle_count")
            assert not hasattr(c, "message_count")
            assert "@s.whatsapp.net" not in c.display_label
            assert "@g.us" not in c.display_label
            assert "+39" not in c.display_label
            assert "Gruppo_Sintetico" not in c.display_label
            assert c.display_label.startswith(f"Conversazione {idx} — ")

        # Test SearchService sul primo documento di conversazione reale
        first_doc = next(iter(res.documents.values()))
        search_service = build_search_service_for_conversation(first_doc)
        assert len(search_service.index) == len(first_doc.all_evidence_sections)

        search_res = execute_evidence_search(
            search_service=search_service,
            query_text="sintetico",
            match_mode=MatchMode.PHRASE,
        )
        assert search_res.total_hits > 0
        assert len(search_res.hits) > 0
        for hit in search_res.hits:
            assert hit.source_name == "msgstore_db"
            assert hit.source_record_id is not None
            assert hit.original_text is not None

        # Ricerca senza esito
        empty_search = execute_evidence_search(
            search_service=search_service,
            query_text="parola_inesistente_xyz_999",
            match_mode=MatchMode.PHRASE,
        )
        assert empty_search.total_hits == 0

    def test_whatsapp_msgstore_with_wa_companion_ingestion(self):
        """Test msgstore.db arricchito con file wa.db contatti."""
        assert MSGSTORE_FILE.exists() and WA_FILE.exists()
        req = IngestionRequest(
            source_format=SourceFormat.WHATSAPP_MSGSTORE,
            filename=MSGSTORE_FILE.name,
            file_bytes=MSGSTORE_FILE.read_bytes(),
            companion_filename=WA_FILE.name,
            companion_bytes=WA_FILE.read_bytes(),
        )

        res = execute_real_ingestion(req)
        assert res.status == IngestionStatus.SUCCESS
        summary = res.summary

        assert summary.companion_filename == "wa.db"
        assert summary.companion_sha256 is not None
        assert summary.unified_message_count == 504
        assert summary.conversation_count == 3
        assert summary.auxiliary_record_count == 122  # 118 msgstore aux + 4 wa.db contacts

        # Verifica arricchimento display_name dei mittenti tramite wa.db
        all_messages = []
        for doc in res.documents.values():
            for b in doc.bundles:
                all_messages.append(b.message)

        named_senders = [m for m in all_messages if m.sender and m.sender.display_name]
        assert len(named_senders) > 0, "wa.db deve arricchire i display_name dei sender compatibili"

    def test_whatsapp_msgstore_with_corrupted_companion_adds_warning(self):
        """Test msgstore.db con companion wa.db corrotto/incompatibile (Gate D, G.7)."""
        assert MSGSTORE_FILE.exists()
        req = IngestionRequest(
            source_format=SourceFormat.WHATSAPP_MSGSTORE,
            filename=MSGSTORE_FILE.name,
            file_bytes=MSGSTORE_FILE.read_bytes(),
            companion_filename="corrupted_wa.db",
            companion_bytes=b"invalid_non_sqlite_header_bytes",
        )

        res = execute_real_ingestion(req)
        assert res.status == IngestionStatus.SUCCESS
        summary = res.summary

        # L'import principale di msgstore continua con successo
        assert summary.raw_record_count == 622
        assert summary.unified_message_count == 504
        assert summary.conversation_count == 3
        # companion_aux_count non incrementato (rimane 118 invece di 122)
        assert summary.auxiliary_record_count == 118

        # Warning presente e non sensibile
        assert len(summary.warnings) == 1
        warning = summary.warnings[0]
        assert "wa.db non è stato utilizzato perché non è risultato compatibile o leggibile" in warning
        assert "invalid_non_sqlite" not in warning
        assert "Traceback" not in warning
        assert "Exception" not in warning

    def test_whatsapp_wa_standalone_ingestion(self):
        """Test wa.db standalone: deve risultare AUXILIARY_ONLY con 0 documenti conversazione."""
        assert WA_FILE.exists()
        req = IngestionRequest(
            source_format=SourceFormat.WHATSAPP_WA,
            filename=WA_FILE.name,
            file_bytes=WA_FILE.read_bytes(),
        )

        res = execute_real_ingestion(req)
        assert res.status == IngestionStatus.AUXILIARY_ONLY
        assert len(res.documents) == 0
        summary = res.summary

        assert summary.raw_record_count == 4
        assert summary.auxiliary_record_count == 4
        assert summary.conversation_count == 0
        assert summary.unified_message_count == 0
        assert "wa.db contiene dati contatto/identità" in summary.warnings[0]

    def test_cellebrite_csv_ingestion(self):
        """Test Cellebrite CSV messages.csv."""
        assert CELLEBRITE_CSV_FILE.exists()
        req = IngestionRequest(
            source_format=SourceFormat.CELLEBRITE_CSV,
            filename=CELLEBRITE_CSV_FILE.name,
            file_bytes=CELLEBRITE_CSV_FILE.read_bytes(),
        )

        res = execute_real_ingestion(req)
        assert res.status == IngestionStatus.SUCCESS
        summary = res.summary

        assert summary.raw_record_count == 302
        assert summary.unified_message_count == 302
        assert summary.conversation_count == 3

        first_doc = next(iter(res.documents.values()))
        assert first_doc.source_name == "cellebrite_csv"
        svc = build_search_service_for_conversation(first_doc)
        search_res = execute_evidence_search(svc, query_text="Messaggio", match_mode=MatchMode.PHRASE)
        assert search_res.total_hits > 0

    def test_cellebrite_json_ingestion(self):
        """Test Cellebrite JSON messages.json."""
        assert CELLEBRITE_JSON_FILE.exists()
        req = IngestionRequest(
            source_format=SourceFormat.CELLEBRITE_JSON,
            filename=CELLEBRITE_JSON_FILE.name,
            file_bytes=CELLEBRITE_JSON_FILE.read_bytes(),
        )

        res = execute_real_ingestion(req)
        assert res.status == IngestionStatus.SUCCESS
        summary = res.summary

        assert summary.raw_record_count == 100
        assert summary.unified_message_count == 100
        assert summary.conversation_count == 3

        first_doc = next(iter(res.documents.values()))
        assert first_doc.source_name == "cellebrite_json"
        svc = build_search_service_for_conversation(first_doc)
        search_res = execute_evidence_search(svc, query_text="Messaggio", match_mode=MatchMode.PHRASE)
        assert search_res.total_hits > 0

    def test_cellebrite_xml_ingestion(self):
        """Test Cellebrite XML report.xml (raggruppamento chat UNRESOLVED)."""
        assert CELLEBRITE_XML_FILE.exists()
        req = IngestionRequest(
            source_format=SourceFormat.CELLEBRITE_XML,
            filename=CELLEBRITE_XML_FILE.name,
            file_bytes=CELLEBRITE_XML_FILE.read_bytes(),
        )

        res = execute_real_ingestion(req)
        assert res.status == IngestionStatus.SUCCESS
        summary = res.summary

        assert summary.raw_record_count == 50
        assert summary.unified_message_count == 50
        assert summary.conversation_count == 1  # Unica partizione UNRESOLVED

        doc = next(iter(res.documents.values()))
        assert doc.chat_id is None
        assert doc.metadata.get("is_unresolved") is True
        assert doc.source_name == "cellebrite_xml"

        svc = build_search_service_for_conversation(doc)
        search_res = execute_evidence_search(svc, query_text="Messaggio", match_mode=MatchMode.PHRASE)
        assert search_res.total_hits > 0

    def test_no_network_and_no_ai_invoked(self, monkeypatch):
        """
        Garantisce categoricamente (Fase Y) che durante l'ingestion, esplorazione e ricerca
        non venga istanziato alcun client AI (LmStudioClient, FakeLocalLlmClient)
        e non venga aperto alcun socket di rete.
        """
        # 1. Blocco rete assoluto su socket.connect
        def block_socket_connect(*args, **kwargs):
            pytest.fail(f"Tentativo di connessione di rete non autorizzato rilevato: args={args}")

        monkeypatch.setattr(socket.socket, "connect", block_socket_connect)

        # 2. Monitoraggio classi AI
        from ai import lmstudio
        from ai import backend

        def block_lm_studio(*args, **kwargs):
            pytest.fail("Istanziazione di LmStudioClient rilevata durante real ingestion!")

        def block_fake_ai(*args, **kwargs):
            pytest.fail("Istanziazione di FakeLocalLlmClient rilevata durante real ingestion!")

        monkeypatch.setattr(lmstudio, "LmStudioClient", block_lm_studio)
        monkeypatch.setattr(backend, "FakeLocalLlmClient", block_fake_ai)

        # Esecuzione completa di un'ingestion e ricerca reale
        req = IngestionRequest(
            source_format=SourceFormat.CELLEBRITE_JSON,
            filename=CELLEBRITE_JSON_FILE.name,
            file_bytes=CELLEBRITE_JSON_FILE.read_bytes(),
        )
        res = execute_real_ingestion(req)
        doc = next(iter(res.documents.values()))
        svc = build_search_service_for_conversation(doc)
        search_res = execute_evidence_search(svc, query_text="sintetico")

        assert res.status == IngestionStatus.SUCCESS
        assert search_res.total_hits > 0
