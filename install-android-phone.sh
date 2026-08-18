#!/usr/bin/env bash
# ==============================================================================
#  Nexora Portal v3 — Compilar e Instalar APK en Teléfono por USB (ADB)
# ==============================================================================
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
WEB_DIR="$DIR/web_player"

export ANDROID_HOME="$HOME/Android/Sdk"
export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/cmdline-tools/latest/bin:$PATH"

if [ -d "/snap/android-studio/current/jbr" ]; then
    export JAVA_HOME="/snap/android-studio/current/jbr"
    export PATH="$JAVA_HOME/bin:$PATH"
fi

echo "================================================================="
echo "   📲 INSTALADOR DE NEXORA PLAY EN DISPOSITIVO ANDROID"
echo "================================================================="

# 1. Verificar dispositivo conectado
echo "▶️  Verificando teléfono conectado por USB (ADB)..."
DEVICE=$(adb devices | grep -w "device" | awk '{print $1}' | head -n 1)

if [ -z "$DEVICE" ]; then
    echo "❌ No se encontró ningún teléfono conectado o autorizado por USB."
    echo "   Por favor conecta tu teléfono por cable y acepta la alerta de 'Depuración por USB'."
    echo ""
    adb devices
    exit 1
fi

echo "   ✓ Teléfono detectado: $DEVICE"

# 2. Configurar IP del Backend local
LOCAL_IP=$(ip route get 1.1.1.1 2>/dev/null | awk '{print $7}' | head -n 1)
if [ -z "$LOCAL_IP" ]; then
    LOCAL_IP="192.168.18.253"
fi
echo "   ✓ Conectando APK a Backend en: http://$LOCAL_IP:8000"

# 3. Compilar bundle web
echo ""
echo "▶️  [1/3] Compilando frontend Web Player..."
cd "$WEB_DIR"
VITE_NEXORA_API_BASE_URL="http://$LOCAL_IP:8000" npm run build

# 4. Sincronizar con Capacitor Android
echo ""
echo "▶️  [2/3] Sincronizando con proyecto nativo Android..."
npx cap sync android

# 5. Compilar e Instalar APK en el teléfono
echo ""
echo "▶️  [3/3] Compilando APK e instalando en tu teléfono ($DEVICE)..."
cd "$WEB_DIR/android"
./gradlew installDebug

# 6. Lanzar la aplicación en el teléfono
echo ""
echo "▶️  Lanzando Nexora Play en el teléfono..."
adb -s "$DEVICE" shell am start -n com.nexora.play/com.nexora.play.MainActivity

echo ""
echo "================================================================="
echo "  ✅ ¡NEXORA PLAY INSTALADA Y ABIERTA EN TU TELÉFONO!"
echo "================================================================="
