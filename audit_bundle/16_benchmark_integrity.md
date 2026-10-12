# BENCHMARK INTEGRITY REPORT

Verifica formale dello stato di congelamento (freeze) e integrità crittografica dei file e della configurazione benchmark.

| File | Esiste? | Git Status | SHA-256 (Current) | Modificato vs HEAD? |
| :--- | :---: | :---: | :--- | :---: |
| `ai/experiment.py` | SÌ | `CLEAN (unmodified)` | `91100c0a84cb4a8b7c2eef34a06cff9ec21aa6e6eec14ffd09ce6fea8bcee845` | **NO** |
| `ai/benchmark.py` | SÌ | `CLEAN (unmodified)` | `dc57081dbce3463a8017aa26bb4e4a18889af0a3296853fe40968561d6d44d36` | **NO** |
| `run_benchmark.sh` | SÌ | `CLEAN (unmodified)` | `690924d21f2d776b65b7eaa0e0284d82cd335242febd0c38fb23956a82ac4aef` | **NO** |
| `test_data/cloud_e2e/ground_truth.json` | SÌ | `CLEAN (unmodified)` | `377fb95b7690ba507481f357449b1a56d1f89cb60636da9debd66fe603e5ad6d` | **NO** |
| `test_data/cloud_e2e/messages.json` | SÌ | `CLEAN (unmodified)` | `2e49a9c62d89125d225726953ff0e879fb49e07d7db803db5503c345b051b781` | **NO** |
| `test_data/cellebrite_export/messages.json` | SÌ | `CLEAN (unmodified)` | `251003fef2f7439fb58d98116b0f5a8116ab6003a6a8b2ebe9a729ac8920f59e` | **NO** |

## Dettaglio Prompt e Versioni di Benchmark

- **Prompt Topic Detection Benchmark**: `topic_detection_v1` (definito in `ai/topics.py:PROMPT_VERSION_DETECTION`, intonso). Utilizzato ogni volta che `operational_mode=False` (default di `TopicDetectionAnalyzer` e standard in `ai/benchmark.py`).
- **Prompt Topic Discovery Benchmark**: `topic_discovery_v1` (definito in `ai/topics.py:PROMPT_VERSION_DISCOVERY`, intonso).
- **Prompt Discovery Reduce Benchmark**: `topic_discovery_reduce_v1` (definito in `ai/chunking.py:PROMPT_VERSION_DISCOVERY_REDUCE`, intonso).
- **Metriche Benchmark**: `check_metadata_discrepancies`, calcolo di precision/recall/F1 tri-stato (`PRESENT`, `ABSENT`, `UNCERTAIN`) e validation error tracking immutati.

## Conclusioni Integrità Benchmark
Tutti i componenti appartenenti al protocollo sperimentale di benchmark della tesi risultano **100% CLEAN** e non hanno subito alcuna modifica rispetto al commit HEAD (`b3765a4`). Nessuna modifica è stata apportata a `ai/experiment.py`, `run_benchmark.sh`, prompt di benchmark o file di ground truth.
