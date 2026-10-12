"""
tests/unit/test_search_engine.py
--------------------------------
Test unitari per EvidenceSearchEngine:
- Match modes: PHRASE, ALL_TERMS, ANY_TERM, EXACT
- Whole-term matching (Point E): 'art' non matcha 'partita'
- Case-insensitivity su caratteri ASCII e Unicode
- Preservazione rigorosa degli accenti (caffè vs caffe)
- Testi multilingua (italiano, inglese, spagnolo, mixed)
- Filtri: source_types, language, source_name
- Semantica total_hits vs limit (Point F): total_hits riflette il totale, hits i primi N
- Zero risultati
- Ordine deterministico naturale
- Nessuna mutazione dell'input
- Evidenze multimodali: ORIGINAL_TEXT, STT_TRANSCRIPTION, OCR_TEXT, VISION_DESCRIPTION, VISION_OBSERVATION
"""
import pytest

from ai.models import ConversationEvidenceDocument
from importer.models import RawRecord
from multimodal.evidence import (
    EvidenceSourceType,
    MessageEvidenceBundle,
    TextEvidenceSection,
)
from multimodal.models import (
    AudioTranscriptionResult,
    AudioTranscriptSegment,
    ImageOcrResult,
    ImageVisionResult,
    MediaKind,
    MediaResolutionStatus,
    OcrStatus,
    OcrTextRegion,
    ResolvedMediaAsset,
    TranscriptionStatus,
    VisionStatus,
)
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from search.engine import EvidenceSearchEngine
from search.index import EvidenceIndex
from search.models import EvidenceSearchQuery, MatchMode
from unified.models import UnifiedMessage
from validation.models import ValidationResult


def _make_unified_message(
    idx: str,
    text: str | None = "Testo",
    source_name: str = "msgstore_db",
) -> UnifiedMessage:
    raw = RawRecord(
        source_name=source_name,
        source_path="/path/test",
        source_record_id=idx,
        record_type="message",
        raw_fields={"data": text},
        media_reference=None,
        metadata={},
    )
    val = ValidationResult(record=raw, issues=())
    ts = NormalizedTimestamp(status=TimestampTzStatus.ABSENT)
    norm = NormalizedRecord(
        raw_record=raw,
        validation_result=val,
        source_name=source_name,
        source_record_id=idx,
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT if text else CanonicalMessageType.IMAGE,
        text_content=text,
        media_reference=None,
    )
    return UnifiedMessage(
        message_id=f"unified:{source_name}:{idx}",
        source_name=source_name,
        source_record_id=idx,
        source_path="/path/test",
        record_type="message",
        timestamp=ts,
        message_type=CanonicalMessageType.TEXT if text else CanonicalMessageType.IMAGE,
        text_content=text,
        media_reference=None,
        provenance_record=norm,
    )


