#!/usr/bin/env bash

set -euo pipefail

# ============================================================
# ChatAnalysis - Final LM Studio Benchmark Runner
#
# Usage:
#   ./run_benchmark.sh run1 qwen
#   ./run_benchmark.sh run2 qwen
#   ./run_benchmark.sh run3 qwen
#
#   ./run_benchmark.sh run1 llama
#   ./run_benchmark.sh run2 llama
#   ./run_benchmark.sh run3 llama
#
#   ./run_benchmark.sh run1 deepseek
#   ./run_benchmark.sh run2 deepseek
#   ./run_benchmark.sh run3 deepseek
#
# IMPORTANT:
# - One model loaded at a time
# - Same Git commit for every final run
# - Same context / timeout / max_tokens
# - No changes to ai/experiment.py
# ============================================================


# ------------------------------------------------------------
# 1. Arguments
# ------------------------------------------------------------

if [ "$#" -ne 2 ]; then
    echo "Uso:"
    echo "  $0 <run> <modello>"
    echo
    echo "Esempi:"
    echo "  $0 run1 qwen"
    echo "  $0 run2 llama"
    echo "  $0 run3 deepseek"
    exit 1
fi

RUN_NO="$1"
MODEL_KEY="$2"


# ------------------------------------------------------------
# 2. Fixed benchmark configuration
# ------------------------------------------------------------

BASE_URL="http://127.0.0.1:1234"

CONTEXT_LENGTH=8192
TIMEOUT=240
MAX_TOKENS=512

QUANTIZATION="Q4_K_M"

BASE_RESULTS="$HOME/Desktop/LUCA/benchmark_results"


# ------------------------------------------------------------
# 3. Model mapping
# ------------------------------------------------------------

case "$MODEL_KEY" in

    qwen)
        MODEL_ID="qwen2.5-7b-instruct"
        MODEL_QUERY="qwen2.5-7b-instruct"
        FAMILY="QWEN"
        PARAMETER_SIZE="7B"
        ;;

    llama)
        MODEL_ID="meta-llama-3.1-8b-instruct"
        MODEL_QUERY="llama-3.1-8b-instruct"
        FAMILY="LLAMA"
        PARAMETER_SIZE="8B"
        ;;

    deepseek)
        MODEL_ID="deepseek-r1-distill-qwen-7b"
        MODEL_QUERY="deepseek-r1-distill-qwen-7b"
        FAMILY="DEEPSEEK"
        PARAMETER_SIZE="7B"
        ;;

    *)
        echo "ERRORE: modello non riconosciuto: $MODEL_KEY"
        echo
        echo "Modelli ammessi:"
        echo "  qwen"
        echo "  llama"
        echo "  deepseek"
        exit 1
        ;;
esac


# ------------------------------------------------------------
# 4. Move to repository root
# ------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo
echo "============================================================"
echo " CHATANALYSIS - FINAL BENCHMARK"
echo "============================================================"
echo
echo "Repository:"
pwd
echo


# ------------------------------------------------------------
# 5. Git repository check
# ------------------------------------------------------------

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "ERRORE: questa cartella non è un repository Git."
    exit 1
fi

GIT_COMMIT="$(git rev-parse HEAD)"
GIT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"

echo "Git commit : $GIT_COMMIT"
echo "Git branch : $GIT_BRANCH"
echo


# ------------------------------------------------------------
# 6. Working tree MUST be clean
# ------------------------------------------------------------

if [ -n "$(git status --porcelain)" ]; then

    echo "============================================================"
    echo "ERRORE: WORKING TREE NON PULITO"
    echo "============================================================"
    echo
    echo "Il benchmark finale non può partire con modifiche non committate."
    echo

    git status --short

    echo
    exit 1
fi

echo "Working tree: CLEAN"
echo


# ------------------------------------------------------------
# 7. Python virtual environment
# ------------------------------------------------------------

if [ ! -f ".venv/bin/activate" ]; then
    echo "ERRORE: ambiente virtuale .venv non trovato."
    exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "Python:"
which python
python --version
echo


# ------------------------------------------------------------
# 8. Check LM Studio CLI
# ------------------------------------------------------------

if ! command -v lms >/dev/null 2>&1; then
    echo "ERRORE: comando 'lms' non disponibile."
    echo "Verificare installazione/configurazione LM Studio CLI."
    exit 1
fi


# ------------------------------------------------------------
# 9. Prepare unique output directory
# ------------------------------------------------------------

DATE_TAG="$(date '+%Y%m%d_%H%M%S')"

