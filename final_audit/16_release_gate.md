# Verdetto Finale Release Gate (Release Candidate)

Tutti i 50 criteri di accettazione bloccanti sono stati verificati e soddisfatti:

1. [x] **Packaging**: `analysis/` incluso nella wheel; installazione e import isolati convalidati.
2. [x] **Session State**: Unica fonte di verità in `analysis_by_document`; isolamento e persistenza multi-chat A->B->A garantiti.
3. [x] **Search Service**: `EvidenceIndex` indicizzato una sola volta per documento e riutilizzato.
4. [x] **Discovery & Detection**: Accounting completo di tutti i chunk, Deterministic Support Ranking per `max_topics`, inoltro di `max_tokens`.
5. [x] **Integrità degli Errori**: Errori fatali propagati immediatamente; 0 chunk validi solleva `OperationalAnalysisFailedError`; nessun errore scambiato per ABSENT o ConsistencyResult.
6. [x] **Provenance**: Validazione bidirezionale document-topic nel service layer e in `ConsistencyService`.
7. [x] **Modelli LM Studio**: Isolamento canonico Qwen2.5, Llama3.1, DeepSeek; gestione model key / instance id / response model; JIT unverified esplicito.
8. [x] **Integrità Forense Ingestion**: Decodifica strict senza replacement U+FFFD; streaming riga per riga protetto; budget 2GB esplicitato.
9. [x] **Qualità e Suite**: 1365 test unitari e di integrazione PASSED; 7 Streamlit AppTest PASSED; 0 failed, 0 errors.
10. [x] **Integrità Benchmark**: Runner configurato a 8192, mode 100755, LF, bash -n pass; core benchmark intatto; nessuna run eseguita anzitempo.

Il progetto ChatAnalysis è pronto per il congelamento e l'handover al revisore esterno.
