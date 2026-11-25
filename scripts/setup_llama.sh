#!/usr/bin/env bash
# Compila llama.cpp con el backend que corresponda a tu GPU.
#
#   scripts/setup_llama.sh vulkan   -> AMD/Intel (Radeon 760M en tu caso)
#   scripts/setup_llama.sh cuda     -> NVIDIA
#   scripts/setup_llama.sh cpu      -> solo CPU (muy lento para un VLM)
#
# El backend Vulkan necesita las cabeceras SPIRV-Headers. Si no estan
# instaladas en el sistema (y no hay sudo), el script las extrae del paquete
# de Debian en un prefijo local, sin tocar el sistema.

set -euo pipefail

BACKEND="${1:-vulkan}"
DIR="${LLAMA_DIR:-$HOME/llama.cpp}"
DEPS="$DIR/deps"

echo "==> Backend: $BACKEND"
echo "==> Directorio: $DIR"

if [ ! -d "$DIR" ]; then
    git clone --depth 1 https://github.com/ggml-org/llama.cpp "$DIR"
fi

cd "$DIR"
git pull --ff-only

CMAKE_ARGS=(
    -B build
    -DCMAKE_BUILD_TYPE=Release
    -DLLAMA_CURL=OFF
    -DGGML_NATIVE=ON
)

# --- SPIRV-Headers (solo necesario para Vulkan) ----------------------------
if [ "$BACKEND" = "vulkan" ]; then
    CMAKE_ARGS+=(-DGGML_VULKAN=ON)

    if ! ls /usr/share/cmake/SPIRV-Headers >/dev/null 2>&1; then
        SPIRV_PREFIX="$DEPS/spirv-headers"
        if [ ! -d "$SPIRV_PREFIX/usr/share/cmake/SPIRV-Headers" ]; then
            echo "==> SPIRV-Headers no esta instalado; se extrae en local (sin sudo)"
            mkdir -p "$DEPS" "$SPIRV_PREFIX"
            TMP_DEB="$(mktemp -d)"
            ( cd "$TMP_DEB" && apt-get download spirv-headers >/dev/null 2>&1 ) || {
                echo "No se pudo descargar el paquete spirv-headers." >&2
                echo "Alternativa: clona https://github.com/KhronosGroup/SPIRV-Headers" >&2
                echo "en $SPIRV_PREFIX/usr y pon sus cabeceras en $SPIRV_PREFIX/usr/include." >&2
                exit 1
            }
            dpkg-deb -x "$TMP_DEB"/spirv-headers_*.deb "$SPIRV_PREFIX"
            rm -rf "$TMP_DEB"
        fi
        CMAKE_ARGS+=(-DCMAKE_PREFIX_PATH="$SPIRV_PREFIX/usr")
    fi
fi

case "$BACKEND" in
    cuda)
        CMAKE_ARGS+=(-DGGML_CUDA=ON)
        ;;
    cpu)
        CMAKE_ARGS+=(-DGGML_VULKAN=OFF)
        ;;
    *)
        echo "Backend desconocido: $BACKEND" >&2
        exit 1
        ;;
esac

echo "==> Configurando"
cmake "${CMAKE_ARGS[@]}"

echo "==> Compilando (esto tarda unos minutos)"
cmake --build build --config Release -j"$(nproc)"

echo
echo "Compilado. Binarios en: $DIR/build/bin"
echo "Arranque:"
echo "  scripts/run_llama_server.sh"
