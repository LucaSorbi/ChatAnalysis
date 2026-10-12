# Audit Integrità del Benchmark Scientifico Congelato

## 1. Vincoli Tassativi
- NESSUNA modifica ai file core del benchmark: `ai/experiment.py`, `ai/benchmark.py`.
- NESSUNA alterazione dei dataset di benchmark, delle ground truth e dei prompt congelati.
- NESSUNA esecuzione anzitempo dei 9 run di benchmark ufficiali.

## 2. Verifica Git Diff sui File del Benchmark
```diff
diff --git a/run_benchmark.sh b/run_benchmark.sh
index 5f8570c..f228990 100755
--- a/run_benchmark.sh
+++ b/run_benchmark.sh
@@ -554,6 +554,7 @@ python ai/experiment.py \
     --family "$FAMILY" \
     --quantization "$QUANTIZATION" \
     --parameter-size "$PARAMETER_SIZE" \
+    --context-length "$CONTEXT_LENGTH" \
     --base-url "$BASE_URL" \
     --timeout "$TIMEOUT" \
     --max-tokens "$MAX_TOKENS" \
@@ -615,4 +616,4 @@ else
 fi
 
 echo
-echo "============================================================"
\ No newline at end of file
+echo "============================================================"
```

## 3. Verifica del Runner `run_benchmark.sh`
- Permessi Git Mode: `100755` (confermato da `git ls-files -s run_benchmark.sh`)
- Terminatori di riga: `LF` (confermato da assenza di CRLF b'\r\n')
- Sintassi Bash: Verificata con `bash -n run_benchmark.sh` (Exit code: 0)
- Parametri congelati:
  - `CONTEXT_LENGTH = 8192`
  - `TIMEOUT = 240`
  - `MAX_TOKENS = 512`
  - `QUANTIZATION = Q4_K_M`
  - Modelli previsti (uno alla volta):
    1. `qwen2.5-7b-instruct` (3 run)
    2. `meta-llama-3.1-8b-instruct` (3 run)
    3. `deepseek-r1-distill-qwen-7b` (3 run)

Stato di Verifica: STATICALLY_VERIFIED
