# Isolamento Multi-Conversazione nel Session State (Fase 2, 4)

## 1. Architettura a Singola Fonte di Verità
In `ui/state.py`:
- Lo storage è organizzato esclusivamente nella mappa di sessione `analysis_by_document[document_id]`.
- La conversazione correntemente visualizzata nella UI è tracciata esclusivamente dalla chiave `active_document_id`.
- Tutte le proprietà accessorie (`detection_results`, `discovery_results`, `consistency_results`) sono **viste derivate pure** calcolate a partire dal `DocumentAnalysisState` del documento attivo.
- È stata eliminata ogni sincronizzazione bidirezionale manuale tra variabili globali duplicate e stato per-documento.

## 2. Scenario di Test A -> B -> A
1. L'utente seleziona la conversazione A ed esegue Topic Discovery o Detection. I risultati vengono salvati in `analysis_by_document["A"]`.
2. L'utente seleziona la conversazione B: l'interfaccia si azzera mostrando lo stato vergine di B, senza alcuna contaminazione da A.
3. L'utente ritorna sulla conversazione A: tutti i risultati precedenti (decisioni, evidenze, consistenza) vengono ripristinati fedelmente dalla cronologia delle run di A.

Il test `test_multiconversation_preserves_results_across_switches` in `tests/unit/test_apptest_flows.py` certifica formalmente questo comportamento.
