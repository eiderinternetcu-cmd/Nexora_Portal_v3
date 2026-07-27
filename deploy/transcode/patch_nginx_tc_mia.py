#!/usr/bin/env python3
"""Anade el location /stream/tc-mia/ (torre de Miami) al nginx de produccion.

Idempotente. Mismo gate que tc-main y que los nodos Flussonic: sin token no hay
video. La diferencia es que el origen esta al otro lado de internet, asi que
lleva timeouts de conexion explicitos.
"""
import re
import sys

CONF = "/opt/nexora_api/deploy/nginx/nexoraplay.conf"
MARK = "/stream/tc-mia/"

BLOCK = """    # ── Canales transcodificados en la torre de Miami (nodo tc-mia) ─────────────
    # Origen: 66.163.125.89:8088, cerrado por firewall a todo salvo este edge.
    # El video cruza internet, de ahi los timeouts explicitos.
    location ^~ /stream/tc-mia/ {
        set $stream_orig_uri $request_uri;
        set $stream_token $arg_token;
        auth_request /__stream_auth;
        error_page 401 = @stream_denied;
        error_page 403 = @stream_denied;
        access_log /dev/stdout stream_safe;

        proxy_pass http://66.163.125.89:8088/;
        proxy_http_version 1.1;
        proxy_set_header Host $proxy_host;
        proxy_set_header Range $http_range;
        proxy_set_header If-Range $http_if_range;
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_connect_timeout 5s;
        proxy_read_timeout 30s;
        proxy_next_upstream error timeout;
        proxy_redirect off;
    }

"""

with open(CONF, "r", encoding="utf-8") as fh:
    conf = fh.read()

if MARK in conf:
    print("ya estaba: no se toca nada")
    sys.exit(0)

anchor = re.search(r"\n(    location / \{\n        proxy_pass http://nexora_web_player)", conf)
if not anchor:
    sys.exit("ERROR: no encuentro el location / del web player; no toco el fichero")

out = conf[: anchor.start(1)] + BLOCK + conf[anchor.start(1):]
with open(CONF, "w", encoding="utf-8") as fh:
    fh.write(out)
print("insertado location /stream/tc-mia/")
