# AUDIT TOPIC DISCOVERY (`TopicDiscoveryAnalyzer`)

Analisi tecnica dettagliata del componente `TopicDiscoveryAnalyzer` responsabile della scoperta aperta dei temi emergenti.

---

## 1. Firma e Parametri di `discover_topics()`

```python
def discover_topics(
    self,
    document: ConversationEvidenceDocument,
    strategy: AnalysisLanguageStrategy = AnalysisLanguageStrategy.DIRECT_MULTILINGUAL,
    translation: EvidenceTranslationResult | None = None,
    model_id: str | None = None,
    max_topics: int = 10,
    temperature: float = 0.0,
    seed: int | None = 42,
    timeout_seconds: float = 30.0,
    max_tokens: int | None = None,
) -> TopicDiscoveryResult:
```

* **Prompt Version**: `topic_discovery_v1` (riga 50 in `ai/topics.py`).
* **Max Topics**: Massimo `10` argomenti (vincolo rigido: se il modello genera più di `max_topics`, `validate_topic_discovery_payload` solleva `AiStructuredOutputError` senza troncamento silenzioso).
* **Temperatura**: `0.0`.
* **Seed**: `42`.
* **Timeout**: Default `30.0`s.

---

## 2. Prompt di Sistema (`topic_discovery_v1`)

```text
CRITICAL FORENSIC DATA INTEGRITY AND SECURITY DIRECTIVE:
...
TASK: OPEN TOPIC DISCOVERY (Version: topic_discovery_v1)
Analyze the forensic conversation evidence and discover the main topics or subjects discussed.
Find up to {max_topics} distinct topics.

ETHICAL FORENSIC CONSTRAINTS:
- DO NOT infer personal identities, real names, or demographic profiles.
- DO NOT generate psychological or character assessments.
- Describe ONLY objective subjects, factual events, activities, or matters discussed in the text.

OUTPUT RULES:
1. Provide a concise 'label' for each topic (in Italian).
2. Provide a 'short_description' explaining the topic objectively (in Italian).
3. Cite the exact 'evidence_ids' supporting each topic. Each topic must cite at least one valid evidence_id.
4. Return an array of topics matching schema, with at most {max_topics} elements.
```

---

## 3. Schema JSON e Validazione

* **Schema (`TOPIC_DISCOVERY_SCHEMA`)**:
  ```json
  {
    "type": "object",
    "properties": {
      "topics": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "label": {"type": "string"},
            "short_description": {"type": "string"},
            "evidence_ids": {"type": "array", "items": {"type": "string"}}
          },
          "required": ["label", "short_description", "evidence_ids"],
          "additionalProperties": false
        }
      }
    },
    "required": ["topics"],
    "additionalProperties": false
  }
  ```
* **Validatore (`validate_topic_discovery_payload` in `ai/structured.py`)**:
  - Verifica assenza di chiavi extra;
  - Verifica che ciascun `evidence_id` citato sia presente in `valid_evidence_ids`;
  - Impone che ogni topic citi almeno un `evidence_id` valido;
  - Rifiuta duplicati;
  - Rifiuta array eccedenti `max_topics`.

---

## 4. Verifica Disponibilità Topic Discovery LIVE nella UI

### Modalità DEMO (`DatasetMode.DEMO`):
* **LIVE? NO**.
* I topic emersi nella UI all'avvio derivano unicamente da una tupla hardcoded in `ui/demo.py:build_synthetic_demo_dataset()`. Non viene mai effettuata una chiamata LLM live per il Discovery in modalità DEMO.

### Modalità FILE (`DatasetMode.FILE`):
* **LIVE? NO (ASSENTE DA CODICE)**.
* In `ui/presentation.py`, il tab "Topic Discovery" si limita a invocare `search_topic_discoveries()`. Poiché all'ingestion di un file reale `state.set_discovery_results(())` viene impostato a vuoto e **non esiste alcun pulsante o controller per lanciare `discover_topics()`**, l'interfaccia mostra costantemente 0 argomenti emersi.
* Di conseguenza, l'utente non può eseguire la Topic Discovery né il flusso Discovery -> Detection su alcuna chat reale importata.
