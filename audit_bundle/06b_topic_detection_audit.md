# AUDIT TOPIC DETECTION (`TopicDetectionAnalyzer`)

Analisi tecnica dettagliata del componente `TopicDetectionAnalyzer` responsabile della verifica mirata dei topic.

---

## 1. Firma e Parametri di `detect_topic()`

```python
def detect_topic(
    self,
    document: ConversationEvidenceDocument,
    topic: TopicQuery,
    strategy: AnalysisLanguageStrategy = AnalysisLanguageStrategy.DIRECT_MULTILINGUAL,
    translation: EvidenceTranslationResult | None = None,
    model_id: str | None = None,
    temperature: float = 0.0,
    seed: int | None = 42,
    timeout_seconds: float = 30.0,
    max_tokens: int | None = None,
    operational_mode: bool | None = None,
) -> TopicDetectionResult:
```

### Dettaglio Parametri di Esecuzione Operativa (UI)
* **Modello Effettivo**: Risolto tramite `prepare_operational_model()` (default: `qwen2.5-7b-instruct`).
* **Temperatura**: `0.0` (garantisce massima riproducibilità e determinismo dell'output LLM).
* **Seed**: `42`.
* **Timeout**: `240.0` secondi configurati per la modalità operativa UI (`DEFAULT_OPERATIONAL_TIMEOUT_SECONDS`).
* **Max Tokens**: `512` token per la risposta strutturata JSON.
* **Strategia Linguistica**: `DIRECT_MULTILINGUAL` (nessuna traduzione pregressa richiesta; rationale in italiano).

---

## 2. Prompt di Sistema e Versionamento

### Versione Standard / Benchmark (`topic_detection_v1`)
Attiva quando `operational_mode=False`:
```text
CRITICAL FORENSIC DATA INTEGRITY AND SECURITY DIRECTIVE:
...
TASK: SPECIFIC TOPIC DETECTION (Version: topic_detection_v1)
You must determine if the following topic is discussed or present in the forensic evidence:
TOPIC ID: {topic.topic_id}
LABEL: {topic.label}
DESCRIPTION: {topic.description}

OUTPUT RULES:
1. Return decision 'PRESENT' if and only if the topic is clearly discussed or evidenced in the conversation.
   You MUST cite at least one exact evidence_id in 'evidence_ids'.
2. Return decision 'ABSENT' if the topic is NOT present. 'evidence_ids' MUST be an empty array [].
3. Return decision 'UNCERTAIN' if the evidence is ambiguous, partial, or insufficient to decide. You may cite ambiguous evidence_ids.
4. CITE ONLY EXACT evidence_ids that are present in the evidence block. DO NOT fabricate or invent IDs.
5. Provide a short, objective rationale explaining the decision in Italian. DO NOT provide lengthy chain-of-thought.
```

### Versione Operativa UI (`topic_detection_operational_v1`)
Attiva quando `operational_mode=True` (in `execute_manual_topic_detection` da UI):
Appende al prompt la direttiva semantica esplicita:
```text
MULTIMODAL SEMANTIC DIRECTIVE:
The requested topic may be expressed semantically rather than through literal keyword overlap.

Evaluate the meaning of ALL evidence sources, including:
- original message text,
- speech-to-text transcriptions,
- OCR text,
- image vision descriptions,
- image vision observations.

If the evidence clearly describes the concept represented by the topic, return PRESENT even when the exact words used in the topic label do not appear literally in the evidence.

PRESENT still requires at least one valid evidence_id.
Do not infer beyond the supplied evidence.
```

---

## 3. Schema JSON e Validazione

* **Schema Vincolante (`TOPIC_DETECTION_SCHEMA`)**:
  ```json
  {
    "type": "object",
    "properties": {
      "decision": {"type": "string", "enum": ["PRESENT", "ABSENT", "UNCERTAIN"]},
      "evidence_ids": {"type": "array", "items": {"type": "string"}},
      "rationale": {"type": "string"}
    },
    "required": ["decision", "evidence_ids", "rationale"],
    "additionalProperties": false
  }
  ```
* **Validatore (`validate_topic_detection_payload` in `ai/structured.py`)**:
  - Verifica chiavi esatte (`{"decision", "evidence_ids", "rationale"}`);
  - Nessuna chiave extra consentita;
  - Se `decision == PRESENT`, impone `len(evidence_ids) >= 1`;
  - Se `decision == ABSENT`, impone `len(evidence_ids) == 0`;
  - Rifiuta qualsiasi `evidence_id` allucinato non appartenente a `document.all_evidence_sections` (solleva `AiInvalidEvidenceCitationError`);
  - **Zero Fallback**: Nessuna eccezione viene convertita o degradata in `ABSENT`.

---

## 4. Valutazione sul Sostegno di `VISION_DESCRIPTION`
Sotto il prompt operativo (`topic_detection_operational_v1`), `VISION_DESCRIPTION` è formalmente e semanticamente riconosciuto come fonte idonea a supportare una decisione `PRESENT`. Nel prompt benchmark originario privo di direttiva semantica, i modelli compatti (Qwen 7B) tendevano invece a interpretare restrittivamente la frase *"in the conversation"*, richiedendo sovrapposizione lessicale diretta nei messaggi testuali e producendo `ABSENT`.