@pytest.mark.unit
class TestSearchEngine:

    def test_phrase_match(self):
        sec1 = TextEvidenceSection(
            evidence_id="e1",
            source_type=EvidenceSourceType.ORIGINAL_TEXT,
            text="Si prega di verificare il bonifico bancario urgente.",
            message_id="m1",
            source_name="msgstore_db",
            source_record_id="1",
        )
        sec2 = TextEvidenceSection(
            evidence_id="e2",
            source_type=EvidenceSourceType.ORIGINAL_TEXT,
            text="Nessuna informazione finanziaria qui.",
            message_id="m2",
            source_name="msgstore_db",
            source_record_id="2",
        )
        engine = EvidenceSearchEngine([sec1, sec2])

        q = EvidenceSearchQuery(query_text="bonifico bancario", match_mode=MatchMode.PHRASE)
        res = engine.search(q)
        assert res.total_hits == 1
        assert len(res.hits) == 1
        assert res.hits[0].evidence_id == "e1"
        assert res.hits[0].original_text == "Si prega di verificare il bonifico bancario urgente."
        assert res.hits[0].section is sec1

    def test_all_terms_match(self):
        sec1 = TextEvidenceSection(
            evidence_id="e1",
            source_type=EvidenceSourceType.ORIGINAL_TEXT,
            text="L'importo del bonifico è arrivato, ma quale conto bancario?",
            message_id="m1",
            source_name="msgstore_db",
            source_record_id="1",
        )
        sec2 = TextEvidenceSection(
            evidence_id="e2",
            source_type=EvidenceSourceType.ORIGINAL_TEXT,
            text="Solo un bonifico senza banca.",
            message_id="m2",
            source_name="msgstore_db",
            source_record_id="2",
        )
        engine = EvidenceSearchEngine([sec1, sec2])

        # ALL_TERMS: 'bancario' e 'bonifico' in ordine invertito rispetto al testo
        q = EvidenceSearchQuery(query_text="bancario bonifico", match_mode=MatchMode.ALL_TERMS)
        res = engine.search(q)
        assert res.total_hits == 1
        assert len(res.hits) == 1
        assert res.hits[0].evidence_id == "e1"

    def test_any_term_match(self):
        sec1 = TextEvidenceSection(
            evidence_id="e1",
            source_type=EvidenceSourceType.ORIGINAL_TEXT,
            text="Pagamento effettuato con carta.",
            message_id="m1",
            source_name="msgstore_db",
            source_record_id="1",
        )
        sec2 = TextEvidenceSection(
            evidence_id="e2",
            source_type=EvidenceSourceType.ORIGINAL_TEXT,
            text="Attesa per il bonifico estero.",
            message_id="m2",
            source_name="msgstore_db",
            source_record_id="2",
        )
        sec3 = TextEvidenceSection(
            evidence_id="e3",
            source_type=EvidenceSourceType.ORIGINAL_TEXT,
            text="Messaggio del tutto estraneo.",
            message_id="m3",
            source_name="msgstore_db",
            source_record_id="3",
        )
        engine = EvidenceSearchEngine([sec1, sec2, sec3])

        q = EvidenceSearchQuery(query_text="carta bonifico", match_mode=MatchMode.ANY_TERM)
        res = engine.search(q)
        assert res.total_hits == 2
        assert [h.evidence_id for h in res.hits] == ["e1", "e2"]

    def test_exact_match(self):
        sec1 = TextEvidenceSection(
            evidence_id="e1",
            source_type=EvidenceSourceType.ORIGINAL_TEXT,
            text="Accetto le condizioni",
            message_id="m1",
            source_name="msgstore_db",
            source_record_id="1",
        )
        sec2 = TextEvidenceSection(
            evidence_id="e2",
            source_type=EvidenceSourceType.ORIGINAL_TEXT,
            text="Accetto le condizioni generali di contratto.",
            message_id="m2",
            source_name="msgstore_db",
            source_record_id="2",
        )
        engine = EvidenceSearchEngine([sec1, sec2])

        q = EvidenceSearchQuery(query_text="accetto le condizioni", match_mode=MatchMode.EXACT)
        res = engine.search(q)
        assert res.total_hits == 1
        assert res.hits[0].evidence_id == "e1"

    # --- Whole-Term Matching & Partial-Word False Positives (Point E) ---

    def test_whole_term_matching_art_vs_partita(self):
        sec_partita = TextEvidenceSection(
            evidence_id="e1",
            source_type=EvidenceSourceType.ORIGINAL_TEXT,
            text="Abbiamo seguito la partita di calcio ieri sera.",
            message_id="m1",
            source_name="msgstore_db",
            source_record_id="1",
        )
        sec_art = TextEvidenceSection(
            evidence_id="e2",
            source_type=EvidenceSourceType.ORIGINAL_TEXT,
            text="Si applica l'art. 2043 per la responsabilità extracontrattuale.",
            message_id="m2",
            source_name="msgstore_db",
            source_record_id="2",
        )
        engine = EvidenceSearchEngine([sec_partita, sec_art])

        # ALL_TERMS con query 'art': NON deve matchare sec_partita
        q_all = EvidenceSearchQuery(query_text="art", match_mode=MatchMode.ALL_TERMS)
        res_all = engine.search(q_all)
        assert res_all.total_hits == 1
        assert res_all.hits[0].evidence_id == "e2"

        # ANY_TERM con query 'art': NON deve matchare sec_partita
        q_any = EvidenceSearchQuery(query_text="art", match_mode=MatchMode.ANY_TERM)
        res_any = engine.search(q_any)
        assert res_any.total_hits == 1
        assert res_any.hits[0].evidence_id == "e2"

    def test_whole_term_accents_caffe_vs_caffè(self):
        sec_accent = TextEvidenceSection(
            evidence_id="e1",
            source_type=EvidenceSourceType.ORIGINAL_TEXT,
            text="Un caffè al banco, per favore.",
            message_id="m1",
            source_name="msgstore_db",
            source_record_id="1",
        )
        sec_no_accent = TextEvidenceSection(
            evidence_id="e2",
            source_type=EvidenceSourceType.ORIGINAL_TEXT,
            text="Un caffe scritto senza accento.",
            message_id="m2",
            source_name="msgstore_db",
            source_record_id="2",
        )
        engine = EvidenceSearchEngine([sec_accent, sec_no_accent])

        # ALL_TERMS 'caffè' trova solo e1
        q1 = EvidenceSearchQuery(query_text="caffè", match_mode=MatchMode.ALL_TERMS)
        r1 = engine.search(q1)
        assert r1.total_hits == 1
        assert r1.hits[0].evidence_id == "e1"

        # ALL_TERMS 'caffe' trova solo e2
        q2 = EvidenceSearchQuery(query_text="caffe", match_mode=MatchMode.ALL_TERMS)
        r2 = engine.search(q2)
        assert r2.total_hits == 1
        assert r2.hits[0].evidence_id == "e2"

    def test_case_insensitive_and_unicode_accents(self):
        sec1 = TextEvidenceSection(
            evidence_id="e1",
            source_type=EvidenceSourceType.ORIGINAL_TEXT,
            text="Ci vediamo al CAFFÈ della CITTÀ.",
            message_id="m1",
            source_name="msgstore_db",
            source_record_id="1",
        )
        sec2 = TextEvidenceSection(
            evidence_id="e2",
            source_type=EvidenceSourceType.ORIGINAL_TEXT,
            text="Ho ordinato un caffe normale.",  # senza accento
            message_id="m2",
            source_name="msgstore_db",
            source_record_id="2",
        )
        engine = EvidenceSearchEngine([sec1, sec2])

        q = EvidenceSearchQuery(query_text="caffè")
        res = engine.search(q)
        assert res.total_hits == 1
        assert res.hits[0].evidence_id == "e1"

    def test_multilingual_and_mixed_language(self):
        sec_es = TextEvidenceSection(
            evidence_id="es_1",
            source_type=EvidenceSourceType.ORIGINAL_TEXT,
            text="Nos vemos mañana por la tarde, señor Gómez.",
            language="es",
            message_id="m_es",
            source_name="msgstore_db",
            source_record_id="10",
        )
        sec_en = TextEvidenceSection(
            evidence_id="en_1",
            source_type=EvidenceSourceType.ORIGINAL_TEXT,
            text="Please review the confidential agreement ASAP.",
            language="en",
            message_id="m_en",
            source_name="msgstore_db",
            source_record_id="11",
        )
        sec_mixed = TextEvidenceSection(
            evidence_id="mix_1",
            source_type=EvidenceSourceType.ORIGINAL_TEXT,
            text="Meeting domani alle 10: let's bring the contract.",
            language=None,
            message_id="m_mix",
            source_name="msgstore_db",
            source_record_id="12",
        )
        engine = EvidenceSearchEngine([sec_es, sec_en, sec_mixed])

        # Spagnolo con caratteri ñ
        q_es = EvidenceSearchQuery(query_text="mañana")
        assert engine.search(q_es).total_hits == 1

        # Inglese
        q_en = EvidenceSearchQuery(query_text="confidential agreement")
        assert engine.search(q_en).total_hits == 1

        # Mixed
        q_mix = EvidenceSearchQuery(query_text="contract")
        assert engine.search(q_mix).total_hits == 1

    def test_filters_source_type_language_and_source_name(self):
        sec1 = TextEvidenceSection(
            evidence_id="e1",
            source_type=EvidenceSourceType.ORIGINAL_TEXT,
            text="Contratto stipulato",
            language="it",
            source_name="msgstore_db",
            message_id="m1",
            source_record_id="1",
        )
        sec2 = TextEvidenceSection(
            evidence_id="e2",
            source_type=EvidenceSourceType.OCR_TEXT,
            text="Contratto stipulato",
            language="it",
            source_name="cellebrite_csv",
            message_id="m2",
            source_record_id="2",
        )
        sec3 = TextEvidenceSection(
            evidence_id="e3",
            source_type=EvidenceSourceType.STT_TRANSCRIPTION,
            text="Contratto stipulato",
            language="en",
            source_name="msgstore_db",
            message_id="m3",
            source_record_id="3",
        )
        engine = EvidenceSearchEngine([sec1, sec2, sec3])

        # Filtro source_type
        q_src_type = EvidenceSearchQuery(
            query_text="contratto",
            source_types=[EvidenceSourceType.OCR_TEXT],
        )
        res = engine.search(q_src_type)
        assert res.total_hits == 1
        assert res.hits[0].evidence_id == "e2"

        # Filtro source_name
        q_src_name = EvidenceSearchQuery(
            query_text="contratto",
            source_name="cellebrite_csv",
        )
        assert engine.search(q_src_name).total_hits == 1
        assert engine.search(q_src_name).hits[0].evidence_id == "e2"

        # Filtro language
        q_lang = EvidenceSearchQuery(
            query_text="contratto",
            language="en",
        )
        assert engine.search(q_lang).total_hits == 1
        assert engine.search(q_lang).hits[0].evidence_id == "e3"

    # --- Limit and Total Hits Semantics (Point F) ---

    def test_limit_and_total_hits_semantics(self):
        # 5 sezioni tutte corrispondenti a 'importante'
        sections = [
            TextEvidenceSection(
                evidence_id=f"e_{i}",
                source_type=EvidenceSourceType.ORIGINAL_TEXT,
                text=f"Riferimento numero {i} importante",
                message_id=f"m_{i}",
                source_name="msgstore_db",
                source_record_id=str(i),
            )
            for i in range(5)
        ]
        engine = EvidenceSearchEngine(sections)

        # limit=2: total_hits deve essere 5, len(hits) deve essere 2
        q = EvidenceSearchQuery(query_text="importante", limit=2)
        res = engine.search(q)

        assert res.total_hits == 5
        assert len(res.hits) == 2
        assert [h.evidence_id for h in res.hits] == ["e_0", "e_1"]
        assert res.metadata["total_hits"] == 5
        assert res.metadata["returned_hits"] == 2
        assert res.metadata["truncated"] is True

        # Senza limit: total_hits == 5, len(hits) == 5, truncated == False
        q_no_limit = EvidenceSearchQuery(query_text="importante")
        res_no_limit = engine.search(q_no_limit)
        assert res_no_limit.total_hits == 5
        assert len(res_no_limit.hits) == 5
        assert res_no_limit.metadata["truncated"] is False

    def test_zero_results(self):
        sec = TextEvidenceSection(
            evidence_id="e1",
            source_type=EvidenceSourceType.ORIGINAL_TEXT,
            text="Messaggio di test",
            message_id="m1",
            source_name="msgstore_db",
            source_record_id="1",
        )
        engine = EvidenceSearchEngine([sec])
        q = EvidenceSearchQuery(query_text="inesistente")
        res = engine.search(q)
        assert res.total_hits == 0
        assert len(res.hits) == 0

    def test_no_input_mutation(self):
        original_text = "  Testo Originale   Con   Spazi  "
        sec = TextEvidenceSection(
            evidence_id="e1",
            source_type=EvidenceSourceType.ORIGINAL_TEXT,
            text=original_text,
            message_id="m1",
            source_name="msgstore_db",
            source_record_id="1",
        )
        engine = EvidenceSearchEngine([sec])
        res = engine.search(EvidenceSearchQuery(query_text="originale"))
        assert res.total_hits == 1
        assert sec.text == original_text
        assert res.hits[0].original_text == original_text

    def test_multimodal_evidence_sources_coverage(self):
        """Verifica la copertura di tutti i source type: ORIGINAL_TEXT, STT, OCR, VISION_DESCRIPTION, VISION_OBSERVATION."""
        msg = _make_unified_message("100", "Testo originale messaggio")
        asset = ResolvedMediaAsset(
            message_id=msg.message_id,
            source_name=msg.source_name,
            source_record_id=msg.source_record_id,
            raw_reference="media/file.jpg",
            resolved_path="/path/file.jpg",
            media_kind=MediaKind.IMAGE,
            status=MediaResolutionStatus.RESOLVED,
            file_size_bytes=1024,
            sha256="aabbcc",
            provenance_message=msg,
        )
        audio_asset = ResolvedMediaAsset(
            message_id=msg.message_id,
            source_name=msg.source_name,
            source_record_id=msg.source_record_id,
            raw_reference="audio/file.opus",
            resolved_path="/path/file.opus",
            media_kind=MediaKind.AUDIO,
            status=MediaResolutionStatus.RESOLVED,
            file_size_bytes=2048,
            sha256="ddeeff",
            provenance_message=msg,
        )
        stt = AudioTranscriptionResult(
            message_id=msg.message_id,
            source_name=msg.source_name,
            source_record_id=msg.source_record_id,
            status=TranscriptionStatus.SUCCESS,
            full_transcript="Trascrizione vocale della nota audio",
            segments=(AudioTranscriptSegment(0.0, 2.0, "Trascrizione vocale della nota audio"),),
            detected_language="it",
            provenance_asset=audio_asset,
        )
        ocr = ImageOcrResult(
            message_id=msg.message_id,
            source_name=msg.source_name,
            source_record_id=msg.source_record_id,
            status=OcrStatus.SUCCESS,
            full_text="Testo scansionato nello screenshot della ricevuta",
            regions=(OcrTextRegion("Testo scansionato nello screenshot della ricevuta", order_index=0),),
            language_config="ita",
            provenance_asset=asset,
        )
        vision = ImageVisionResult(
            message_id=msg.message_id,
            source_name=msg.source_name,
            source_record_id=msg.source_record_id,
            status=VisionStatus.SUCCESS,
            description="Una foto raffigurante un documento contabile timbrato",
            observations=(
                "Presenza di timbro circolare in inchiostro blu",
                "Firma autografa sul margine inferiore",
            ),
            provenance_asset=asset,
        )

        bundle = MessageEvidenceBundle(
            message=msg,
            audio_transcription=stt,
            image_ocr=ocr,
            image_vision=vision,
        )
        doc = ConversationEvidenceDocument(
            document_id="doc::multimodal",
            bundles=(bundle,),
            source_name="msgstore_db",
        )
        engine = EvidenceSearchEngine(doc)

        # 1. ORIGINAL_TEXT
        r_orig = engine.search(EvidenceSearchQuery(query_text="originale messaggio"))
        assert r_orig.total_hits == 1
        assert r_orig.hits[0].source_type == EvidenceSourceType.ORIGINAL_TEXT

        # 2. STT_TRANSCRIPTION
        r_stt = engine.search(EvidenceSearchQuery(query_text="nota audio"))
        assert r_stt.total_hits == 1
        assert r_stt.hits[0].source_type == EvidenceSourceType.STT_TRANSCRIPTION

        # 3. OCR_TEXT
        r_ocr = engine.search(EvidenceSearchQuery(query_text="ricevuta"))
        assert r_ocr.total_hits == 1
        assert r_ocr.hits[0].source_type == EvidenceSourceType.OCR_TEXT

        # 4. VISION_DESCRIPTION
        r_vis_desc = engine.search(EvidenceSearchQuery(query_text="documento contabile"))
        assert r_vis_desc.total_hits == 1
        assert r_vis_desc.hits[0].source_type == EvidenceSourceType.VISION_DESCRIPTION

        # 5. VISION_OBSERVATION
        r_vis_obs = engine.search(EvidenceSearchQuery(query_text="timbro circolare"))
        assert r_vis_obs.total_hits == 1
        assert r_vis_obs.hits[0].source_type == EvidenceSourceType.VISION_OBSERVATION
