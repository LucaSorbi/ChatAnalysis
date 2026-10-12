"""
core/config.py
--------------
Configurazione centralizzata e parametri operativi forensi per ChatAnalysis:
- Limiti dimensionali per upload di grandi file (fino a 2 GB);
- Limiti di sicurezza per archivi ZIP WhatsApp (decompressa, membri, ratio, singolo membro);
- Parametri di streaming e chunking I/O.
"""
from __future__ import annotations

# Limiti Upload Globale Applicativo (coerente con .streamlit/config.toml server.maxUploadSize)
MAX_UPLOAD_SIZE_MB: int = 2048
MAX_UPLOAD_SIZE_BYTES: int = MAX_UPLOAD_SIZE_MB * 1024 * 1024  # 2.147.483.648 byte (2 GB)

# Dimensione chunk per streaming hashing SHA-256 e scrittura su disco temporaneo
DEFAULT_STREAM_CHUNK_SIZE: int = 1024 * 1024  # 1 MB

# Limiti di Sicurezza e Anti-Zip-Bomb per Archivi WhatsApp ZIP
# A. Dimensione massima FILE COMPRESSO caricato: regolata da MAX_UPLOAD_SIZE_MB (2048 MB)
# B. Dimensione massima totale DECOMPRESSA:
#    Un archivio WhatsApp legittimo da 1-2 GB con molti media multimediali (foto, audio, video)
#    può raggiungere dimensioni decompresse complessive fino a 4 GB.
#    Il limite a 4096 MB consente tali archivi forensi legittimi prevenendo al contempo
#    attacchi DoS / ZIP bomb a espansione infinita (es. 42.zip).
MAX_ARCHIVE_UNCOMPRESSED_SIZE_MB: int = 4096
MAX_ARCHIVE_UNCOMPRESSED_SIZE_BYTES: int = MAX_ARCHIVE_UNCOMPRESSED_SIZE_MB * 1024 * 1024  # 4 GB

# C. Numero massimo di membri all'interno dell'archivio ZIP:
#    Chat WhatsApp estese nel tempo contengono decine di migliaia di media attachments e note vocali.
MAX_ARCHIVE_MEMBERS: int = 50_000

# D. Rapporto di compressione massimo (Compression Ratio) per singolo membro > 1 MB:
#    I media WhatsApp (JPEG, MP4, Opus) hanno ratio tipici tra 1.0 e 2.0; i file di testo tra 3.0 e 10.0.
#    Un rapporto superiore a 100.0 è un chiaro indicatore di compressione anomala / sparse zip-bomb.
MAX_COMPRESSION_RATIO: float = 100.0

# E. Dimensione massima di un singolo membro decompressa (es. singoli video WhatsApp di grandi dimensioni):
MAX_SINGLE_FILE_SIZE_MB: int = 2048
MAX_SINGLE_FILE_SIZE_BYTES: int = MAX_SINGLE_FILE_SIZE_MB * 1024 * 1024  # 2 GB
