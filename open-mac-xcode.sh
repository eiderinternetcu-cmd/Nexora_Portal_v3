#!/usr/bin/env bash
# ==============================================================================
#  Nexora Portal v3 — Script para Mac: Compilar, Sincronizar y Abrir Xcode
# ==============================================================================
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
WEB_DIR="$DIR/web_player"

echo "================================================================="
echo "   🍎 PREPARANDO Y ABRIENDO PROYECTO IOS EN XCODE (MAC)"
echo "================================================================="

cd "$WEB_DIR"

# 1. Verificar dependencias
if [ ! -d "node_modules" ]; then
    echo "📦 Instalando dependencias de Node.js..."
    npm install
fi

# 2. Compilar frontend de producción
echo "▶️  [1/3] Compilando frontend web de producción..."
npm run build

# 3. Sincronizar con Capacitor iOS
echo ""
echo "▶️  [2/3] Sincronizando con proyecto nativo iOS..."
npx cap sync ios

# 4. Abrir Xcode automáticamente en Mac
echo ""
echo "▶️  [3/3] Abriendo Xcode..."
if command -v open &> /dev/null; then
    open ios/App/App.xcworkspace
    echo ""
    echo "================================================================="
    echo "  ✅ ¡XCODE ABIERTO CON ÉXITO EN TU MAC!"
    echo "  👉 Ve a: Product ➔ Archive ➔ Distribute App ➔ App Store Connect"
    echo "================================================================="
else
    npx cap open ios
fi
