#!/usr/bin/env bash
# ==============================================================================
#  Nexora Portal v3 — Generador de Bundle para Google Play (.aab / .apk)
# ==============================================================================
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
WEB_DIR="$DIR/web_player"
OUT_DIR="$DIR/release_builds/android"

export ANDROID_HOME="$HOME/Android/Sdk"
export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/cmdline-tools/latest/bin:$PATH"

if [ -d "/snap/android-studio/current/jbr" ]; then
    export JAVA_HOME="/snap/android-studio/current/jbr"
    export PATH="$JAVA_HOME/bin:$PATH"
fi

mkdir -p "$OUT_DIR"

echo "================================================================="
echo "   📦 COMPILANDO BUNDLE PARA GOOGLE PLAY STORE (.AAB)"
echo "================================================================="

echo "▶️  [1/4] Compilando frontend web de producción con API https://nexoraplay.net..."
cd "$WEB_DIR"
VITE_NEXORA_API_BASE_URL="https://nexoraplay.net" npm run build

# 2. Sincronizar con Capacitor Android
echo ""
echo "▶️  [2/4] Sincronizando assets con proyecto nativo Android..."
npx cap sync android

# 3. Compilar Android App Bundle (.aab) y APK Release
echo ""
echo "▶️  [3/4] Compilando con Gradle (bundleRelease + assembleRelease)..."
cd "$WEB_DIR/android"
./gradlew bundleRelease assembleRelease

# 4. Copiar archivos finales
echo ""
echo "▶️  [4/4] Copiando bundles a la carpeta de release..."
AAB_PATH=$(find app/build/outputs/bundle/release -name "*.aab" | head -n 1)
APK_PATH=$(find app/build/outputs/apk/release -name "*.apk" | head -n 1)

if [ -n "$AAB_PATH" ]; then
    cp "$AAB_PATH" "$OUT_DIR/NexoraPlay-release.aab"
    echo "   ✓ Creado: $OUT_DIR/NexoraPlay-release.aab (Para Google Play Store)"
fi

if [ -n "$APK_PATH" ]; then
    cp "$APK_PATH" "$OUT_DIR/NexoraPlay-release.apk"
    echo "   ✓ Creado: $OUT_DIR/NexoraPlay-release.apk (Para pruebas directas)"
fi

echo ""
echo "================================================================="
echo "  ✅ ¡BUNDLE DE ANDROID GENERADO CON ÉXITO!"
echo "  📁 Ubicación: $OUT_DIR/NexoraPlay-release.aab"
echo "================================================================="
