"""
tests/unit/test_media_resolver.py
---------------------------------
Test unitari per MediaResolver e MediaResolutionContext:
- Risoluzione sicura entro allowed_roots
- Calcolo incrementale SHA-256 e file size
- Riconoscimento MediaKind da estensione e message_type
- Gestione di file mancanti (MISSING)
- Gestione di messaggi senza riferimento (NO_REFERENCE)
- Blocco di riferimenti remoti (REMOTE_REFERENCE) senza chiamate di rete
- Protezione da Path Traversal (OUTSIDE_ALLOWED_ROOT)
- Blocco di percorsi assoluti esterni alle allowed_roots
- Linkage intra-source WhatsApp media_refs
- Immutabilità profonda di ResolvedMediaAsset
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
from pathlib import Path
import pytest

from importer.models import RawRecord
from normalization.models import (
    CanonicalMessageType,
    NormalizedRecord,
    NormalizedTimestamp,
    TimestampTzStatus,
)
from multimodal.models import MediaKind, MediaResolutionStatus, ResolvedMediaAsset
from multimodal.resolver import (
    MediaResolutionContext,
    MediaResolver,
    compute_sha256_chunked,
)
from unified.models import UnifiedMessage
from validation.models import ValidationResult


def _make_unified_message(
    source_name: str = "msgstore_db",
    source_record_id: str = "1",
    media_reference: str | None = None,
    message_type: CanonicalMessageType = CanonicalMessageType.AUDIO,
) -> UnifiedMessage:
    raw = RawRecord(
        source_name=source_name,
        source_path="/path/test",
        source_record_id=source_record_id,
        record_type="message",
        raw_fields={},
        media_reference=media_reference,
        metadata={},
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
        media_reference=media_reference,
    )
    return UnifiedMessage(
        message_id=f"unified:{source_name}:{source_record_id}",
        source_name=source_name,
        source_record_id=source_record_id,
        source_path="/path/test",
        record_type="message",
        timestamp=ts,
        message_type=message_type,
        media_reference=media_reference,
        provenance_record=norm,
    )


@pytest.mark.unit
class TestMediaResolver:

    def test_require_allowed_roots(self):
        with pytest.raises(ValueError, match="almeno una root"):
            MediaResolver(allowed_roots=[])

    def test_resolve_existing_audio_file(self, tmp_path: Path):
        media_dir = tmp_path / "media"
        media_dir.mkdir()
        audio_file = media_dir / "test_audio.opus"
        content = b"OggS_fake_audio_content_for_testing"
        audio_file.write_bytes(content)

        resolver = MediaResolver(allowed_roots=[tmp_path])
        msg = _make_unified_message(media_reference="media/test_audio.opus", message_type=CanonicalMessageType.AUDIO)

        asset = resolver.resolve(msg)
        assert asset.status == MediaResolutionStatus.RESOLVED
        assert asset.is_resolved is True
        assert asset.media_kind == MediaKind.AUDIO
        assert asset.resolved_path == str(audio_file.resolve())
        assert asset.file_size_bytes == len(content)
        assert asset.sha256 == hashlib.sha256(content).hexdigest()

    def test_resolve_existing_image_file(self, tmp_path: Path):
        img_file = tmp_path / "photo.jpg"
        img_file.write_bytes(b"\xff\xd8\xff_jpeg_fake")

        resolver = MediaResolver(allowed_roots=[tmp_path])
        msg = _make_unified_message(media_reference="photo.jpg", message_type=CanonicalMessageType.IMAGE)

        asset = resolver.resolve(msg)
        assert asset.status == MediaResolutionStatus.RESOLVED
        assert asset.media_kind == MediaKind.IMAGE
        assert asset.file_size_bytes == len(b"\xff\xd8\xff_jpeg_fake")

    def test_resolve_existing_video_file(self, tmp_path: Path):
        vid_file = tmp_path / "clip.mp4"
        vid_file.write_bytes(b"mp4_header_fake")

        resolver = MediaResolver(allowed_roots=[tmp_path])
        msg = _make_unified_message(media_reference="clip.mp4", message_type=CanonicalMessageType.VIDEO)

        asset = resolver.resolve(msg)
        assert asset.status == MediaResolutionStatus.RESOLVED
        assert asset.media_kind == MediaKind.VIDEO

    def test_missing_file_within_allowed_root(self, tmp_path: Path):
        resolver = MediaResolver(allowed_roots=[tmp_path])
        msg = _make_unified_message(media_reference="non_existent.opus", message_type=CanonicalMessageType.AUDIO)

        asset = resolver.resolve(msg)
        assert asset.status == MediaResolutionStatus.MISSING
        assert asset.is_resolved is False
        assert asset.resolved_path is None
        assert asset.sha256 is None
        assert asset.file_size_bytes is None
        assert asset.media_kind == MediaKind.AUDIO

    def test_no_reference_declared(self, tmp_path: Path):
        resolver = MediaResolver(allowed_roots=[tmp_path])
        msg = _make_unified_message(media_reference=None, message_type=CanonicalMessageType.TEXT)

        asset = resolver.resolve(msg)
        assert asset.status == MediaResolutionStatus.NO_REFERENCE
        assert asset.is_resolved is False
        assert asset.raw_reference is None
        assert asset.resolved_path is None

    def test_remote_url_never_fetched(self, tmp_path: Path):
        resolver = MediaResolver(allowed_roots=[tmp_path])
        msg = _make_unified_message(
            media_reference="https://cdn.example.com/audio/voice.opus",
            message_type=CanonicalMessageType.AUDIO,
        )

        asset = resolver.resolve(msg)
        assert asset.status == MediaResolutionStatus.REMOTE_REFERENCE
        assert asset.is_resolved is False
        assert asset.resolved_path is None
        assert asset.metadata.get("remote_url") == "https://cdn.example.com/audio/voice.opus"

    def test_path_traversal_blocked(self, tmp_path: Path):
        safe_root = tmp_path / "sandbox"
        safe_root.mkdir()
        secret_file = tmp_path / "secret.txt"
        secret_file.write_text("sensibile")

        resolver = MediaResolver(allowed_roots=[safe_root])
        msg = _make_unified_message(media_reference="../secret.txt", message_type=CanonicalMessageType.OTHER)

        asset = resolver.resolve(msg)
        assert asset.status == MediaResolutionStatus.OUTSIDE_ALLOWED_ROOT
        assert asset.is_resolved is False
        assert asset.resolved_path is None

    def test_absolute_path_outside_allowed_roots_blocked(self, tmp_path: Path):
        safe_root = tmp_path / "sandbox"
        safe_root.mkdir()
        outside_file = tmp_path / "outside.opus"
        outside_file.write_bytes(b"audio")

        resolver = MediaResolver(allowed_roots=[safe_root])
        msg = _make_unified_message(media_reference=str(outside_file.resolve()), message_type=CanonicalMessageType.AUDIO)

        asset = resolver.resolve(msg)
        assert asset.status == MediaResolutionStatus.OUTSIDE_ALLOWED_ROOT
        assert asset.is_resolved is False
        assert asset.resolved_path is None

    def test_chunked_sha256_computation(self, tmp_path: Path):
        large_file = tmp_path / "test_sha.bin"
        # 200 KB di dati
        data = b"0123456789ABCDEF" * 12800
        large_file.write_bytes(data)

        expected_hash = hashlib.sha256(data).hexdigest()
        computed = compute_sha256_chunked(large_file, chunk_size=1024)
        assert computed == expected_hash

    def test_deep_immutability_of_resolved_media_asset(self, tmp_path: Path):
        asset = ResolvedMediaAsset(
            message_id="msg:1",
            source_name="src",
            source_record_id="1",
            raw_reference="audio.opus",
            resolved_path=None,
            media_kind=MediaKind.AUDIO,
            status=MediaResolutionStatus.MISSING,
            metadata={"nested": {"field": "val"}},
        )
        with pytest.raises(FrozenInstanceError):
            asset.status = MediaResolutionStatus.RESOLVED  # type: ignore[misc]

        with pytest.raises(TypeError):
            asset.metadata["nested"]["field"] = "hacked"  # type: ignore[index]


@pytest.mark.unit
class TestMediaResolutionContext:

    def test_whatsapp_media_refs_linkage(self, tmp_path: Path):
        media_file = tmp_path / "WhatsApp Audio" / "AUD_00001.opus"
        media_file.parent.mkdir(parents=True)
        media_file.write_bytes(b"audio_bytes")

        # Record media_ref simulato da msgstore.db
        raw_media_ref = RawRecord(
            source_name="msgstore_db",
            source_path="/path/msgstore.db",
            source_record_id="ref_10",
            record_type="media_ref",
            raw_fields={
                "message_row_id": 101,
                "file_path": "WhatsApp Audio/AUD_00001.opus",
                "file_size": 11,
                "media_type": 2,
                "media_job_uuid": "uuid-1234",
            },
            media_reference="WhatsApp Audio/AUD_00001.opus",
            metadata={},
        )
        ctx = MediaResolutionContext.from_records([raw_media_ref])
        assert ctx.get_msgstore_ref("101") is not None
        assert ctx.get_msgstore_ref("101")["file_path"] == "WhatsApp Audio/AUD_00001.opus"

        # Messaggio senza media_reference esplicito (sfrutta fallback da context)
        resolver = MediaResolver(allowed_roots=[tmp_path], context=ctx)
        msg = _make_unified_message(source_name="msgstore_db", source_record_id="101", media_reference=None)

        asset = resolver.resolve(msg)
        assert asset.status == MediaResolutionStatus.RESOLVED
        assert asset.is_resolved is True
        assert asset.media_kind == MediaKind.AUDIO
        assert asset.metadata.get("resolved_from_media_ref_fallback") is True
        assert asset.metadata["whatsapp_media_ref"]["media_job_uuid"] == "uuid-1234"
