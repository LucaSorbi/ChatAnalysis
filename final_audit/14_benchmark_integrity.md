# Integrità del Benchmark Core e del Runner Bash (Fase 48, 49)

## 1. File del Core Benchmark (Completamente Immutati)
- `ai/experiment.py`: intatto;
- `ai/benchmark.py`: intatto;
- Prompt e Ground Truth di benchmark: intatti;
- Dataset di benchmark: intatti.

## 2. Verifica Script run_benchmark.sh
- Git Mode: 100755 2f6539bb27429bbc060e6eb593ce467aa340aa06 0	run_benchmark.sh
- Sintassi Bash (bash -n): PASS (0 syntax errors)
- Terminatori di Riga: Rigorosamente LF (b'\r\n' assente, termina con '\n');
- Parametro Context Length: `--context-length "$CONTEXT_LENGTH"` con CONTEXT_LENGTH=8192.

## 3. Divieto di Esecuzione delle 9 Run Finali
Le 9 run di benchmark finale NON sono state eseguite in questa fase, come tassativamente richiesto.