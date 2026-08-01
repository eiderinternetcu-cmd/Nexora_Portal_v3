#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compara la config de nginx VERSIONADA con la que corre de verdad.

POR QUE EXISTE (P0.9.2 — la causa raiz, no el sintoma)
------------------------------------------------------
Produccion NO es un repositorio git: el codigo llega por copia (rsync/scp), asi
que `git status` limpio no significa "produccion esta al dia". El 27-jul-2026 un
despliegue subio una copia rancia de `snippets/stream-gate.conf` que PISO un
arreglo que ya estaba en git (el `location ^~ /stream/tc-mia/`, commit c1f76e1,
02:11) una hora despues de commitearlo. Resultado: 12,5 h con los canales de
Miami devolviendo el HTML del SPA con 200 en las dos marcas.
Ver docs/ANALISIS_BYPASS_TCMIA.md § 8.

Ese despliegue no fallo por un error de nginx: fallo porque NADIE COMPARO antes
de copiar. Esta herramienta es esa comparacion, y esta pensada para ejecutarse
ANTES de copiar nada.

COMO SE USA (dos pasos, sin credenciales embebidas)
---------------------------------------------------
No abre conexiones, no sabe la IP del servidor y no lee ninguna clave: consume
la salida de `nginx -T` que el operador ya sabe obtener, por fichero o por
stdin. Asi sirve igual con docker, con ssh, con un pegado manual o con un
volcado guardado de hace una semana.

  1) En el servidor, volcar la config VIVA (solo lectura):

       sudo docker exec nexora_nginx nginx -T > /tmp/nginx-vivo.txt 2>/dev/null

  2) En la maquina que tenga el repo, comparar:

       python scripts/nginx_config_diff.py /tmp/nginx-vivo.txt

     o encadenado, sin fichero intermedio:

       ssh operador@servidor 'sudo docker exec nexora_nginx nginx -T 2>/dev/null' \
         | python scripts/nginx_config_diff.py -

CODIGOS DE SALIDA (pensados para encadenar con && )
---------------------------------------------------
  0  Todo coincide. Se puede desplegar.
  1  Hay divergencia. NO copies hasta entenderla (ver el runbook, seccion 10.2).
  2  Error de uso o de E/S (no se pudo comparar: no vale como "todo bien").

  Ejemplo de uso como puerta:

      python scripts/nginx_config_diff.py /tmp/nginx-vivo.txt && ./desplegar.sh

QUE COMPARA, Y LAS TRES CLASES DE HALLAZGO
------------------------------------------
  DIFIERE      El fichero esta en los dos sitios y el contenido no coincide.
               -> alguien edito en caliente, o el repo lleva algo sin desplegar.
  SOLO EN VIVO nginx carga un fichero que el repo no versiona.
               -> es el caso de `/etc/nginx/nginx.conf` (P0.9.4): si alguien
                  recrea el contenedor, ese fichero no se puede reconstruir.
  FALTA        Esta versionado y nginx NO lo carga.
               -> ES EXACTAMENTE EL FALLO DE tc-mia EN FORMA DE FICHERO: el
                  arreglo existe en git y produccion no lo tiene.

Por defecto la comparacion ignora comentarios y espaciado (compara DIRECTIVAS,
que es lo que cambia el comportamiento de nginx). Con --raw compara byte a byte.
"""

from __future__ import annotations

import argparse
import difflib
import fnmatch
import os
import re
import sys

# Marcador que nginx -T emite antes del contenido de cada fichero.
FILE_HEADER_RE = re.compile(r"^#\s*configuration file\s+(/\S+):\s*$")

# Lineas de ruido que nginx -T escribe fuera del volcado (van a stderr, pero el
# operador suele redirigir con 2>&1 y acaban mezcladas).
NOISE_PREFIX = "nginx:"

# Correspondencia por defecto: prefijo dentro del contenedor -> ruta en el repo.
# Refleja docker-compose.production.yml (monta los DIRECTORIOS, no un fichero).
DEFAULT_MAPS = [
    ("/etc/nginx/conf.d/", "deploy/nginx/conf.d/"),
    ("/etc/nginx/snippets/", "deploy/nginx/snippets/"),
]

EXIT_OK = 0
EXIT_DIFF = 1
EXIT_ERROR = 2


def strip_inline_comment(line: str) -> str:
    """Quita el comentario `#` respetando comillas.

    Necesario porque el gate devuelve cuerpos JSON entre comillas simples
    (`return 403 '{"success":false,...}'`) y los `log_format` llevan cadenas
    largas: un split ingenuo por '#' podria partir una directiva real.
    """
    out = []
    quote = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\" and i + 1 < len(line):
                out.append(ch)
                i += 1
                out.append(line[i])
                i += 1
                continue
            if ch == quote:
                quote = None
            out.append(ch)
        else:
            if ch in ('"', "'"):
                quote = ch
                out.append(ch)
            elif ch == "#":
                break
            else:
                out.append(ch)
        i += 1
    return "".join(out)


def normalize(text: str, raw: bool) -> list[str]:
    """Reduce el texto a la lista de lineas que se comparan."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if raw:
        while lines and lines[-1] == "":
            lines.pop()
        return lines
    out = []
    for line in lines:
        cleaned = " ".join(strip_inline_comment(line).split())
        if cleaned:
            out.append(cleaned)
    return out


