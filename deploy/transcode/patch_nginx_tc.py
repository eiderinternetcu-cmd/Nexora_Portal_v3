#!/usr/bin/env python3
"""Anade el location /stream/tc-main/ al conf de nginx de produccion.

Idempotente: si el bloque ya esta, no toca nada. Se inserta JUSTO ANTES del
`location / {` que hace de catch-all hacia el web player, para que la regla mas
especifica gane sin depender del orden de nginx.
"""
import re
import sys

CONF = "/opt/nexora_api/deploy/nginx/nexoraplay.conf"
MARK = "/stream/tc-main/"

BLOCK = """    # ── Canales transcodificados (nodo virtual tc-main) ─────────────────────────
    # Mismo gate que ec-main/co-main: el manifiesto exige ?token= y los segmentos
    # pasan por el grant de Redis. El origen es el contenedor nexora_hls, que no
    # publica puertos: sin este proxy no se alcanza desde fuera.
    location ^~ /stream/tc-main/ {
        set $stream_orig_uri $request_uri;
        set $stream_token $arg_token;
        auth_request /__stream_auth;
        error_page 401 = @stream_denied;
        error_page 403 = @stream_denied;
        access_log /dev/stdout stream_safe;

        proxy_pass http://nexora_hls:80/;
        proxy_http_version 1.1;
        proxy_set_header Host $proxy_host;
        proxy_set_header Range $http_range;
        proxy_set_header If-Range $http_if_range;
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 60s;
        proxy_redirect off;
    }

"""

with open(CONF, "r", encoding="utf-8") as fh:
    conf = fh.read()

if MARK in conf:
    print("ya estaba: no se toca nada")
    sys.exit(0)

# El catch-all del web player es el ancla de insercion.
anchor = re.search(r"\n(    location / \{\n        proxy_pass http://nexora_web_player)", conf)
if not anchor:
    sys.exit("ERROR: no encuentro el location / del web player; no toco el fichero")

out = conf[: anchor.start(1)] + BLOCK + conf[anchor.start(1) :]

with open(CONF, "w", encoding="utf-8") as fh:
    fh.write(out)

print("insertado location /stream/tc-main/ antes del catch-all")
