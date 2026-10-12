"""
ui/demo.py
----------
Generatore deterministico di dataset sintetico per la modalità dimostrativa della UI.

Requisiti:
- Utilizza ESCLUSIVAMENTE i modelli di dominio approvati (senza copie o repliche).
- Nessuna chiamata di rete, nessun LLM, nessun benchmark esterno.
- Contiene messaggi in più lingue (IT, EN, ES, mixed-language).
- Contiene sezioni per EvidenceSourceType:
  ORIGINAL_TEXT, STT_TRANSCRIPTION, OCR_TEXT, VISION_DESCRIPTION, VISION_OBSERVATION.
- Genera TopicDetectionResult con decisioni PRESENT, ABSENT, UNCERTAIN.
- Genera TopicDiscoveryResult con DiscoveredTopic emergenti.
- Rispetta al 100% l'integrità referenziale: tutti gli evidence_ids sono rigorosamente
  presenti nel ConversationEvidenceDocument e la provenance_document_id corrisponde.
"""
from __future__ import annotations

from ai.models import (
    ConversationEvidenceDocument,
    DiscoveredTopic,
    TopicDecision,
    TopicDetectionResult,
    TopicDiscoveryResult,
    TopicQuery,
)
from importer.models import RawRecord
from multimodal.evidence import EvidenceSourceType, MessageEvidenceBundle
from multimodal.models import (
    AudioTranscriptionResult,
    ImageOcrResult,
    ImageVisionResult,
    MediaKind,
    MediaResolutionStatus,
    OcrStatus,
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
from unified.models import UnifiedMessage
from validation.models import ValidationResult


def _create_synthetic_message(
    msg_id: str,
    source_record_id: str,
    text_content: str,
    source_name: str = "demo_msgstore_db",
    message_type: CanonicalMessageType = CanonicalMessageType.TEXT,
    media_reference: str | None = None,
) -> UnifiedMessage:
    """Costruisce un UnifiedMessage valido con la sua catena di provenance immutabile."""
    raw = RawRecord(
        source_name=source_name,
        source_path="/evidence/demo/chat_export.db",
        source_record_id=source_record_id,
        record_type="message",
        raw_fields={"data": text_content, "id": source_record_id},
        media_reference=media_reference,
        metadata={"source": "synthetic_demo"},
    )
    val = ValidationResult(record=raw, issues=())
    ts = NormalizedTimestamp(status=TimestampTzStatus.ABSENT)
    norm = NormalizedRecord(
        raw_record=raw,
        validation_result=val,
        source_name=source_name,
        source_record_id=source_record_id,
        record_type="message",
        timestamp=ts,
        message_type=message_type,
        text_content=text_content,
        media_reference=media_reference,
    )
    return UnifiedMessage(
        message_id=msg_id,
        source_name=source_name,
        source_record_id=source_record_id,
        source_path="/evidence/demo/chat_export.db",
        record_type="message",
        timestamp=ts,
        message_type=message_type,
        text_content=text_content,
        media_reference=media_reference,
        provenance_record=norm,
    )


def build_synthetic_demo_dataset() -> tuple[
    ConversationEvidenceDocument,
    tuple[TopicDetectionResult, ...],
    tuple[TopicDiscoveryResult, ...],
]:
    """
    Costruisce e restituisce un dataset dimostrativo completo, immutabile e verificato.

    Returns:
    --------
    (document, detection_results, discovery_results)
    """
    doc_id = "doc::demo_forensic_chat"
    source_name = "demo_msgstore_db"

    # Messaggio 1: Testo originale in Italiano
    msg1 = _create_synthetic_message(
        msg_id="msg::1",
        source_record_id="101",
        text_content="Ciao Mario, confermo che l'incontro preliminare per l'accordo riservato si terrà domani mattina a Milano.",
        source_name=source_name,
    )
    bundle1 = MessageEvidenceBundle(message=msg1)

    # Messaggio 2: Audio vocale in Inglese con trascrizione STT
    msg2 = _create_synthetic_message(
        msg_id="msg::2",
        source_record_id="102",
        text_content="",
        source_name=source_name,
        message_type=CanonicalMessageType.AUDIO,
        media_reference="audio/draft_request.opus",
    )
    asset2 = ResolvedMediaAsset(
        message_id=msg2.message_id,
        source_name=msg2.source_name,
        source_record_id=msg2.source_record_id,
        raw_reference="audio/draft_request.opus",
        resolved_path="/evidence/demo/audio/draft_request.opus",
        media_kind=MediaKind.AUDIO,
        status=MediaResolutionStatus.RESOLVED,
        file_size_bytes=48120,
        sha256="2a5b8e91c7819f03d528b61c92b23081e7d827a4192b0c95028ef784a91902ba",
        provenance_message=msg2,
    )
    stt2 = AudioTranscriptionResult(
        message_id=msg2.message_id,
        source_name=msg2.source_name,
        source_record_id=msg2.source_record_id,
        status=TranscriptionStatus.SUCCESS,
        full_transcript="Please send me the updated confidential contract draft as soon as possible.",
        detected_language="en",
        language_probability=0.98,
        engine="faster-whisper",
        model_name="small",
        provenance_asset=asset2,
    )
    bundle2 = MessageEvidenceBundle(message=msg2, audio_transcription=stt2)

    # Messaggio 3: Immagine con testo in Spagnolo ed estrazione OCR
    msg3 = _create_synthetic_message(
        msg_id="msg::3",
        source_record_id="103",
        text_content="Mira la factura del proveedor que acaba de llegar por correo.",
        source_name=source_name,
        message_type=CanonicalMessageType.IMAGE,
        media_reference="images/factura_103.jpg",
    )
    asset3 = ResolvedMediaAsset(
        message_id=msg3.message_id,
        source_name=msg3.source_name,
        source_record_id=msg3.source_record_id,
        raw_reference="images/factura_103.jpg",
        resolved_path="/evidence/demo/images/factura_103.jpg",
        media_kind=MediaKind.IMAGE,
        status=MediaResolutionStatus.RESOLVED,
        file_size_bytes=102400,
        sha256="4f8a19bc019283e74b6510a9c847d018274a910bf83748c90184b8d72810a911",
        provenance_message=msg3,
    )
    ocr3 = ImageOcrResult(
        message_id=msg3.message_id,
        source_name=msg3.source_name,
        source_record_id=msg3.source_record_id,
        status=OcrStatus.SUCCESS,
        full_text="FACTURA DE COMPRA - TOTAL 15.000 EUR - PAGADO AL CONTADO",
        language_config="es",
        engine="tesseract",
        provenance_asset=asset3,
    )
    bundle3 = MessageEvidenceBundle(message=msg3, image_ocr=ocr3)

    # Messaggio 4: Testo misto IT-EN (mixed-language)
    msg4 = _create_synthetic_message(
        msg_id="msg::4",
        source_record_id="104",
        text_content="Ho appena verificato il budget: we need to finalize the payment terms by Friday noon.",
        source_name=source_name,
    )
    bundle4 = MessageEvidenceBundle(message=msg4)

    # Messaggio 5: Immagine con analisi Vision (descrizione e osservazioni)
    msg5 = _create_synthetic_message(
        msg_id="msg::5",
        source_record_id="105",
        text_content="Foto scattata all'ingresso del deposito merci.",
        source_name=source_name,
        message_type=CanonicalMessageType.IMAGE,
        media_reference="images/deposito_ingresso.jpg",
    )
    asset5 = ResolvedMediaAsset(
        message_id=msg5.message_id,
        source_name=msg5.source_name,
        source_record_id=msg5.source_record_id,
        raw_reference="images/deposito_ingresso.jpg",
        resolved_path="/evidence/demo/images/deposito_ingresso.jpg",
        media_kind=MediaKind.IMAGE,
        status=MediaResolutionStatus.RESOLVED,
        file_size_bytes=245000,
        sha256="91a82c478104d9b8e72c8194a0293847e6182903847bca8291048b29c8192039",
        provenance_message=msg5,
    )
    vision5 = ImageVisionResult(
        message_id=msg5.message_id,
        source_name=msg5.source_name,
        source_record_id=msg5.source_record_id,
        status=VisionStatus.SUCCESS,
        description="Un magazzino industriale con diverse casse etichettate e una porta di sicurezza blindata.",
        observations=("Casse di legno sigillate", "Porta di sicurezza blindata"),
        engine="local-vision",
        model_name="vision-base",
        provenance_asset=asset5,
    )
    bundle5 = MessageEvidenceBundle(message=msg5, image_vision=vision5)

    # Messaggio 6: Vocale in Italiano con trascrizione STT
    msg6 = _create_synthetic_message(
        msg_id="msg::6",
        source_record_id="106",
        text_content="",
        source_name=source_name,
        message_type=CanonicalMessageType.AUDIO,
        media_reference="audio/avvocato_rossi.opus",
    )
    asset6 = ResolvedMediaAsset(
        message_id=msg6.message_id,
        source_name=msg6.source_name,
        source_record_id=msg6.source_record_id,
        raw_reference="audio/avvocato_rossi.opus",
        resolved_path="/evidence/demo/audio/avvocato_rossi.opus",
        media_kind=MediaKind.AUDIO,
        status=MediaResolutionStatus.RESOLVED,
        file_size_bytes=52300,
        sha256="a1b2c3d4e5f60718293a4b5c6d7e8f90123456789abcdef0123456789abcdef0",
        provenance_message=msg6,
    )
    stt6 = AudioTranscriptionResult(
        message_id=msg6.message_id,
        source_name=msg6.source_name,
        source_record_id=msg6.source_record_id,
        status=TranscriptionStatus.SUCCESS,
        full_transcript="Ieri sera ho parlato con l'avvocato Rossi per definire la clausola di riservatezza dell'accordo.",
        detected_language="it",
        language_probability=0.99,
        engine="faster-whisper",
        model_name="small",
        provenance_asset=asset6,
    )
    bundle6 = MessageEvidenceBundle(message=msg6, audio_transcription=stt6)

    # Messaggio 7: Testo originale in Inglese
    msg7 = _create_synthetic_message(
        msg_id="msg::7",
        source_record_id="107",
        text_content="The forensic technical audit showed zero vulnerabilities in our database backup infrastructure.",
        source_name=source_name,
    )
    bundle7 = MessageEvidenceBundle(message=msg7)

    # Messaggio 8: Testo originale in Spagnolo
    msg8 = _create_synthetic_message(
        msg_id="msg::8",
        source_record_id="108",
        text_content="Nos vemos mañana en el despacho para revisar todos los documentos y contratos firmados.",
        source_name=source_name,
    )
    bundle8 = MessageEvidenceBundle(message=msg8)

    # Costruzione ConversationEvidenceDocument
    doc = ConversationEvidenceDocument(
        document_id=doc_id,
        bundles=(bundle1, bundle2, bundle3, bundle4, bundle5, bundle6, bundle7, bundle8),
        chat_id="chat::synthetic_investigation",
        source_name=source_name,
        metadata={"environment": "demo_synthetic", "case_reference": "CASO-TEST-2026-01"},
    )

    # Costruzione TopicDetectionResult (PRESENT, ABSENT, UNCERTAIN)
    t_present = TopicDetectionResult(
        topic=TopicQuery(
            topic_id="T01",
            label="Accordo Riservato",
            description="Accordi preliminari e clausole contrattuali di riservatezza",
        ),
        decision=TopicDecision.PRESENT,
        evidence_ids=("msg::1::ORIGINAL_TEXT", "msg::6::STT_TRANSCRIPTION"),
        rationale="Confermati l'incontro preliminare per l'accordo a Milano (msg::1) e il colloquio con l'avvocato Rossi per la clausola di riservatezza (msg::6).",
        provenance_document_id=doc_id,
    )

    t_absent = TopicDetectionResult(
        topic=TopicQuery(
            topic_id="T02",
            label="Attività di Contrabbando",
            description="Compravendita illegale di merci contraffatte o contrabbandate",
        ),
        decision=TopicDecision.ABSENT,
        evidence_ids=(),
        rationale="Nessuna evidenza rilevata nei messaggi della conversazione in merito ad attività di contrabbando o merci illegali.",
        provenance_document_id=doc_id,
    )

    t_uncertain = TopicDetectionResult(
        topic=TopicQuery(
            topic_id="T03",
            label="Transazioni Finanziarie Sospette",
            description="Pagamenti in contanti o termini di pagamento anomali non tracciati",
        ),
        decision=TopicDecision.UNCERTAIN,
        evidence_ids=("msg::3::OCR_TEXT", "msg::4::ORIGINAL_TEXT"),
        rationale="Rilevata fattura da 15.000 EUR pagata al contado (msg::3) e sollecito a saldare i termini entro venerdì (msg::4), senza evidenza esplicita di illiceità.",
        provenance_document_id=doc_id,
    )

    # Costruzione TopicDiscoveryResult
    discovery = TopicDiscoveryResult(
        topics=(
            DiscoveredTopic(
                label="Logistica e Ispezione Depositi",
                short_description="Ispezione visiva del magazzino merci con casse sigillate e sicurezza",
                evidence_ids=("msg::5::VISION_DESCRIPTION",),
            ),
            DiscoveredTopic(
                label="Gestione Contratti e Bozze",
                short_description="Scambio di richieste per bozze contrattuali e revisione documenti firmati",
                evidence_ids=("msg::2::STT_TRANSCRIPTION", "msg::8::ORIGINAL_TEXT"),
            ),
        ),
        provenance_document_id=doc_id,
        metadata={"engine": "synthetic-discovery-pilot"},
    )

    return doc, (t_present, t_absent, t_uncertain), (discovery,)