def parse_dump(text: str) -> "dict[str, str]":
    """Trocea la salida de `nginx -T` en {ruta_en_el_contenedor: contenido}."""
    files: dict[str, list[str]] = {}
    current = None
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        match = FILE_HEADER_RE.match(line)
        if match:
            current = match.group(1)
            files.setdefault(current, [])
            continue
        if current is None:
            # Preambulo: "nginx: the configuration file ... syntax is ok", etc.
            continue
        if line.startswith(NOISE_PREFIX):
            continue
        files[current].append(line)
    return {path: "\n".join(body) for path, body in files.items()}


def to_repo_path(live_path: str, maps: "list[tuple[str, str]]") -> "str | None":
    for live_prefix, repo_prefix in maps:
        if live_path.startswith(live_prefix):
            return repo_prefix + live_path[len(live_prefix):]
    return None


def read_repo_file(repo_root: str, rel_path: str) -> str:
    with open(os.path.join(repo_root, rel_path), "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def list_repo_confs(repo_root: str, maps: "list[tuple[str, str]]") -> "list[tuple[str, str]]":
    """Devuelve [(ruta_relativa, ruta_equivalente_en_vivo)] de todo *.conf versionado."""
    found = []
    for live_prefix, repo_prefix in maps:
        directory = os.path.join(repo_root, repo_prefix)
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".conf"):
                continue
            if not os.path.isfile(os.path.join(directory, name)):
                continue
            found.append((repo_prefix + name, live_prefix + name))
    return found


def parse_map_option(value: str) -> "tuple[str, str]":
    if "=" not in value:
        raise argparse.ArgumentTypeError(
            "--map espera VIVO=REPO, por ejemplo /etc/nginx/conf.d/=deploy/nginx/conf.d/"
        )
    live, repo = value.split("=", 1)
    if not live.endswith("/"):
        live += "/"
    if not repo.endswith("/"):
        repo += "/"
    return live, repo


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nginx_config_diff.py",
        description="Compara la config de nginx versionada con la que corre en produccion.",
        epilog=(
            "Volcado en el servidor (solo lectura):\n"
            "  sudo docker exec nexora_nginx nginx -T > /tmp/nginx-vivo.txt 2>/dev/null\n"
            "Comparacion:\n"
            "  python scripts/nginx_config_diff.py /tmp/nginx-vivo.txt\n"
            "Salida: 0 = coincide, 1 = diverge, 2 = error."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "dump",
        help="Fichero con la salida de `nginx -T`, o '-' para leerla de stdin.",
    )
    parser.add_argument(
        "--repo-root",
        default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        help="Raiz del repositorio (por defecto: la que contiene a este script).",
    )
    parser.add_argument(
        "--map",
        dest="maps",
        action="append",
        type=parse_map_option,
        metavar="VIVO=REPO",
        help="Correspondencia ruta-en-contenedor=ruta-en-repo. Repetible. "
             "Si se usa, sustituye a las por defecto.",
    )
    parser.add_argument(
        "--allow-unversioned",
        dest="allowed",
        action="append",
        default=[],
        metavar="GLOB",
        help="Ruta viva que puede no estar versionada sin que eso cuente como "
             "divergencia (repetible). Ej: --allow-unversioned '/etc/nginx/mime.types'",
    )
    parser.add_argument("--raw", action="store_true",
                        help="Comparar byte a byte, sin ignorar comentarios ni espaciado.")
    parser.add_argument("--context", type=int, default=3, metavar="N",
                        help="Lineas de contexto del diff (por defecto 3).")
    parser.add_argument("--quiet", action="store_true",
                        help="Solo el resumen y el veredicto, sin el diff completo.")
    return parser


