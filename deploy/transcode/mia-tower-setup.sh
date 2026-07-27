#!/usr/bin/env bash
# Torre de transcodificacion (nodo tc-mia) — 66.163.125.89, Ubuntu 24.04.
# Idempotente: se puede repetir sin romper nada.
#
# Sin Docker a proposito: la maquina es dedicada y ffmpeg va nativo, que arranca
# antes y se supervisa con systemd.
#
# Lo que deja montado:
#   - nginx sirviendo /var/hls en el 8088, SOLO accesible desde el edge
#   - plantilla systemd por canal, con Restart=always
#   - un .env por canal en /etc/nexora-tc/
#
# Arrancar un canal:  systemctl enable --now nexora-tc@ESTRELLAS_CA
# Pararlo:            systemctl disable --now nexora-tc@ESTRELLAS_CA
#
# CAPACIDAD MEDIDA: cada canal 1080p->720p consume ~1 nucleo entero (no 0,67
# como se estimo por calibracion sintetica). Con 4 nucleos fisicos, la torre da
# para TRES canales, no cuatro.
set -euo pipefail

EDGE_IP="45.184.225.4"

echo "=== nginx ==="
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nginx ffmpeg >/dev/null
mkdir -p /var/hls
chown www-data:www-data /var/hls

cat > /etc/nginx/sites-available/hls <<'NGINX'
# Salida HLS de la torre. No es publica: el firewall solo deja entrar al edge,
# que es quien aplica la autorizacion firmada antes de servir nada al cliente.
server {
    listen 8088;
    server_name _;
    root /var/hls;
    autoindex off;

    location / {
        types {
            application/vnd.apple.mpegurl m3u8;
            video/mp2t                    ts;
        }
        default_type application/octet-stream;

        # La lista se reescribe cada pocos segundos: cachearla rompe el directo.
        location ~ \.m3u8$ {
            add_header Cache-Control "no-cache, no-store, must-revalidate" always;
        }
        location ~ \.ts$ {
            add_header Cache-Control "public, max-age=30" always;
        }
    }

    location = /health {
        return 200 "ok\n";
        default_type text/plain;
    }
}
NGINX

ln -sf /etc/nginx/sites-available/hls /etc/nginx/sites-enabled/hls
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl enable --now nginx && systemctl reload nginx
echo "  nginx sirviendo /var/hls en 8088"

echo "=== plantilla systemd ==="
# OJO con $VFILTER sin llaves: systemd expande ${VAR} como UN argumento y $VAR
# separandolo en palabras. El filtro son dos argumentos (-vf y su valor), asi
# que con llaves ffmpeg recibe "-vf scale=1280:720" pegado y falla.
cat > /etc/systemd/system/nexora-tc@.service <<'UNIT'
[Unit]
Description=Transcodificador Nexora — canal %i
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=www-data
EnvironmentFile=/etc/nexora-tc/%i.env
RuntimeDirectory=nexora-tc
ExecStartPre=/bin/mkdir -p /var/hls/%i
ExecStartPre=/bin/chown www-data:www-data /var/hls/%i
# GOP cerrado de 2 s: es lo que exige Media Source Extensions y la razon de que
# estos canales no reproduzcan en navegador tal como salen de la cabecera.
ExecStart=/usr/bin/ffmpeg -hide_banner -loglevel warning \
  -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 \
  -i ${SOURCE} \
  $VFILTER \
  -c:v libx264 -preset ultrafast -pix_fmt yuv420p \
  -b:v ${VBITRATE} -maxrate ${VMAXRATE} -bufsize ${VBUFSIZE} \
  -g 60 -keyint_min 60 -sc_threshold 0 \
  -c:a aac -b:a 128k -ac 2 \
  -f hls -hls_time 4 -hls_list_size 6 \
  -hls_flags delete_segments+append_list+omit_endlist \
  -hls_segment_filename /var/hls/%i/seg_%%05d.ts \
  /var/hls/%i/index.m3u8
Restart=always
RestartSec=5
Nice=5

[Install]
WantedBy=multi-user.target
UNIT

mkdir -p /etc/nexora-tc
systemctl daemon-reload

echo "=== .env por canal (los 16 de TelecoWR del catalogo) ==="
# TUDN queda fuera: su fuente no esta viva. GOLDEN_PLUS tampoco: corre en el edge.
for CH in GoldenPremier_2H ESTRELLAS_CA TLNOVELAS_CA DPELICULA_PLUS TELEHIT \
          TELEHIT_MUSICA TELEHIT_MUSICA_PLUS DistritoComedia BANDAMAX BITME \
          DPELICULA GOLDEN GOLDEN_EDGE GoldenPremier ADRENALINA UNIVISION; do
  cat > "/etc/nexora-tc/${CH}.env" <<EOF
# Canal ${CH} — TelecoWR, H.264 1920x1080 con GOP de ~11 s y un solo IDR.
SOURCE=http://181.78.246.211:8002/${CH}/mpegts
VFILTER=-vf scale=1280:720
VBITRATE=2500k
VMAXRATE=2800k
VBUFSIZE=5000k
EOF
done
echo "  .env creados: $(ls -1 /etc/nexora-tc/*.env | wc -l)"

echo "=== firewall: 8088 solo para el edge ==="
apt-get install -y -qq ufw >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow 22/tcp >/dev/null
ufw allow from "${EDGE_IP}" to any port 8088 proto tcp >/dev/null
ufw --force enable >/dev/null
ufw status numbered | head -8

echo
echo "REQUISITO EXTERNO: la cabecera 181.78.246.211 filtra por IP. Sin la IP de"
echo "esta maquina en su lista blanca, ffmpeg no recibe nada (ni siquiera ping)."
