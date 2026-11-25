#!/usr/bin/env bash
# Espera a que el motor de inferencia (llama-server) este listo.
#
#   scripts/wait_for_engine.sh            # espera hasta 600 s
#   WAIT_ENGINE_TIMEOUT=120 scripts/wait_for_engine.sh
#
# Se usa como ExecStartPre del servicio del bot: al arrancar el PC el modelo
# tarda en cargarse (10 GB + proyector de vision), y sin esta espera el bot
# atenderia fotos mientras el motor sigue cargando.
#
# Sale con 0 en cuanto el endpoint /models responde, o con 1 al agotar el
# tiempo de espera (el servicio se reiniciara y volvera a intentarlo).

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"
TIMEOUT="${WAIT_ENGINE_TIMEOUT:-600}"
INTERVAL="${WAIT_ENGINE_INTERVAL:-3}"

# La URL puede venir del entorno o del .env del proyecto.
BASE_URL="${LLM_BASE_URL:-}"
if [ -z "$BASE_URL" ] && [ -f "$ENV_FILE" ]; then
    BASE_URL="$(grep -E '^LLM_BASE_URL=' "$ENV_FILE" | tail -n 1 | cut -d= -f2- | tr -d "\"' \r")"
fi
BASE_URL="${BASE_URL:-http://127.0.0.1:8080/v1}"
URL="${BASE_URL%/}/models"

echo "Esperando al motor de inferencia en $URL (max ${TIMEOUT}s)..."

deadline=$(( $(date +%s) + TIMEOUT ))
while [ "$(date +%s)" -lt "$deadline" ]; do
    if curl -fsS -m 5 "$URL" >/dev/null 2>&1; then
        echo "Motor listo: $URL"
        exit 0
    fi
    sleep "$INTERVAL"
done

echo "El motor no respondio en ${TIMEOUT}s: $URL" >&2
exit 1
