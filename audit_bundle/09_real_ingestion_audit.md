# AUDIT COMPLETO FORMATI SORGENTE FORENSI (REAL INGESTION)

Matrice dettagliata di conformità per ciascun formato di file reale supportato nella pipeline ChatAnalysis.

---

## 1. Matrice Comparativa End-to-End

| Caratteristica Pipeline | `WHATSAPP_MSGSTORE` | `WHATSAPP_WA` (Standalone) | `WHATSAPP_EXPORT` (TXT) | `WHATSAPP_EXPORT` (ZIP) | `CELLEBRITE_CSV` | `CELLEBRITE_JSON` | `CELLEBRITE_XML` |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **SourceFormat Enum** (`ui/models.py`) | SÌ (riga 14) | SÌ (riga 15) | SÌ (riga 16) | SÌ (riga 16) | SÌ (riga 17) | SÌ (riga 18) | SÌ (riga 19) |
| **Selezionabile in UI** (`ui/presentation.py:234`) | SÌ | SÌ | SÌ | SÌ | SÌ | SÌ | SÌ |
| **Estensioni Uploader UI** | `db, sqlite` | `db, sqlite` | `txt, zip` | `txt, zip` | `csv` | `json` | `xml` |
| **Classe Importer Concreta** | `WhatsAppMsgstoreImporter` | `WhatsAppWaDbImporter` | `WhatsAppExportImporter` | `WhatsAppExportImporter` | `CellebriteCsvImporter` | `CellebriteJsonImporter` | `CellebriteXmlImporter` |
| **File Sorgente Importer** | `importer/whatsapp_msgstore.py` | `importer/whatsapp_wa.py` | `importer/whatsapp_export.py` | `importer/whatsapp_export.py` | `importer/cellebrite_csv.py` | `importer/cellebrite_json.py` | `importer/cellebrite_xml.py` |
| **Eredita da `BaseImporter`** | SÌ | SÌ | SÌ | SÌ | SÌ | SÌ | SÌ |
| **`can_import()` implementato** | SÌ (riga 48) | SÌ (riga 47) | SÌ (riga 89) | SÌ (riga 89) | SÌ (riga 53) | SÌ (riga 53) | SÌ (riga 62) |
| **`validate_source()` implementato** | SÌ | SÌ | SÌ (riga 46) | SÌ (riga 46) | SÌ | SÌ | SÌ |
| **Preflight Sandbox UI** (`ui/ingestion.py`) | SQLite signature (riga 208) | SQLite signature (riga 208) | Text header (riga 235) | ZIP structure (riga 219) | Validazione CSV (riga 305) | Validazione JSON (riga 305) | Validazione XML (riga 305) |
| **Dispatch Ingestion** (`ui/ingestion.py:265`) | SÌ | SÌ | SÌ | SÌ | SÌ | SÌ | SÌ |
| **Generazione `RawRecord`** | SÌ (record streaming) | SÌ (solo contatti) | SÌ (parsing regex) | SÌ (parsing regex) | SÌ (tabellare) | SÌ (albero JSON) | SÌ (albero XML) |
| **`RecordValidator`** | SÌ | SÌ | SÌ | SÌ | SÌ | SÌ | SÌ |
| **`RecordNormalizer`** | SÌ | SÌ | SÌ | SÌ | SÌ | SÌ | SÌ |
| **`DeterministicEntityResolver`** | SÌ | SÌ (supporto) | SÌ | SÌ | SÌ | SÌ | SÌ |
| **`UnifiedModelBuilder`** | SÌ | NO (solo contatti) | SÌ | SÌ | SÌ | SÌ | SÌ |
| **`MessageEvidenceBundle`** | SÌ | NO | SÌ | SÌ | SÌ | SÌ | SÌ |
| **`ConversationEvidenceDocument`** | SÌ (1 per chat) | NO (0 documenti) | SÌ (1 documento) | SÌ (1 documento) | SÌ (1 per chat) | SÌ (1 per chat) | SÌ (1 per chat) |
| **Indicizzazione `SearchService`** | SÌ | NO | SÌ | SÌ | SÌ | SÌ | SÌ |
| **Topic Detection Area 1 (UI)** | **SÌ** (attiva) | NO | **SÌ** (attiva) | **SÌ** (attiva) | **SÌ** (attiva) | **SÌ** (attiva) | **SÌ** (attiva) |
| **Topic Discovery LIVE (UI)** | **NO (ASSENTE)** | NO | **NO (ASSENTE)** | **NO (ASSENTE)** | **NO (ASSENTE)** | **NO (ASSENTE)** | **NO (ASSENTE)** |
| **Discovery -> Detection (UI)** | **NO (BLOCCATO)**| NO | **NO (BLOCCATO)**| **NO (BLOCCATO)**| **NO (BLOCCATO)**| **NO (BLOCCATO)**| **NO (BLOCCATO)**|

---

## 2. Note Strutturali Emerse dall'Audit

1. **Catena Deterministica Solida**:
   La catena da file grezzo fino a `ConversationEvidenceDocument` e `SearchService` è interamente deterministica, coperta da test e perfettamente funzionante su tutti i formati supportati.
2. **Caso Speciale Standalone `wa.db`**:
   `WhatsAppWaDbImporter` gestisce la tabella `wa_contacts`. Quando importato da solo, restituisce legittimamente `IngestionStatus.AUXILIARY_ONLY` con 0 conversazioni e un messaggio informativo all'utente che suggerisce di usarlo come file companion a `msgstore.db`.
3. **Mancanza di Multimodalità Automatica su File Reali**:
   Quando si importa un export WhatsApp ZIP con immagini o audio, `WhatsAppExportImporter` popola correttamente il campo `media_reference` sul `RawRecord`, ma la pipeline in `ui/ingestion.py` non esegue pipeline multimodali derivate (OCR o trascrizione Whisper) durante l'ingestion né espone un comando per lanciarle on-demand. Di conseguenza, i bundle reali non possiedono evidenze `STT_TRANSCRIPTION` o `VISION_DESCRIPTION`.
4. **Asimmetria Topic Discovery**:
   Topic Discovery e il workflow Discovery -> Detection non sono disponibili per alcun file reale importato a causa dell'assenza dei controlli UI e dell'orchestrazione applicativa.
