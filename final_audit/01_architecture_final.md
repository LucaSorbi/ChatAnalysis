# Architettura Finale ChatAnalysis (Release Candidate)

## 1. Principi Guida del Refactoring Finale
Il sistema ChatAnalysis è stato consolidato architetturalmente per eliminare ogni forma di patchwork, sincronizzazione manuale o fallback debole. Il risultato presenta:
1. **Unica Fonte di Verità**: Lo stato dell'applicazione è rigorosamente confinato all'identificatore del documento attivo (`active_document_id`) e a un unico dizionario di sessione `analysis_by_document[document_id]`.
2. **Immutabilità e Modello a Run**: Ogni analisi di Topic Detection, Topic Discovery o Consistency produce un record immutabile con identificatore univoco (`run_id`), metadati completi e datazione ISO. La cronologia delle esecuzioni è tracciata in modo ordinato e non distruttivo.
3. **Indice delle Evidenze Scoped e Riutilizzabile**: L'indice vettoriale/lessicale `EvidenceIndex` viene costruito una sola volta per documento e memorizzato nello stato runtime, eliminando la ri-indicizzazione a ogni operazione AI.
4. **Isolamento Rigoroso dei Modelli**: Distinzione tipizzata tra OperationalModelSpec (Qwen2.5, Llama3.1, DeepSeek-R1-Distill) con divieto assoluto di cross-family matching. Gestione corretta dell'identità del modello LM Studio (`model_key`, `instance_id`, `api_identifier`).
5. **Robustezza Multi-Chunk**: Accounting esaustivo di ogni chunk (completed, failed, expected). Nessun fallimento silenzioso. Fallimento totale = OperationalAnalysisFailedError. Normalizzazione deterministica con Unicode NFKC, casefold, pulizia punteggiatura e Deterministic Support Ranking per `max_topics`.
6. **Integrità Forense dell'Ingestion**: Streaming riga per riga rigoroso con decodifica `errors="strict"` (nessun replacement U+FFFD silenzioso). Pipeline a generatori senza copie ridondanti in RAM. Distinzione esplicita tra `MAX_UPLOAD_SIZE = 2 GB` e budget di memoria operativa.
7. **Trasparenza Multimodale**: Dichiarazione veritiera delle capacità media reali (i file reali generano evidenze `ORIGINAL_TEXT`; elaborazione STT/OCR/Vision avviene solo su esplicita richiesta offline se i motori locali sono configurati).

---

## 2. Diagramma di Flusso Orchestrazione AI e Ingestion

```mermaid
graph TD
    Upload[Upload File TXT / ZIP] --> Preflight[Preflight & Validazione ZIP/Firma]
    Preflight --> IngestStream[Streaming Line-by-Line & Strict UTF-8]
    IngestStream --> Normalize[Record Normalizer]
    Normalize --> UnifiedBuild[Unified Model Builder]
    UnifiedBuild --> DocBuild[ConversationEvidenceDocument Immutabile]
    DocBuild --> IndexBuild[Costruzione Scoped EvidenceIndex (Singola)]
    IndexBuild --> RunState[DocumentAnalysisState Scoped]
    
    subgraph Orchestrazione AI
        RunState --> Service[LocalTopicAnalysisService]
        Service --> ChunkSplit[Suddivisione Chunk Conservativa]
        ChunkSplit --> LMStudio[Client LM Studio / Spec Risolta]
        LMStudio --> Aggregator[Aggregatore con Support Ranking]
        Aggregator --> ResultRun[TopicRun Immutabile con run_id]
        ResultRun --> RunState
    end
```

---

## 3. Garanzie di Provenance e Prova del Nove
- `verify_discovered_topic` verifica a livello di application service che il topic appartenga al documento attivo e a un risultato di Discovery verificato.
- `ConsistencyService.evaluate` rifiuta categoricamente discrepanze cross-documento.
- Errori operativi durante l'inferenza propagano errori controllati senza generare falsi risultati di consistenza (`DISCREPANCY_UNCERTAIN`).
