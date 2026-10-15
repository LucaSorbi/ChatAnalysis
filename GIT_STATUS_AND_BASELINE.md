# CHATANALYSIS — STATO GIT E BASELINE DI RIFERIMENTO (RELEASE 2026-10-15)

## 1. Commit Baseline e Riferimento Git

- **Branch attivo**: `main`
- **Ultimo commit tracciato**: `6c1dc468c4d2fe94e9fcf0963aa36fc48bc9568b`
- **Politica di rilascio**: Nessun commit né push eseguito autonomamente (modifiche fornite come patch unificata UTF-8 `git_diff_sqlite_legacy_hardening.patch`).

---

## 2. File Modificati Tracciati e Sintesi delle Correzioni

I seguenti file contengono modifiche rispetto a `HEAD`:

- `analysis/checkpoint.py`:
  - **Correzione 1 (Preservazione Legacy Valido con SQLite Corrotto)**: Distinzione categorica di chunk assente, valido, divergente e corrotto. In presenza di chunk SQLite non parsabili, viene registrato un conflitto esplicito, inibita qualsiasi sovrascrittura arbitraria o eliminazione prematura del file legacy valido. La verifica analitica pre-unlinking esamina biunivocamente indici, payload e parsabilità dei chunk senza fare affidamento su `COUNT(*)`.
  - **Correzione 2 (Preservazione File Legacy Non Decodificabili)**: In `clear_all_for_document_id()` e `clear_for_document()`, eliminazione del pattern `except Exception: pass; unlink()`. Adottata un'unica procedura di attribuzione verificata: i file danneggiati o con prefisso compatibile non decodificabili vengono rigorosamente preservati su disco per finalità forensi e segnalati con diagnostica esplicita (`logger.warning`), così come i file appartenenti ad altri documenti.
  - **Correzione 3 (Validazione Rigorosa Metadati Pre-Fusione)**: Prima di importare chunk da file legacy, vengono confrontati punto per punto con SQLite: `document_id`, `analysis_type`, `model_id`, `prompt_version`, `total_planned_chunks`, indici dei chunk e provenienza documentale. In caso di incompatibilità, la testata SQLite rimane inalterata, non viene applicato alcun aggiornamento `MAX(total_planned_chunks, ...)`, i chunk alieni vengono respinti e il file legacy viene preservato.
- `tests/unit/test_checkpoint_integrity_scalability.py`:
  - Aggiunti i test specialistici 24, 25, 26, 27, 28, 29 per riprodurre e validare le tre correzioni e tutti i 12 requisiti del protocollo di hardening.
  - Totale test specialistici: **29 su 29 superati**.
- `scripts/generate_sqlite_legacy_hardening_patch_utf8.py`: Script di generazione della patch unificata UTF-8.
- `scripts/package_sqlite_legacy_hardening_zip.py`: Script di confezionamento dell'archivio di release con verifica CRC e calcolo SHA-256.

---

## 3. Risultati della Suite di Test

- **Pytest completo**: **1762 superati**, 1 saltato, 5 esclusi (benchmark congelati), **0 fallimenti** (in `pytest_and_apptest.log`).
- **Suite Specialistica Integrità e Scalabilità**: **29 su 29 superati**.
- **Streamlit AppTest (`test_apptest_flows.py`)**: **7 su 7 superati**.
- **Packaging e Integrità Wheel (`test_packaging_release.py`)**: **1 su 1 superato** (89 sorgenti Python, import isolato confermato).

---

## 4. Verifica Hash File Scientifici Congelati

I tre file scientifici frozen non sono stati modificati e i rispettivi hash crittografici SHA-256 corrispondono esattamente:

| File | SHA256 Rilevato | SHA256 Atteso (`benchmark_frozen_sha256.txt`) | Esito |
| :--- | :--- | :--- | :---: |
| `ai/experiment.py` | `91100C0A84CB4A8B7C2EEF34A06CFF9EC21AA6E6EEC14FFD09CE6FEA8BCEE845` | `91100C0A84CB4A8B7C2EEF34A06CFF9EC21AA6E6EEC14FFD09CE6FEA8BCEE845` | **CONFORME** |
| `ai/benchmark.py` | `DC57081DBCE3463A8017AA26BB4E4A18889AF0A3296853FE40968561D6D44D36` | `DC57081DBCE3463A8017AA26BB4E4A18889AF0A3296853FE40968561D6D44D36` | **CONFORME** |
| `run_benchmark.sh` | `7F9CAE97DF2FAE1BC3B5964E639B9F80E3AC5A388EC9C3E20305FF64D48044A7` | `7F9CAE97DF2FAE1BC3B5964E639B9F80E3AC5A388EC9C3E20305FF64D48044A7` | **CONFORME** |
