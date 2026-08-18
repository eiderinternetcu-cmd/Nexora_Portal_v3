#!/bin/bash
set -e

echo "================================================="
echo "🍏 PREPARANDO NEXORA PLAY PARA iOS (MAC)"
echo "================================================="

# Validar que estamos en la carpeta correcta
if [ ! -d "web_player" ]; then
    echo "❌ Error: Debes ejecutar este script desde la carpeta Nexora_Portal_v3"
    exit 1
fi

cd web_player

echo "1. Instalando dependencias web..."
npm install

echo "2. Compilando el reproductor web..."
npm run build

echo "3. Sincronizando con el proyecto iOS nativo..."
npx cap sync ios

echo "================================================="
echo "✅ ¡LISTO! Abriendo el proyecto en Xcode..."
echo "================================================="
echo "👉 Cuando se abra Xcode:"
echo "   1. Selecciona el simulador (ej. iPhone 15 Pro) en la barra superior."
echo "   2. Presiona el botón de 'Play' (▶️) o presiona CMD + R para lanzar la app."
echo "================================================="

npx cap open ios
