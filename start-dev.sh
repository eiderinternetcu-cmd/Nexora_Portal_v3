#!/usr/bin/env bash
# ==============================================================================
#  Nexora Portal v3 — Script de Inicio Local (Web Player + API)
# ==============================================================================
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "================================================================="
echo "   🚀 INICIANDO NEXORA PLAY (DEV SERVER)"
echo "================================================================="

# 1. Asegurar dependencias de Frontend (web_player)
if [ ! -d "$DIR/web_player/node_modules" ]; then
    echo "📦 Instalando dependencias de Node.js en web_player..."
    (cd "$DIR/web_player" && npm install)
    echo "   ✓ Dependencias instaladas."
fi

# 2. Configurar entorno .env del Web Player apuntando a producción
if [ ! -f "$DIR/web_player/.env" ]; then
    echo "VITE_NEXORA_API_BASE_URL=https://nexoraplay.net" > "$DIR/web_player/.env"
fi

# 3. Manejo de terminación limpia (Ctrl+C)
cleanup() {
    echo ""
    echo "🛑 Deteniendo servicios..."
    if [ -n "$FRONTEND_PID" ]; then
        kill "$FRONTEND_PID" 2>/dev/null || true
    fi
    if [ -n "$BACKEND_PID" ]; then
        kill "$BACKEND_PID" 2>/dev/null || true
    fi
    exit 0
}
trap cleanup SIGINT SIGTERM EXIT

# 4. Verificar si existe Redis local para decidir si levantar backend local
LOCAL_REDIS=0
if nc -z 127.0.0.1 6379 2>/dev/null; then
    LOCAL_REDIS=1
fi

if [ "$LOCAL_REDIS" -eq 1 ]; then
    VENV_DIR="$DIR/venv"
    PYTHON_EXEC="$VENV_DIR/bin/python"
    if [ ! -f "$PYTHON_EXEC" ]; then PYTHON_EXEC="python3"; fi

    echo "▶️  Redis local detectado: Iniciando Backend FastAPI en http://localhost:8000..."
    "$PYTHON_EXEC" "$DIR/scripts/dev_server.py" --host 0.0.0.0 --port 8000 &
    BACKEND_PID=$!
else
    echo "☁️  Conectando Web Player al Backend en la nube (https://nexoraplay.net)..."
fi

# 5. Iniciar Web Player (Vite en puerto 5173)
echo "▶️  Iniciando Web Player en http://localhost:5173..."
(cd "$DIR/web_player" && npm run dev) &
FRONTEND_PID=$!

echo ""
echo "================================================================="
echo "  ✅ SERVIDOR LISTO"
echo "  🌐 Web Player:  http://localhost:5173"
echo "  📱 Red Local:   http://192.168.18.253:5173"
echo "  ☁️  Backend API: https://nexoraplay.net"
echo "================================================================="
echo "  Presiona [Ctrl + C] para detener el servidor."
echo "================================================================="

if [ -n "$BACKEND_PID" ]; then
    wait $BACKEND_PID $FRONTEND_PID
else
    wait $FRONTEND_PID
fi
