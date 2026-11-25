#!/usr/bin/env bash
# Instala los bots y el motor de inferencia como servicios, de forma que
# arranquen solos al iniciar el PC.
#
#   scripts/install_autostart.sh              # servicios de USUARIO (sin sudo)
#   scripts/install_autostart.sh --system     # servicios del SISTEMA (sudo)
#   scripts/install_autostart.sh --no-start   # instala y habilita, sin arrancar
#
# Los servicios de usuario van a ~/.config/systemd/user/ y no necesitan sudo.
# Con `loginctl enable-linger <usuario>` el gestor de servicios de usuario
# arranca al iniciar el equipo, sin que nadie tenga que iniciar sesion.
#
# Los servicios del sistema (con sudo) atienden a todos los usuarios del
# equipo y arrancan antes que el inicio de sesion.
#
# Gestiona:
#   systemctl --user status  foodbot llama-server
#   systemctl --user restart foodbot
#   journalctl --user -u foodbot -f

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY="$ROOT/deploy"
UNIT_DIR="${UNIT_DIR:-$HOME/.config/systemd/user}"
USER_NAME="${SUDO_USER:-$(id -un)}"
START=1
SYSTEM=0

for arg in "$@"; do
    case "$arg" in
        --no-start) START=0 ;;
        --system)   SYSTEM=1 ;;
        -h|--help) sed -n '2,25p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "Opcion desconocida: $arg" >&2; exit 1 ;;
    esac
done

command -v systemctl >/dev/null || { echo "Falta systemd" >&2; exit 1; }

# Sustituye los marcadores por las rutas reales de esta maquina.
render() {
    sed -e "s|@PROJECT@|$ROOT|g" -e "s|@USER@|$USER_NAME|g" "$1"
}

# El script de espera tiene que ser ejecutable.
chmod +x "$ROOT/scripts/wait_for_engine.sh" "$ROOT/scripts/run_llama_server.sh"

if [ "$SYSTEM" = "1" ]; then
    echo "Instalando servicios del sistema (necesita sudo)..."
    sudo install -d -m 755 /etc/systemd/system
    for unit in llama-server.service foodbot.service; do
        render "$DEPLOY/$unit" | sudo tee "/etc/systemd/system/$unit" >/dev/null
        sudo chmod 644 "/etc/systemd/system/$unit"
        echo "  Instalado: /etc/systemd/system/$unit"
    done
    sudo systemctl daemon-reload
    sudo systemctl enable llama-server.service foodbot.service
    if [ "$START" = "1" ]; then
        sudo systemctl restart llama-server.service
        sudo systemctl restart foodbot.service
    fi
    echo
    sudo systemctl --no-pager --lines=0 status foodbot.service llama-server.service || true
    exit 0
fi

echo "Instalando servicios de usuario en $UNIT_DIR..."
mkdir -p "$UNIT_DIR"
for unit in llama-server.user.service foodbot.user.service; do
    target="$UNIT_DIR/${unit%.user.service}.service"
    render "$DEPLOY/$unit" > "$target"
    chmod 644 "$target"
    echo "  Instalado: $target"
done

systemctl --user daemon-reload
systemctl --user enable llama-server.service foodbot.service

# Que arranque al iniciar el PC sin depender de que alguien entre en sesion.
if loginctl show-user "$USER_NAME" 2>/dev/null | grep -q "Linger=yes"; then
    echo "Linger ya activado: los servicios arrancan al iniciar el PC."
elif loginctl enable-linger "$USER_NAME" 2>/dev/null; then
    echo "Linger activado: los servicios arrancan al iniciar el PC."
else
    echo "AVISO: no se pudo activar el linger (hace falta sudo)."
    echo "       Los servicios arrancaran al iniciar sesion en el escritorio."
fi

if [ "$START" = "1" ]; then
    echo "Arrancando el motor de inferencia..."
    systemctl --user restart llama-server.service
    echo "Arrancando los bots..."
    systemctl --user restart foodbot.service
fi

echo
systemctl --user --no-pager --lines=0 status foodbot.service llama-server.service || true
