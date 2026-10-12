# ARCHITETTURA FINALE E COMPLETA - CHATANALYSIS

## 1. Topologia Architetturale

```
SOURCE (WhatsApp msgstore/wa/TXT/ZIP, Cellebrite CSV/JSON/XML)
  |
  v
INGESTION (0% AI, deterministica, streaming a chunk line-by-line, sandbox forense)
  |
  v
ConversationEvidenceDocument (immutabile, deep immutability, full provenance)
  |
  v
DOCUMENT-SCOPED ANALYSIS SESSION (ui/state.py, scoped per document_id)
  |
  +---------------------------------------+
  |                                       |
  v                                       v
TOPIC DISCOVERY SERVICE          TOPIC DETECTION SERVICE
(LocalTopicAnalysisService)      (LocalTopicAnalysisService)
  * multi-chunking trasparente     * multi-chunking trasparente
  * aggregazione deterministica    * fail-safe: mai falso ABSENT su errore
  * metadati preservati            * operational semantic directive
  |                                       |
  +-------------------+-------------------+
                      |
                      v
            CONSISTENCY SERVICE
            (ConsistencyService)
            * CONSISTENT (PRESENT con overlap)
            * PARTIALLY_CONSISTENT (PRESENT senza overlap)
            * DISCREPANCY_ABSENT (ABSENT)
            * DISCREPANCY_UNCERTAIN (UNCERTAIN)
                      |
                      v
                SEARCH SERVICE
                (SearchService & TopicSearchEngine)
                * Ricerca deterministica
                * Preservazione metadati: model, latency, run_id, origin
                      |
                      v
                STREAMLIT UI
                (Presentation / App)
                * View pura: zero logica di business AI
                * Nessun preflight duplicato
                * Nessun banner obsoleto
```

## 2. Componenti Chiave

1. **`analysis/service.py` (`LocalTopicAnalysisService`)**:
   - Gestore autoritativo e unificato per tutte le inferenze operative.
   - Lifecycle LM Studio centralizzato: verifica loopback, risoluzione modello, load atomico, singola istanza client.
   - Supporto integrato al multi-chunking con chunk budget conservativo per non superare il context 8192.
   - Regole di aggregazione rigorose: `PRESENT` se almeno un chunk è PRESENT; `ABSENT` solo se TUTTI i chunk hanno successo e sono ABSENT; `UNCERTAIN` se ci sono errori o incertezze (mai falso ABSENT).

2. **`analysis/consistency.py` (`ConsistencyService`)**:
   - Valutatore formale della consistenza tra Topic Discovery e Topic Detection ("Prova del Nove").
   - Totalmente disaccoppiato da presentation.py.

3. **`ui/state.py`**:
   - Modello di sessione riscritto: stato document-scoped tipizzato via `DocumentAnalysisState`.
   - Cambio conversazione A -> B -> A ripristina fedelmente lo stato di A senza alcuna cancellazione o contaminazione.
   - Separazione netta tra risultati di riferimento DEMO (`reference_detection_results`, `reference_discovery_results`) e risultati live (inizialmente vuoti `()`, popolati solo su richiesta esplicita).

4. **`importer/whatsapp_export.py` & Streaming**:
   - Decodifica incrementale line-by-line con `TextIOWrapper` e campionamento iniziale encoding.
   - Nessun caricamento integrale in RAM (`read_bytes()`, `splitlines()`) per transcript di grandi dimensioni.
   - Protezione traversal cross-platform normalizzata contro `../`, `..\`, percorsi assoluti Windows/POSIX e UNC.

5. **`run_benchmark.sh`**:
   - Aggiunto `--context-length "$CONTEXT_LENGTH"` (8192) per allineare l'avvio LM Studio con i metadati runtime.
   - Modalità Git 100755 e terminatori LF preservati.