MODEL_DIR="${BASE_RESULTS}/${MODEL_KEY}_${RUN_NO}_${DATE_TAG}"

mkdir -p "$MODEL_DIR"

ENV_FILE="${MODEL_DIR}/hardware_and_environment.txt"
CONSOLE_LOG="${MODEL_DIR}/run_console.log"

# From this point save console output as well
exec > >(tee -a "$CONSOLE_LOG") 2>&1

echo
echo "Output directory:"
echo "$MODEL_DIR"
echo


# ------------------------------------------------------------
# 10. Start / verify LM Studio
# ------------------------------------------------------------

echo "============================================================"
echo " LM STUDIO SERVER"
echo "============================================================"
echo

# Start daemon if necessary.
# If already active, failure/non-action is harmless.
lms daemon start >/dev/null 2>&1 || true

if ! lms server status >/dev/null 2>&1; then

    echo "LM Studio server non attivo."
    echo "Avvio server sulla porta 1234..."

    lms server start --port 1234

    sleep 2
fi

echo
lms server status
echo


# ------------------------------------------------------------
# 11. Verify local API
# ------------------------------------------------------------

python - <<'PY'
import json
import urllib.request

url = "http://127.0.0.1:1234/v1/models"

try:
    with urllib.request.urlopen(url, timeout=5) as response:
        data = json.loads(response.read().decode("utf-8"))
except Exception as exc:
    raise SystemExit(
        f"ERRORE: LM Studio non risponde correttamente su {url}: {exc}"
    )

print("LM Studio API raggiungibile.")
print("Modelli attualmente esposti:", [
    item.get("id") for item in data.get("data", [])
])
PY

echo


# ------------------------------------------------------------
# 12. Remove any currently loaded model
# ------------------------------------------------------------

echo "============================================================"
echo " UNLOAD MODELLI"
echo "============================================================"
echo

lms unload --all || true

sleep 2

echo
echo "Stato dopo unload:"
lms ps || true
echo


# ------------------------------------------------------------
# 13. Estimate model loading
# ------------------------------------------------------------

echo "============================================================"
echo " MODEL LOAD ESTIMATE"
echo "============================================================"
echo

echo "Model query : $MODEL_QUERY"
echo "Identifier  : $MODEL_ID"
echo "Context     : $CONTEXT_LENGTH"
echo "GPU         : max"
echo

lms load \
    --estimate-only \
    "$MODEL_QUERY" \
    --context-length "$CONTEXT_LENGTH" \
    --gpu max

echo


# ------------------------------------------------------------
# 14. Load exact benchmark model
# ------------------------------------------------------------

echo "============================================================"
echo " LOAD MODEL"
echo "============================================================"
echo

lms load \
    "$MODEL_QUERY" \
    --identifier "$MODEL_ID" \
    --context-length "$CONTEXT_LENGTH" \
    --gpu max

echo
echo "Modello caricato."
echo

lms ps

echo


# ------------------------------------------------------------
# 15. Verify exact model identifier
# ------------------------------------------------------------

EXPECTED_MODEL="$MODEL_ID" python - <<'PY'
import os
import sys

from ai.lmstudio import LmStudioClient

expected = os.environ["EXPECTED_MODEL"]

client = LmStudioClient(
    base_url="http://127.0.0.1:1234"
)

models = client.list_models()

print("Modelli LM Studio esposti:", models)

if expected not in models:
    print()
    print("ERRORE:")
    print(f"Il modello atteso '{expected}' non è esposto da LM Studio.")
    print("Benchmark annullato.")
    sys.exit(2)

print()
print(f"Model ID verificato: {expected}")
PY

echo


# ------------------------------------------------------------
# 16. Record hardware and software environment
# ------------------------------------------------------------

