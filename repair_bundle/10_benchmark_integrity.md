# INTEGRITÀ DEL BENCHMARK SPERIMENTALE

## 1. Moduli e Dataset Intatti
- `ai/experiment.py`: INVARIATO (0 diff)
- `ai/benchmark.py`: INVARIATO (0 diff)
- `test_data/ai_benchmark/`: INVARIATO (0 diff)
- Ground truth e prompt di benchmark: INVARIATI (0 diff)

## 2. Unica Modifica Intenzionale in run_benchmark.sh
Aggiunto l'argomento mancante `--context-length "$CONTEXT_LENGTH"` (8192) alla chiamata di `ai/experiment.py`:
```diff

```