def main(argv: "list[str]") -> int:
    args = build_parser().parse_args(argv)
    maps = args.maps if args.maps else list(DEFAULT_MAPS)
    repo_root = os.path.abspath(args.repo_root)

    if not os.path.isdir(repo_root):
        print("ERROR: --repo-root no es un directorio: %s" % repo_root, file=sys.stderr)
        return EXIT_ERROR

    try:
        if args.dump == "-":
            text = sys.stdin.read()
        else:
            with open(args.dump, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
    except OSError as exc:
        print("ERROR: no se pudo leer el volcado: %s" % exc, file=sys.stderr)
        return EXIT_ERROR

    live_files = parse_dump(text)
    if not live_files:
        print("ERROR: el volcado no contiene ninguna linea "
              "'# configuration file /...:'.", file=sys.stderr)
        print("       ¿Seguro que es la salida de `nginx -T` y no de `nginx -t`?",
              file=sys.stderr)
        return EXIT_ERROR

    identical, differing, only_live, missing, unreadable = [], [], [], [], []

    # --- 1. Lo que nginx carga, contra lo versionado --------------------------
    for live_path in sorted(live_files):
        rel = to_repo_path(live_path, maps)
        if rel is None:
            if any(fnmatch.fnmatch(live_path, pattern) for pattern in args.allowed):
                continue
            only_live.append(live_path)
            continue
        try:
            repo_text = read_repo_file(repo_root, rel)
        except OSError as exc:
            unreadable.append((rel, live_path, str(exc)))
            continue

        repo_lines = normalize(repo_text, args.raw)
        live_lines = normalize(live_files[live_path], args.raw)
        if repo_lines == live_lines:
            identical.append((rel, live_path))
        else:
            diff = list(difflib.unified_diff(
                repo_lines, live_lines,
                fromfile="repo:  %s" % rel,
                tofile="vivo:  %s" % live_path,
                lineterm="", n=args.context,
            ))
            differing.append((rel, live_path, diff))

    # --- 2. Lo versionado que nginx NO carga (el fallo de tc-mia) -------------
    for rel, live_equivalent in list_repo_confs(repo_root, maps):
        if live_equivalent not in live_files:
            missing.append((rel, live_equivalent))

    # --- 3. Informe -----------------------------------------------------------
    print("=" * 78)
    print("COMPARACION nginx: repo <-> vivo")
    print("  repo:    %s" % repo_root)
    print("  volcado: %s" % ("stdin" if args.dump == "-" else os.path.abspath(args.dump)))
    print("  modo:    %s" % ("byte a byte (--raw)" if args.raw
                             else "solo directivas (comentarios y espaciado ignorados)"))
    print("=" * 78)

    for rel, live_path in identical:
        print("  OK            %s" % rel)

    for rel, live_path, diff in differing:
        print("  DIFIERE       %s" % rel)
        if not args.quiet:
            for line in diff:
                print("      %s" % line)

    for live_path in only_live:
        print("  SOLO EN VIVO  %s   (nginx lo carga; el repo no lo versiona)" % live_path)

    for rel, live_equivalent in missing:
        print("  FALTA         %s   (versionado, pero nginx NO lo carga)" % rel)

    for rel, live_path, err in unreadable:
        print("  ILEGIBLE      %s   (%s)" % (rel, err))

    print("-" * 78)
    print("  identicos: %d | difieren: %d | solo en vivo: %d | faltan en vivo: %d | ilegibles: %d"
          % (len(identical), len(differing), len(only_live), len(missing), len(unreadable)))

    problems = len(differing) + len(only_live) + len(missing) + len(unreadable)
    if problems == 0:
        print("  VEREDICTO: coinciden. Se puede desplegar.")
        print("=" * 78)
        return EXIT_OK

    print("  VEREDICTO: DIVERGEN. No copies nada hasta entender por que.")
    if differing:
        print("             'DIFIERE' con contenido que no esta en git = alguien edito")
        print("             en caliente. Copiar el repo encima BORRA ese cambio.")
    if missing:
        print("             'FALTA' = el arreglo esta en git y produccion no lo tiene.")
        print("             Es la forma exacta del incidente de tc-mia (12,5 h).")
    if only_live:
        print("             'SOLO EN VIVO' = fichero cargado y no versionado: si se")
        print("             recrea el contenedor, no hay de donde reconstruirlo (P0.9.4).")
    # Solo ASCII en lo que se imprime: la consola de Windows por defecto no es
    # UTF-8 y un caracter suelto convierte una salida util en ruido.
    print("             Procedimiento: deploy/RUNBOOK_EDGE_MULTIDOMINIO.md, seccion 10.")
    print("=" * 78)
    return EXIT_DIFF


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
