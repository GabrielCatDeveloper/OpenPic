#!/usr/bin/env bash
# Arranca llama-server con Food-R1.
#
#   scripts/run_llama_server.sh                 # usa models/Food-R1-BF16.gguf
#   scripts/run_llama_server.sh q8_0            # otra cuantizacion
#   MODELS_DIR=/mnt/datos/models scripts/run_llama_server.sh q8_0
#
# Ajustes finos por variable de entorno:
#   PORT=8080 CTX=4096 NGL=99 CACHE_TYPE=q8_0 THREADS=$(nproc)

set -euo pipefail

QUANT="${1:-bf16}"
# El primer argumento es la cuantizacion: no debe llegar a llama-server.
if [ $# -gt 0 ]; then shift; fi
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODELS_DIR="${MODELS_DIR:-$ROOT/models}"
LLAMA_DIR="${LLAMA_DIR:-$HOME/llama.cpp}"
PORT="${PORT:-8080}"
HOST="${HOST:-127.0.0.1}"
CTX="${CTX:-4096}"
THREADS="${THREADS:-$(nproc)}"
NGL="${NGL:-99}"
CACHE_TYPE="${CACHE_TYPE:-q8_0}"

case "$QUANT" in
    bf16)   MODEL_FILE="Food-R1-BF16.gguf" ;;
    q8_0)   MODEL_FILE="Food-R1-Q8_0.gguf" ;;
    q6_k)   MODEL_FILE="Food-R1-Q6_K.gguf" ;;
    q5_k_m) MODEL_FILE="Food-R1-Q5_K_M.gguf" ;;
    q4_k_m) MODEL_FILE="Food-R1-Q4_K_M.gguf" ;;
    *) echo "Cuantizacion desconocida: $QUANT" >&2; exit 1 ;;
esac

MODEL="$MODELS_DIR/$MODEL_FILE"
MMPROJ="$MODELS_DIR/mmproj-Food-R1-F16.gguf"
SERVER="$LLAMA_DIR/build/bin/llama-server"

[ -f "$MODEL" ]   || { echo "No existe $MODEL. Descargalo con scripts/download_model.py" >&2; exit 1; }
[ -f "$MMPROJ" ]  || { echo "No existe $MMPROJ (proyector de vision)." >&2; exit 1; }
[ -x "$SERVER" ]  || { echo "No existe $SERVER. Compilalo con scripts/setup_llama.sh" >&2; exit 1; }

echo "Modelo   : $MODEL"
echo "Proyector: $MMPROJ"
echo "Endpoint : http://$HOST:$PORT/v1"
echo "Contexto : $CTX | capas GPU: $NGL | cache: $CACHE_TYPE"
echo

# Notas de tunelado para una iGPU con VRAM dedicada pequeña (Radeon 760M: 4.9 GB):
#
#  - Se deja el mmap por defecto (NO --no-mmap): con mmap las paginas del
#    modelo son "file-backed" y el kernel puede expulsarlas a disco (NVMe,
#    rapido) en vez de obligar a swap. Con --no-mmap el modelo vive en
#    memoria anonima y swap thrashearia.
#  - -ngl 99 descarga a la GPU todas las capas que quepan en la VRAM; el
#    resto se queda en RAM y se ejecuta en CPU.
#  - La cache KV en q8_0 ahorra memoria de contexto, que aqui es escasa.
#  - --image-max-tokens 1024 recorta la resolucion de la foto: en Qwen3-VL cada
#    token cubre 32x32 px, asi que 1024 tokens = 1.048.576 px, exactamente el
#    max_pixels=1048576 que usa el script de evaluacion oficial. Sin esto, una
#    foto de 4000x3000 de Telegram se comeria 15.000 tokens de contexto.
exec "$SERVER" \
    --model "$MODEL" \
    --mmproj "$MMPROJ" \
    --host "$HOST" \
    --port "$PORT" \
    --ctx-size "$CTX" \
    --threads "$THREADS" \
    --alias food-r1 \
    --parallel 1 \
    --flash-attn on \
    --image-max-tokens 1024 \
    --cache-type-k "$CACHE_TYPE" \
    --cache-type-v "$CACHE_TYPE" \
    -ngl "$NGL" \
    "$@"
