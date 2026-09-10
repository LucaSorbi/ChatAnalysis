"""
tests/integration/test_multimodal_pipeline_integration.py
---------------------------------------------------------
Test di integrazione per il layer di Multimodal Processing su dataset sintetico reale:
- Risoluzione dei media su tutti i 956 UnifiedMessage generati dalla pipeline completa
- Verifica dei conteggi fisici reali (RESOLVED, MISSING, NO_REFERENCE) per ciascuna sorgente
- Esecuzione end-to-end della MultimodalAudioPipeline con FakeAudioTranscriber
- Verifica della rigorosa immutabilità di UnifiedMessage (text_content inalterato)
"""
from __future__ import annotations

from pathlib import Path
import pytest

from importer.cellebrite_csv import CellebriteCsvImporter
from importer.cellebrite_json import CellebriteJsonImporter
from importer.cellebrite_xml import CellebriteXmlImporter
from importer.whatsapp_msgstore import WhatsAppMsgstoreImporter
from importer.whatsapp_wa import WhatsAppWaDbImporter
from normalization.normalizer import RecordNormalizer
from entity_resolution.resolver import DeterministicEntityResolver
from multimodal.models import MediaKind, MediaResolutionStatus, TranscriptionStatus
from multimodal.pipeline import MultimodalAudioPipeline
from multimodal.resolver import MediaResolutionContext, MediaResolver
from multimodal.transcriber import FakeAudioTranscriber
from unified.builder import UnifiedModelBuilder
from unified.context import UnifiedBuildContext
from validation.validator import RecordValidator

TEST_DATA_DIR = Path("test_data").resolve()
WHATSAPP_DIR = TEST_DATA_DIR / "whatsapp_export"
CELLEBRITE_DIR = TEST_DATA_DIR / "cellebrite_export"


@pytest.fixture(scope="module")
def full_pipeline_messages():
    """Esegue la pipeline completa fino a UnifiedMessage su tutti i dati di test_data."""
    importers = [
        WhatsAppMsgstoreImporter(),
        WhatsAppWaDbImporter(),
        CellebriteCsvImporter(),
        CellebriteJsonImporter(),
        CellebriteXmlImporter(),
    ]
    raw_records = []
    # Ordine deterministico delle sorgenti
    sources = [
        WHATSAPP_DIR / "msgstore.db",
        WHATSAPP_DIR / "wa.db",
        CELLEBRITE_DIR / "messages.csv",
        CELLEBRITE_DIR / "messages.json",
        CELLEBRITE_DIR / "report.xml",
    ]
    for src in sources:
        for imp in importers:
            if imp.can_import(src):
                raw_records.extend(list(imp.import_records(src)))
                break

    validator = RecordValidator()
    val_results = [validator.validate(r) for r in raw_records]

    normalizer = RecordNormalizer()
    norm_records = [normalizer.normalize(v) for v in val_results]

    resolver = DeterministicEntityResolver()
    res_result = resolver.resolve(norm_records)

    build_ctx = UnifiedBuildContext.from_records(norm_records, resolution=res_result)
    builder = UnifiedModelBuilder(context=build_ctx)
    unified_msgs = builder.build_all(norm_records)

    return raw_records, unified_msgs


