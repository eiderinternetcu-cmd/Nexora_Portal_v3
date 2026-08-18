from PIL import Image

# Crear fondo oscuro de 1024x500
bg_color = (10, 26, 58) # #0a1a3a azul oscuro
banner = Image.new('RGB', (1024, 500), bg_color)

# Cargar el icono
icon_path = 'web_player/public/icon-512.png'
icon = Image.open(icon_path).convert("RGBA")

# Escalar el icono un poco más pequeño para que no quede aplastado en los bordes
target_size = 300
icon = icon.resize((target_size, target_size), Image.Resampling.LANCZOS)

# Calcular posición central
x = (1024 - target_size) // 2
y = (500 - target_size) // 2

# Pegar icono en el banner usando la máscara alfa para transparencias
banner.paste(icon, (x, y), icon)

# Guardar
out_path = '/home/brgo-solventyc/Escritorio/nexora_img/Google_Play_Store/00_banner_1024x500.png'
banner.save(out_path, format="PNG")
print("Banner creado en: " + out_path)
