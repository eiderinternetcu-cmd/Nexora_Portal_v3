#!/usr/bin/env bash
# ==============================================================================
#  Nexora Portal v3 — Script de Inicio Local Rápido (Backend + Frontend)
# ==============================================================================
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "================================================================="
echo "   🚀 INICIANDO NEXORA PORTAL v3 (LOCAL DEV)"
echo "================================================================="

# 1. Verificar archivo .env en Backend
if [ ! -f "$DIR/.env" ]; then
    echo "⚠️  No se encontró .env en la raíz. Copiando desde .env.example..."
    cp "$DIR/.env.example" "$DIR/.env"
    echo "   ✓ Creado .env base."
fi

# 2. Entorno virtual de Python
VENV_DIR="$DIR/venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 Creando entorno virtual de Python (venv)..."
    python3 -m venv "$VENV_DIR"
    echo "   Instalando dependencias en venv..."
    "$VENV_DIR/bin/pip" install --upgrade pip
    if [ -f "$DIR/requirements.txt" ]; then
        "$VENV_DIR/bin/pip" install -r "$DIR/requirements.txt"
    fi
    echo "   ✓ Entorno virtual listo."
fi

PYTHON_EXEC="$VENV_DIR/bin/python"
if [ ! -f "$PYTHON_EXEC" ]; then
    PYTHON_EXEC="python3"
fi

# 3. Verificar dependencias de Frontend (web_player)
if [ ! -d "$DIR/web_player/node_modules" ]; then
    echo "📦 Instalando dependencias de Node.js en web_player..."
    (cd "$DIR/web_player" && npm install)
    echo "   ✓ Dependencias de frontend instaladas."
fi

# 4. Manejo de terminación limpia (Ctrl+C)
cleanup() {
    echo ""
    echo "🛑 Deteniendo servicios de Nexora..."
    if [ -n "$BACKEND_PID" ]; then
        kill "$BACKEND_PID" 2>/dev/null || true
    fi
    if [ -n "$FRONTEND_PID" ]; then
        kill "$FRONTEND_PID" 2>/dev/null || true
    fi
    echo "   ✓ Servicios detenidos correctamente."
    exit 0
}
trap cleanup SIGINT SIGTERM EXIT

# 5. Iniciar Backend (FastAPI en puerto 8000)
echo ""
echo "▶️  Iniciando Backend FastAPI en http://localhost:8000..."
"$PYTHON_EXEC" "$DIR/scripts/dev_server.py" --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

# 6. Iniciar Frontend (Vite en puerto 5173)
echo "▶️  Iniciando Web Player (Vite) en http://localhost:5173..."
(cd "$DIR/web_player" && npm run dev) &
FRONTEND_PID=$!

echo ""
echo "================================================================="
echo "  ✅ SERVICIOS EN EJECUCIÓN"
echo "  🌐 Web Player:  http://localhost:5173"
echo "  📄 Swagger API:  http://localhost:8000/docs"
echo "  ⚡ Backend API:  http://localhost:8000"
echo "================================================================="
echo "  Presiona [Ctrl + C] para detener ambos servicios."
echo "================================================================="

# Esperar a que terminen los procesos
wait $BACKEND_PID $FRONTEND_PID
