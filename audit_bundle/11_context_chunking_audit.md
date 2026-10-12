# AUDIT CHUNKING, CONTEXT WINDOW E GESTIONE CONVERSAZIONI ESTESE

Analisi critica del comportamento del sistema quando una conversazione reale supera la dimensione della finestra di contesto dell'LLM locale.

---

## 1. Il Paradosso Architetturale del Chunking

### Esistenza del Modulo `ai/chunking.py`
Nel repository esiste un modulo dedicato (`ai/chunking.py`) contenente funzioni formalmente progettate per il chunking:
* `split_document_into_chunks(document, max_characters=8000, max_bundles=25)`: partiziona un documento in sotto-documenti preservando l'atomicità dei bundle;
* `ChunkAnalysisOutcome`: dataclass per tracciare lo stato di analisi di ciascun chunk (`SUCCESS`, `FAILED`);
* Logica di aggregazione *Map-Reduce* con regole tri-stato (`PRESENT` se almeno un chunk è PRESENT; `ABSENT` solo se tutti i chunk sono ABSENT).

### Mancata Integrazione nella Pipeline Operativa
**LA PIPELINE OPERATIVA NON USA IL CHUNKING**:
* In `TopicDetectionAnalyzer.detect_topic()` (`ai/topics.py:145`):
  ```python
  user_content = serialize_document_for_llm(document, strategy=strategy, translation=translation)
  messages = [
      {"role": "system", "content": system_content},
      {"role": "user", "content": user_content},
  ]
  response = self.client.chat_completion(...)
  ```
  L'intero `ConversationEvidenceDocument` viene serializzato in blocco e inviato in un unico payload a LM Studio.
* In `TopicDiscoveryAnalyzer.discover_topics()` (`ai/topics.py:274`):
  L'intero documento viene parimenti serializzato in blocco e inviato in un unico payload.
* **`split_document_into_chunks` non viene mai chiamata** né dalla UI, né da `ui/application.py`, né da `TopicDetectionAnalyzer`. Viene invocata **esclusivamente nei test unitari** (`tests/unit/test_ai_chunking.py`).

---

## 2. Cosa Accade Quando una Conversazione Reale Supera il Context Window?

In una conversazione reale (es. esportazione WhatsApp con migliaia di messaggi):
1. **Serializzazione Massiva**:
   `serialize_document_for_llm` genera un blocco JSON grezzo potenzialmente di centinaia di migliaia di caratteri (es. 200.000–1.000.000 caratteri).
2. **Finestra di Contesto Configurata**:
   In `ai/lmstudio.py:load_model` il parametro è fissato a `context_length = 8192` token (circa 30.000–35.000 caratteri).
3. **Mancanza di Guard Preventivi a Codice**:
   Python non verifica la dimensione in token prima dell'invio via REST. Manda tutto il payload a `POST /v1/chat/completions`.
4. **Comportamento del Backend LLM**:
   - In presenza di server LM Studio con troncamento automatico abilitato nelle impostazioni di runtime, il server effettua un **troncamento silenzioso e non tracciato** delle evidenze (tagliando le ultime migliaia di messaggi).
   - In presenza di validazione stretta, LM Studio restituisce un errore HTTP 400 (context window exceeded), sollevando un'eccezione `AiBackendRequestError` che interrompe l'analisi.
   - Non esiste riassunto automatico, né suddivisione iterativa multi-chunk, né fallback trasparente.

---

## 3. Limiti Hardcoded Riscontrati nel Codice

| File e Parametro | Valore Hardcoded | Effetto / Utilizzo |
| :--- | :---: | :--- |
| `ai/lmstudio.py` (`load_model`) | `context_length = 8192` | Lunghezza finestra di contesto passata a LM Studio REST |
| `ui/application.py` (`DEFAULT_OPERATIONAL_CONTEXT_LENGTH`) | `8192` | Default contestuale per l'auto-load da UI |
| `ui/application.py` (`DEFAULT_OPERATIONAL_MAX_TOKENS`) | `512` | Token massimi generabili per il JSON di risposta |
| `ai/chunking.py` (`split_document_into_chunks`) | `max_characters = 8000` | Budget prudenziale per chunk (usato solo nei test) |
| `ai/chunking.py` (`split_document_into_chunks`) | `max_bundles = 25` | Massimo numero messaggi per chunk (usato solo nei test) |
| `ai/topics.py` (`TopicDiscoveryAnalyzer`) | `max_topics = 10` | Massimo numero di argomenti estraibili dal Discovery |

---

## 4. Conclusioni per il Revisore
Il sottosistema di chunking è un tipico esempio di **componente orfano / disconnesso**: implementato e testato formalmente in isolamento per soddisfare i test unitari delle prime fasi di sviluppo, ma mai integrato nell'orchestrazione operativa della UI né nel flusso di inferenza principale. Per gestire chat reali estese, il modulo deve essere rifattorizzato e integrato attivamente nella pipeline.
