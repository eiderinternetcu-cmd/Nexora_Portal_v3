#!/usr/bin/env bash
# ==============================================================================
#  Nexora Portal v3 — Generador de Paquete iOS para Apple App Store / Xcode
# ==============================================================================
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
WEB_DIR="$DIR/web_player"
OUT_DIR="$DIR/release_builds/ios"

mkdir -p "$OUT_DIR"

echo "================================================================="
echo "   🍎 EMPAQUETANDO PROYECTO IOS PARA APPLE APP STORE (XCODE)"
echo "================================================================="

# 1. Compilar web en modo producción
echo "▶️  [1/3] Compilando frontend web de producción..."
cd "$WEB_DIR"
npm run build

# 2. Sincronizar con Capacitor iOS
echo ""
echo "▶️  [2/3] Sincronizando assets con proyecto nativo Xcode (iOS)..."
npx cap sync ios

# 3. Empaquetar proyecto Xcode en ZIP para transferir a Mac o subir a CI/CD
echo ""
echo "▶️  [3/3] Comprimiendo proyecto Xcode (.xcworkspace)..."
cd "$WEB_DIR/ios"
zip -r "$OUT_DIR/NexoraPlay-iOS-Xcode.zip" App -x "*.DS_Store"

echo ""
echo "================================================================="
echo "  ✅ ¡PROYECTO IOS PREPARADO CON ÉXITO!"
echo "  📁 Ubicación ZIP: $OUT_DIR/NexoraPlay-iOS-Xcode.zip"
echo "  📁 Carpeta Xcode: $WEB_DIR/ios/App/App.xcworkspace"
echo "================================================================="
