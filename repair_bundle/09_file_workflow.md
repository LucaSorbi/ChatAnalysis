# WORKFLOW FILE: INGESTION REALE ED ELABORAZIONE OPERATIVA

## Fasi Operative
1. **Importazione (Zero AI)**:
   - Caricamento del file tramite `execute_real_ingestion()`.
   - Nessuna chiamata AI o contatto con LM Studio durante l'ingestion.
   - Creazione del `ConversationEvidenceDocument` con evidenze forensi integre.
2. **Document-Scoped Session**:
   - `KEY_ANALYSIS_BY_DOCUMENT[document_id]` gestisce lo stato di ogni conversazione.
   - La selezione di un'altra chat commuta lo stato salvando e ripristinando le sessioni senza sovrascritture.
3. **Analisi On-Demand**:
   - Topic Detection manuale su argomento libero.
   - Topic Discovery aperta su tutta la conversazione reale.
   - Prova del Nove: Discovery -> seconda inferenza mirata con Topic Detection.
