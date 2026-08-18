#!/usr/bin/env python3
"""
Generador automático de iconos para Android, iOS y Web usando el logo de Nexora.
"""
from pathlib import Path
from PIL import Image, ImageOps

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_ICON = BASE_DIR / "web_player" / "public" / "assets" / "icon.png"
ANDROID_RES = BASE_DIR / "web_player" / "android" / "app" / "src" / "main" / "res"
IOS_ICONSET = BASE_DIR / "web_player" / "ios" / "App" / "App" / "Assets.xcassets" / "AppIcon.appiconset"
PUBLIC_DIR = BASE_DIR / "web_player" / "public"

def create_app_icon(size: int, bg_color=(10, 14, 26, 255), padding_ratio=0.15) -> Image.Image:
    """Crea un icono cuadrado con fondo oscuro y la 'N' centrada."""
    src = Image.open(SRC_ICON).convert("RGBA")
    
    canvas = Image.new("RGBA", (size, size), bg_color)
    
    # Calcular tamaño de la 'N' con padding
    target_inner = int(size * (1 - 2 * padding_ratio))
    
    src.thumbnail((target_inner, target_inner), Image.Resampling.LANCZOS)
    
    # Centrar en el canvas
    offset_x = (size - src.width) // 2
    offset_y = (size - src.height) // 2
    
    canvas.paste(src, (offset_x, offset_y), mask=src)
    return canvas

def generate_android_icons():
    """Genera iconos mipmap para Android."""
    sizes = {
        "mipmap-mdpi": 48,
        "mipmap-hdpi": 72,
        "mipmap-xhdpi": 96,
        "mipmap-xxhdpi": 144,
        "mipmap-xxxhdpi": 192,
    }
    
    for folder, size in sizes.items():
        out_dir = ANDROID_RES / folder
        out_dir.mkdir(parents=True, exist_ok=True)
        
        # ic_launcher.png
        icon = create_app_icon(size, padding_ratio=0.12)
        icon.save(out_dir / "ic_launcher.png", "PNG")
        
        # ic_launcher_round.png
        icon.save(out_dir / "ic_launcher_round.png", "PNG")
        
        # ic_launcher_foreground.png (para iconos adaptativos Android 8+)
        fg_size = int(size * 1.5)
        fg_icon = create_app_icon(fg_size, bg_color=(0, 0, 0, 0), padding_ratio=0.25)
        fg_icon.save(out_dir / "ic_launcher_foreground.png", "PNG")
        
    print("✓ Iconos de Android generados en todos los tamaños.")

def generate_ios_icons():
    """Genera el icono universal de iOS (1024x1024 para App Store y Xcode)."""
    IOS_ICONSET.mkdir(parents=True, exist_ok=True)
    
    icon_1024 = create_app_icon(1024, bg_color=(10, 14, 26, 255), padding_ratio=0.15)
    
    # iOS no permite transparencia en el icono de App Store
    icon_1024_rgb = Image.new("RGB", (1024, 1024), (10, 14, 26))
    icon_1024_rgb.paste(icon_1024, mask=icon_1024.split()[3])
    
    icon_1024_rgb.save(IOS_ICONSET / "AppIcon-512@2x.png", "PNG")
    
    # Generar Contents.json para Xcode
    contents_json = """{
  "images" : [
    {
      "filename" : "AppIcon-512@2x.png",
      "idiom" : "universal",
      "platform" : "ios",
      "size" : "1024x1024"
    }
  ],
  "info" : {
    "author" : "xcode",
    "version" : 1
  }
}
"""
    (IOS_ICONSET / "Contents.json").write_text(contents_json, encoding="utf-8")
    print("✓ Icono de iOS (1024x1024) y Contents.json generados con éxito.")

def generate_web_favicons():
    """Genera favicon y logo para la web."""
    icon_512 = create_app_icon(512, padding_ratio=0.12)
    icon_512.save(PUBLIC_DIR / "icon-512.png", "PNG")
    
    icon_192 = create_app_icon(192, padding_ratio=0.12)
    icon_192.save(PUBLIC_DIR / "icon-192.png", "PNG")
    
    icon_32 = create_app_icon(32, padding_ratio=0.08)
    icon_32.save(PUBLIC_DIR / "favicon.ico", "ICO")
    print("✓ Favicons Web generados.")

if __name__ == "__main__":
    print("🎨 Generando iconos nativos con la 'N' de Nexora...")
    generate_android_icons()
    generate_ios_icons()
    generate_web_favicons()
    print("🚀 ¡Todos los iconos fueron creados con éxito!")