@pytest.mark.integration
class TestMultimodalPipelineIntegration:

    def test_media_resolver_ground_truth_counts_across_dataset(self, full_pipeline_messages):
        raw_records, messages = full_pipeline_messages
        assert len(messages) == 956

        media_ctx = MediaResolutionContext.from_records(raw_records)
        resolver = MediaResolver(allowed_roots=[TEST_DATA_DIR], context=media_ctx)

        assets = [resolver.resolve(m) for m in messages]
        assert len(assets) == 956

        status_counts = {}
        for a in assets:
            status_counts[a.status] = status_counts.get(a.status, 0) + 1

        # Ground truth convalidato:
        # 2 RESOLVED (WhatsApp Images/IMG_00003.jpg e IMG_00012.jpg)
        # 138 MISSING (113 WhatsApp + 25 Cellebrite CSV)
        # 816 NO_REFERENCE (389 WhatsApp + 277 CSV + 100 JSON + 50 XML)
        assert status_counts.get(MediaResolutionStatus.RESOLVED, 0) == 2
        assert status_counts.get(MediaResolutionStatus.MISSING, 0) == 138
        assert status_counts.get(MediaResolutionStatus.NO_REFERENCE, 0) == 816
        assert sum(status_counts.values()) == 956

        # Verifica per sorgente
        msgstore_assets = [a for a in assets if a.source_name == "msgstore_db"]
        assert len(msgstore_assets) == 504
        assert sum(1 for a in msgstore_assets if a.status == MediaResolutionStatus.RESOLVED) == 2
        assert sum(1 for a in msgstore_assets if a.status == MediaResolutionStatus.MISSING) == 113
        assert sum(1 for a in msgstore_assets if a.status == MediaResolutionStatus.NO_REFERENCE) == 389

        csv_assets = [a for a in assets if a.source_name == "cellebrite_csv"]
        assert len(csv_assets) == 302
        assert sum(1 for a in csv_assets if a.status == MediaResolutionStatus.RESOLVED) == 0
        assert sum(1 for a in csv_assets if a.status == MediaResolutionStatus.MISSING) == 25
        assert sum(1 for a in csv_assets if a.status == MediaResolutionStatus.NO_REFERENCE) == 277

        json_assets = [a for a in assets if a.source_name == "cellebrite_json"]
        assert len(json_assets) == 100
        # Tutti e 100 i record JSON hanno content.media_path = null
        assert sum(1 for a in json_assets if a.status == MediaResolutionStatus.NO_REFERENCE) == 100

        xml_assets = [a for a in assets if a.source_name == "cellebrite_xml"]
        assert len(xml_assets) == 50
        assert sum(1 for a in xml_assets if a.status == MediaResolutionStatus.NO_REFERENCE) == 50

    def test_resolved_assets_have_valid_hashes_and_sizes(self, full_pipeline_messages):
        raw_records, messages = full_pipeline_messages
        media_ctx = MediaResolutionContext.from_records(raw_records)
        resolver = MediaResolver(allowed_roots=[TEST_DATA_DIR], context=media_ctx)

        resolved_assets = [resolver.resolve(m) for m in messages if resolver.resolve(m).is_resolved]
        assert len(resolved_assets) == 2

        for a in resolved_assets:
            assert a.status == MediaResolutionStatus.RESOLVED
            assert a.media_kind == MediaKind.IMAGE
            assert a.file_size_bytes is not None and a.file_size_bytes > 0
            assert a.sha256 is not None and len(a.sha256) == 64
            assert Path(a.resolved_path).exists()

    def test_pipeline_streaming_end_to_end_preserves_messages(self, full_pipeline_messages):
        raw_records, messages = full_pipeline_messages
        media_ctx = MediaResolutionContext.from_records(raw_records)
        resolver = MediaResolver(allowed_roots=[TEST_DATA_DIR], context=media_ctx)
        transcriber = FakeAudioTranscriber(default_language="it")
        pipeline = MultimodalAudioPipeline(resolver=resolver, transcriber=transcriber)

        text_contents_before = [m.text_content for m in messages]

        results = list(pipeline.process_messages(messages))
        assert len(results) == 956

        # Nessun file audio del dataset sintetico coincide fisicamente con i riferimenti (i 2 file risolti sono immagini)
        # Quindi nessuna trascrizione audio scatta per i record reali esistenti
        transcriptions = [tr for _, tr in results if tr is not None]
        assert len(transcriptions) == 0

        # Il testo di tutti i 956 messaggi deve rimanere identico e inalterato
        text_contents_after = [m.text_content for m in messages]
        assert text_contents_after == text_contents_before
