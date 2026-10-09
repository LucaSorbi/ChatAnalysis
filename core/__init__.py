"""
core
----
Package neutrale per utility e strutture condivise trasversalmente tra tutti i layer.
"""
from core.immutability import freeze_structural
from core.config import (
    MAX_UPLOAD_SIZE_MB,
    MAX_UPLOAD_SIZE_BYTES,
    MAX_ARCHIVE_UNCOMPRESSED_SIZE_MB,
    MAX_ARCHIVE_UNCOMPRESSED_SIZE_BYTES,
    MAX_ARCHIVE_MEMBERS,
    MAX_COMPRESSION_RATIO,
    MAX_SINGLE_FILE_SIZE_MB,
    MAX_SINGLE_FILE_SIZE_BYTES,
    DEFAULT_STREAM_CHUNK_SIZE,
)

__all__ = [
    "freeze_structural",
    "MAX_UPLOAD_SIZE_MB",
    "MAX_UPLOAD_SIZE_BYTES",
    "MAX_ARCHIVE_UNCOMPRESSED_SIZE_MB",
    "MAX_ARCHIVE_UNCOMPRESSED_SIZE_BYTES",
    "MAX_ARCHIVE_MEMBERS",
    "MAX_COMPRESSION_RATIO",
    "MAX_SINGLE_FILE_SIZE_MB",
    "MAX_SINGLE_FILE_SIZE_BYTES",
    "DEFAULT_STREAM_CHUNK_SIZE",
]