{
    echo "============================================================"
    echo "CHATANALYSIS FINAL BENCHMARK ENVIRONMENT"
    echo "============================================================"
    echo

    echo "DATE"
    date --iso-8601=seconds
    echo

    echo "RUN"
    echo "$RUN_NO"
    echo

    echo "MODEL KEY"
    echo "$MODEL_KEY"
    echo

    echo "MODEL ID"
    echo "$MODEL_ID"
    echo

    echo "MODEL QUERY"
    echo "$MODEL_QUERY"
    echo

    echo "FAMILY"
    echo "$FAMILY"
    echo

    echo "PARAMETER SIZE"
    echo "$PARAMETER_SIZE"
    echo

    echo "QUANTIZATION"
    echo "$QUANTIZATION"
    echo

    echo "RUNTIME CONTEXT LENGTH"
    echo "$CONTEXT_LENGTH"
    echo

    echo "TIMEOUT"
    echo "$TIMEOUT"
    echo

    echo "MAX TOKENS"
    echo "$MAX_TOKENS"
    echo

    echo "TEMPERATURE"
    echo "0"
    echo

    echo "BASE URL"
    echo "$BASE_URL"
    echo

    echo "------------------------------------------------------------"
    echo "GIT"
    echo "------------------------------------------------------------"

    echo "Commit:"
    git rev-parse HEAD

    echo
    echo "Branch:"
    git rev-parse --abbrev-ref HEAD

    echo
    echo "Status:"
    git status --short

    echo

    echo "------------------------------------------------------------"
    echo "OPERATING SYSTEM"
    echo "------------------------------------------------------------"

    uname -a

    if command -v lsb_release >/dev/null 2>&1; then
        echo
        lsb_release -a 2>/dev/null || true
    fi

    echo

    echo "------------------------------------------------------------"
    echo "CPU"
    echo "------------------------------------------------------------"

    lscpu || true

    echo

    echo "------------------------------------------------------------"
    echo "RAM"
    echo "------------------------------------------------------------"

    free -h || true

    echo

    echo "------------------------------------------------------------"
    echo "GPU"
    echo "------------------------------------------------------------"

    if command -v nvidia-smi >/dev/null 2>&1; then
        nvidia-smi || true
    else
        echo "nvidia-smi non disponibile"
    fi

    echo

    echo "------------------------------------------------------------"
    echo "PYTHON"
    echo "------------------------------------------------------------"

    which python
    python --version
    python -m pip --version

    echo

    echo "------------------------------------------------------------"
    echo "LM STUDIO CLI"
    echo "------------------------------------------------------------"

    lms --version || true

    echo

    echo "------------------------------------------------------------"
    echo "LM STUDIO SERVER"
    echo "------------------------------------------------------------"

    lms server status || true

    echo

    echo "------------------------------------------------------------"
    echo "LM STUDIO LOADED MODEL"
    echo "------------------------------------------------------------"

    lms ps || true

    echo

    echo "============================================================"

} > "$ENV_FILE"


echo "Environment salvato in:"
echo "$ENV_FILE"
echo


# ------------------------------------------------------------
# 17. Run the official benchmark
# ------------------------------------------------------------

echo "============================================================"
echo " START BENCHMARK"
echo "============================================================"
echo

echo "Run           : $RUN_NO"
echo "Model         : $MODEL_ID"
echo "Family        : $FAMILY"
echo "Parameters    : $PARAMETER_SIZE"
echo "Quantization  : $QUANTIZATION"
echo "Context       : $CONTEXT_LENGTH"
echo "Timeout       : $TIMEOUT"
echo "Max tokens    : $MAX_TOKENS"
echo "Output        : $MODEL_DIR"
echo

BENCHMARK_START="$(date +%s)"

python ai/experiment.py \
    --model-id "$MODEL_ID" \
    --family "$FAMILY" \
    --quantization "$QUANTIZATION" \
    --parameter-size "$PARAMETER_SIZE" \
    --base-url "$BASE_URL" \
    --timeout "$TIMEOUT" \
    --max-tokens "$MAX_TOKENS" \
    --output-dir "$MODEL_DIR" \
    --force-run

BENCHMARK_END="$(date +%s)"

TOTAL_SECONDS=$((BENCHMARK_END - BENCHMARK_START))


# ------------------------------------------------------------
# 18. Final checks
# ------------------------------------------------------------

echo
echo "============================================================"
echo " BENCHMARK COMPLETATO"
echo "============================================================"
echo

echo "Modello:"
echo "  $MODEL_ID"

echo
echo "Run:"
echo "  $RUN_NO"

echo
echo "Tempo totale runner:"
echo "  ${TOTAL_SECONDS} secondi"

echo
echo "Git commit:"
echo "  $GIT_COMMIT"

echo
echo "Output:"
echo "  $MODEL_DIR"

echo
echo "File prodotti:"
find "$MODEL_DIR" -maxdepth 1 -type f -printf "  %f\n" | sort

echo
echo "Git status finale:"

if [ -n "$(git status --porcelain)" ]; then

    echo
    echo "ATTENZIONE:"
    echo "Il working tree è cambiato durante il benchmark."
    git status --short

else

    echo "CLEAN"

fi

echo
echo "============================================================"