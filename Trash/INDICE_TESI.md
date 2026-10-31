# Indice per la tesi — dove trovare cosa

Questo indice **non contiene nuove inferenze né nuovi risultati**: rimanda ai documenti esistenti. Ogni valore riportato
è citato come compare nel documento indicato; qui non è stato rieseguito alcun test LIVE. Etichette: DOC = scritto nel
documento (non riverificato qui); NON IN REPO = presente solo nelle istruzioni del committente, in nessun file del repository.

## Topic Discovery reale (campagna 26 parent)
- **26/26 parent, 140/140 microchunk, 10 topic finali** (DOC; payload verificati contro 261 file grezzi, 638 citazioni
  dei 10 topic, 604 `evidence_id` unici su 1.049): `docs/CHATANALYSIS_AUDIT_FINALE_PROVENANCE_20261027.md`
  (copia identica: `Trash/Analisi_Tesi/CHATANALYSIS_AUDIT_FINALE_PROVENANCE_20261027.md`).
- Risultati e discussione: `docs/CHATANALYSIS_TESI_RISULTATI_E_DISCUSSIONE_20261027.md` (copia identica in `Trash/Analisi_Tesi/`).
- Validazione Discovery reale e correzione a microchunk: `docs/CHATANALYSIS_REAL_DISCOVERY_VALIDATION_20261027.md`,
  `docs/CHATANALYSIS_MICROCHUNK_DISCOVERY_FIX_20261027.md`.
- Causa dei zero topic e recupero motore: `docs/CHATANALYSIS_DISCOVERY_26_CHUNK_ZERO_TOPICS_ROOT_CAUSE_20261026.md`,
  `docs/CHATANALYSIS_FINAL_ENGINE_RECOVERY_REPORT_20261026.md`, `docs/CHATANALYSIS_AUDIT_PROVENIENZA_ZERO_TOPICS.md`.

## Consolidamento dei topic e limiti qualitativi
- Nessuna deduplicazione semantica automatica; etichette quasi equivalenti restano distinte (limite da dichiarare in tesi):
  `docs/DETECTION_MICROCHUNK_RUNBOOK_WINDOWS.md`, sezione 7.

## Topic Detection reale
- Prova Ubuntu iniziale 25/26 e successiva stabilizzazione/validazione live della Detection (DOC):
  `Trash/Analisi_Tesi/CHATANALYSIS_STABILIZZAZIONE_LIVE_FINALE_20261025.md`,
  `Trash/Analisi_Tesi/CHATANALYSIS_REGRESSIONE_25_22_7_DIAGNOSI_20261025.md`,
  `Trash/Analisi_Tesi/CHATANALYSIS_VERIFICA_FINALE_26_OF_26_REPORT.md`,
  `Trash/Analisi_Tesi/CHATANALYSIS_LIVE_24_OF_26_ROOT_CAUSE_REPORT.md`.
- Problemi di attestazione ("UNATTESTED") e veridicità dell'export (DOC):
  `Trash/Audit_Storici/CHATANALYSIS_F1_SEVEN_FAILURES_AND_EXPORT_TRUTHFULNESS_FINAL_REPORT.md`,
  `Trash/Analisi_Tesi/CHATANALYSIS_JSON_EXPORT_SCHEMA_SPECIFICATION.md`,
  `Trash/Audit_Storici/CHATANALYSIS_FULL_EXPERIMENTAL_JSON_EXPORTS_AUDIT_REPORT.md`,
  `Trash/Audit_Storici/CHATANALYSIS_WINDOWS_LIVE_BLOCKERS_FINAL_REPORT.md`.
- Contatore dei microchunk "recuperati" pari a zero e export in 0,386 s nella Detection: **NON IN REPO** (nessun file
  del repository contiene questi valori; non vanno citati in tesi senza il log/run originale).
- Detection a microchunk, checkpoint, resume, prova LIVE limitata (nessun LIVE VERIFIED finché non eseguita):
  `docs/DETECTION_MICROCHUNK_RUNBOOK_WINDOWS.md`.

## Provenienza (provenance)
- `docs/CHATANALYSIS_AUDIT_FINALE_PROVENANCE_20261027.md` e `Trash/Analisi_Tesi/` (stessa copia);
  audit complementari in `Trash/Audit_Storici/` (WAL, snapshot, inode, integrità ZIP).

## Benchmark congelati (NON modificare)
- In radice: `benchmark_frozen_sha256.txt`, `run_benchmark.sh`, `baseline_scalability_measurements.json`,
  `benchmark_corrected_scalability_measurements.json`, `optimized_scalability_measurements.json`,
  `benchmark_checkpoint_persistence_results.json`, `large_file_benchmark_results.json`; misure descritte in `docs/SCALABILITY_MEASUREMENTS_REPORT.md`.

## Runbook di riproduzione
- In radice: `RUNBOOK_UBUNTU.txt`, `UBUNTU_*_RUNBOOK.md`, `UBUNTU_SMOKE_INSTRUCTIONS.md`, `WINDOWS_TOPIC_PIPELINE_RUNBOOK.md`;
  in `docs/`: `DETECTION_MICROCHUNK_RUNBOOK_WINDOWS.md`, `UBUNTU_LIVE_CALIBRATION_GUIDE.md`, `lmstudio-benchmark-runbook.md`.

## Capitolo 3 e sprint
- `Trash/Analisi_Tesi/CHATANALYSIS_CAPITOLO3_ANALISI_TECNICA.md`, `..._CAPITOLO3_STRUTTURA_MANOSCRITTO.md`,
  `Trash/Analisi_Tesi/analisi_tecnica.md`, `Trash/Analisi_Tesi/CHATANALYSIS_SPRINT_TESI_06NOV2026_REPORT.md`.
