# Quarantena dati potenzialmente sensibili (2026-11-16)

Questi file sono stati spostati qui dalla radice del repository perché sono **artefatti di esecuzione** (database di checkpoint, log e risultati di benchmark, copia di una richiesta a LM Studio) che possono contenere testi, titoli o identificativi derivati da conversazioni reali. **L'applicazione non li usa**: non sono letti da nessun modulo né dai test.

> **Attenzione**: `Trash/` è tracciato da git. Spostare i file qui **non li rimuove** dal repository pubblico né dalla sua cronologia. Per toglierli davvero dalla pubblicazione servono: repository privato, oppure riscrittura della cronologia (`git filter-repo`) con push forzato.

## Ripristino (se mai servisse)

Ogni percorso va riportato alla radice del repository, ad esempio:

```bash
git mv Trash/quarantena_dati_sensibili_2026-11-16/scratch scratch
git mv Trash/quarantena_dati_sensibili_2026-11-16/forensic_provenance_audit_backup_20261027 forensic_provenance_audit_backup_20261027
```

## Contenuto e impronte SHA-256

| Percorso originale (relativo alla radice) | Byte | SHA-256 |
| :--- | ---: | :--- |
| `chat-20261021T182727Z-1-001.zip` | 249982 | `c451f46cd993196c5ca26c83ca18fc0172ccea6fd43689c4406b5458256103e9` |
| `forensic_provenance_audit_backup_20261027/.checkpoints_backup_20261025_regression_checkpoints_backup_20261025_204920.db` | 98304 | `c82ed7a6062b851c673288f890b44ad70e6fdf26702d33d1bbba776d06615867` |
| `forensic_provenance_audit_backup_20261027/.checkpoints_checkpoints.db` | 147456 | `9a5001981f125fd8ddbcdb21eb34045ffe42772569a177060d7cc61447b162dd` |
| `forensic_provenance_audit_backup_20261027/.checkpoints_checkpoints_backup_26of26_verified.db` | 102400 | `d5b80519c8516ccb0c7d2e65a6e562f0674720d285a8d5957380903a8c96762a` |
| `forensic_provenance_audit_backup_20261027/.checkpoints_live_recovery_work_checkpoints.db` | 139264 | `0a1afc91f14121c9dfba40e41ab44b18ed830d08c0cb5ed436b442ad6b0aeb74` |
| `forensic_provenance_audit_backup_20261027/.checkpoints_reconstructed_verified_20261025_checkpoints.db` | 98304 | `c82ed7a6062b851c673288f890b44ad70e6fdf26702d33d1bbba776d06615867` |
| `forensic_provenance_audit_backup_20261027/checkpoints_archive_safety_26of26_run_checkpoints_26of26_backup.db` | 102400 | `d5b80519c8516ccb0c7d2e65a6e562f0674720d285a8d5957380903a8c96762a` |
| `forensic_provenance_audit_backup_20261027/checkpoints_archive_safety_pre_engine_reset_20261026_checkpoints_pre_reset_backup.db` | 167936 | `f0cb18aed81c91ecb8a7278ad7f768d4b181b457edb01df28cc3a4716f572df6` |
| `forensic_provenance_audit_backup_20261027/checkpoints_archive_safety_zero_topics_run_20261026_checkpoints_zero_topics_backup.db` | 147456 | `a8fac0793b06d873e6345bb829e7509ae161e942ff26e515dc93c6935a926c79` |
| `scratch/debug_actual_output.py` | 1515 | `f05d219013cf799ebdec99ae6b1dae4b2c415ab913f8ac8ff2b26ed43871b98b` |
| `scratch/fresh_live_test_store/checkpoints.db` | 61440 | `0f78d819729ede9f1337e1f41aa1b26e3e65047a547962a08463a5e8405ff861` |
| `scratch_call_385_request_body.json` | 1914 | `c427d66e6637b5a1e747a0208afd8857c9106b3b7ada496e8de139175ca0683f` |

## Motivo dello spostamento

- `forensic_provenance_audit_backup_20261027/`: copie di database SQLite dei checkpoint di run su una conversazione WhatsApp (contengono i risultati serializzati della Discovery e gli identificativi del documento).
- `scratch/`: script di debug con percorsi personali (`C:\Users\...`) e un database di checkpoint di prova.
- `scratch_call_385_request_body.json`: copia di una richiesta inviata a LM Studio.
- `chat-20261021T182727Z-1-001.zip`: archivio di risultati e log di benchmark (incluse esecuzioni su conversazioni reali, cartella `test_reali/`).
