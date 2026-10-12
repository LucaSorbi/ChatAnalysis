# AUDIT DEL SERIALIZER E DELLA STRUTTURA DELLE EVIDENZE

Verifica dettagliata del meccanismo di serializzazione dei documenti forensi (`ConversationEvidenceDocument`) nel payload JSON sicuro inviato agli LLM locali.

---

## 1. Tracciamento della Catena di Serializzazione

```text
ConversationEvidenceDocument
          ↓
document.bundles (tuple[MessageEvidenceBundle, ...])
          ↓
bundle.text_evidence_sections (tuple[TextEvidenceSection, ...])
          ↓
document.all_evidence_sections (ordinamento deterministico per bundle e source_type)
          ↓
serialize_document_for_llm()
          ↓
JSON Payload delimitato:
=== BEGIN FORENSIC EVIDENCE DATA (UNTRUSTED RAW JSON) ===
{ ... }
=== END FORENSIC EVIDENCE DATA ===
          ↓
messages: [{"role": "system", ...}, {"role": "user", "content": <serialized_json>}]
          ↓
client.chat_completion()
```

---

## 2. Analisi Dettagliata per Ciascuna `EvidenceSourceType`

| Source Type | Dove viene Creato | Dove viene Aggregato | Entra in `all_evidence_sections`? | Serializzato in JSON? | Inviato a Detection? | Inviato a Discovery? |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: |
| `ORIGINAL_TEXT` | `UnifiedMessage.text_content` | `MessageEvidenceBundle.text_evidence_sections` (riga 238) | **SÌ** | **SÌ** | **SÌ** | **SÌ** |
| `STT_TRANSCRIPTION` | `AudioTranscriber` (`multimodal/transcriber.py`) | `MessageEvidenceBundle.audio_transcription` (riga 256) | **SÌ** | **SÌ** | **SÌ** | **SÌ** |
| `OCR_TEXT` | `ImageTextExtractor` (`multimodal/ocr.py`) | `MessageEvidenceBundle.image_ocr` (riga 278) | **SÌ** | **SÌ** | **SÌ** | **SÌ** |
| `VISION_DESCRIPTION` | `ImageVisionAnalyzer` (`multimodal/vision.py`) | `MessageEvidenceBundle.image_vision` (riga 300) | **SÌ** | **SÌ** | **SÌ** | **SÌ** |
| `VISION_OBSERVATION` | `ImageVisionAnalyzer.observations` | `MessageEvidenceBundle.image_vision` (riga 322) | **SÌ** | **SÌ** | **SÌ** | **SÌ** |

---

## 3. Formato del Singolo Oggetto JSON Serializzato

Ogni sezione viene mappata tramite `serialize_evidence_section_dict()` (`ai/serializer.py:49-65`) nel formato rigoroso:

```json
{
  "evidence_id": "msg::5::VISION_DESCRIPTION",
  "source_type": "VISION_DESCRIPTION",
  "message_id": "msg::5",
  "source_name": "demo_msgstore_db",
  "source_record_id": "105",
  "language": null,
  "ordinal": null,
  "text": "Un magazzino industriale con diverse casse etichettate e una porta di sicurezza blindata."
}
```

---

## 4. Rilievi Critici e Asimmetria Riscontrata

1. **A livello di Serializer (`ai/serializer.py`)**:
   Tutte le 5 sorgenti di evidenza sono gestite e serializzate senza alcuna esclusione. Il JSON escaping tramite `json.dumps(..., ensure_ascii=False)` neutralizza attacchi di prompt injection o delimitatori spuri.
2. **A livello di Pipeline Ingestion Reale (`ui/ingestion.py`)**:
   Esiste un'asimmetria critica tra DEMO e FILE reale:
   - Nel **DEMO** (`ui/demo.py`), i bundle contengono istanze popolate di `audio_transcription`, `image_ocr` e `image_vision`, rendendo visibili tutte le 5 sorgenti.
   - Nei **FILE REALI** (`ui/ingestion.py:428`), i bundle vengono creati unicamente con `MessageEvidenceBundle(message=m)`. Nessun componente multimodale (Whisper STT o OCR o Vision) viene eseguito durante l'ingestion o post-ingestion, pertanto i documenti reali contengono **solo ed esclusivamente sezioni `ORIGINAL_TEXT`**.
