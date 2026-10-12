# BLOCKER 2 — Discovery Provenance e Consistency

## 1. Problema Originale
`analysis/service.py` e `analysis/consistency.py` consentivano di verificare un `DiscoveredTopic` privo di vincolo documentale stringente, permettendo l'inoltro di evidence ID appartenenti a un documento estraneo o mancanti di una discovery run tracciabile.

## 2. Causa Radice
La verifica non convalidava a livello di domain service l'esistenza del `discovery_run_id`, la provenienza del topic rispetto al `ConversationEvidenceDocument`, e passava frammenti informativi non isolati alla fase di detection.

## 3. Decisione Architetturale
Flusso di isolamento inferenziale e validazione di provenienza:
```text
ConversationEvidenceDocument
       ↓
TOPIC DISCOVERY LIVE → TopicDiscoveryResult (con run_id)
       ↓
DiscoveredTopicRef (document_id + discovery_run_id + DiscoveredTopic)
       ↓
PROVENANCE VALIDATION (document check, run_id check, evidence existence check)
       ↓
NEW INDEPENDENT TOPIC DETECTION (riceve SOLO label e short_description)
       ↓
TopicDetectionResult
       ↓
ConsistencyService.evaluate (confronto a posteriori degli evidence_ids)
```
- Introdotta l'eccezione tipizzata `TopicProvenanceError` in `analysis/models.py`.
- Introdotto `DiscoveredTopicRef` che incapsula immutabilmente `document_id`, `discovery_run_id` e `topic`.
- In `verify_discovered_topic`:
  1. Validazione identità del documento.
  2. `discovery_run_id` rigorosamente obbligatorio (mai vuoto o fittizio).
  3. Validazione che tutti gli `evidence_ids` del topic scoperto esistano nel documento (rifiuto ID estranei).
  4. La Topic Detection viene chiamata con una nuova query contenente **esclusivamente** `label` e `short_description` (nessun hint o evidence ID della discovery).
  5. Validazione che tutti gli `evidence_ids` restituiti dalla detection appartengano al documento.
  6. Valutazione di consistenza a posteriori con i 4 stati canonici (`CONSISTENT`, `PARTIALLY_CONSISTENT`, `DISCREPANCY_ABSENT`, `DISCREPANCY_UNCERTAIN`).

## 4. File Modificati
- `analysis/models.py`: Aggiunta `TopicProvenanceError`, `DiscoveredTopicRef`.
- `analysis/__init__.py`: Esportazione nuovi tipi.
- `analysis/service.py`: Implementazione validazioni rigide in `verify_discovered_topic`.
- `tests/unit/test_discovery_provenance_service.py`: 13 test di conformità per tutte le casistiche richieste.

## 5. Test Aggiunti ed Esiti
- `test_discovery_a_document_a_allowed`: PASSED
- `test_discovery_a_document_b_rejected`: PASSED
- `test_foreign_evidence_id_in_discovered_topic_rejected`: PASSED
- `test_foreign_evidence_id_in_detection_result_rejected`: PASSED
- `test_missing_discovery_run_rejected`: PASSED
- `test_invalid_topic_reference_rejected`: PASSED
- `test_multi_conversation_isolation_and_history_preservation`: PASSED
- `test_discovery_evidence_ids_not_passed_to_detection`: PASSED
- `test_detection_generates_independent_llm_call`: PASSED
- Matrice consistency 4 stati: 4 test PASSED.

## 6. Stato di Verifica: MOCK_VERIFIED
