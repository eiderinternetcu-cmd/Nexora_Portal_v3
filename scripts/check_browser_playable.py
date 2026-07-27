#!/usr/bin/env python3
"""Decide si un canal reproduce en navegador SIN transcodificar.

    python scripts/check_browser_playable.py AXN AE_MUNDO SONY_CHANNEL

Un canal vale para navegador si cumple dos cosas:

  1. El codec de video es H.264 (MSE no decodifica MPEG-2: en Chrome
     MediaSource.isTypeSupported('mp2v') es false).
  2. Cada segmento HLS **arranca en keyframe**. La especificacion de Media
     Source Extensions inicializa la bandera `need random access point` a true y
     descarta todo fotograma que no sea punto de acceso aleatorio, reiniciando
     esa regla al principio de cada segmento.

OJO CON EL METODO: hay que mirar los PAQUETES (`-show_entries packet=flags`),
que van en orden de decodificacion. Los FOTOGRAMAS (`frame=pict_type`) salen en
orden de presentacion, donde los B se muestran antes que el I que se decodifica
primero — y ahi un segmento perfectamente valido parece empezar en B. Mirando
fotogramas se concluye que hace falta transcodificar cuando no hace falta.

Solo lectura: no toca la cabecera ni la base de datos.
"""
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

ENV = Path(__file__).resolve().parent.parent / ".env"
BASE = "http://181.78.246.211:8002"


def load_env(path: Path) -> dict:
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=25) as r:
        return r.read()


def ffprobe(args: list, data: bytes) -> str:
    out = subprocess.run(["ffprobe", "-v", "error"] + args + ["-"],
                         input=data, capture_output=True)
    return out.stdout.decode("utf-8", "replace")


def revisar(canal: str) -> None:
    print(f"\n=== {canal} ===")
    try:
        master = fetch(f"{BASE}/{canal}/index.m3u8").decode()
    except Exception as e:
        print(f"  no responde: {e}")
        return

    variante = next((l.strip() for l in master.splitlines()
                     if l.strip() and not l.startswith("#")), None)
    if not variante:
        print("  el manifiesto no lista variantes")
        return
    url_var = variante if variante.startswith("http") else f"{BASE}/{canal}/{variante}"

    lista = fetch(url_var).decode()
    segs = [l.strip() for l in lista.splitlines()
            if l.strip() and not l.startswith("#")]
    if not segs:
        print("  la variante no lista segmentos")
        return

    seg = segs[len(segs) // 2]
    url_seg = seg if seg.startswith("http") else url_var.rsplit("/", 1)[0] + "/" + seg
    data = fetch(url_seg)

    info = ffprobe(["-select_streams", "v:0", "-show_entries",
                    "stream=codec_name,width,height", "-of", "json"], data)
    try:
        st = json.loads(info)["streams"][0]
        codec = st.get("codec_name", "?")
        dim = f"{st.get('width')}x{st.get('height')}"
    except Exception:
        codec, dim = "?", "?"

    flags = [f.strip() for f in ffprobe(
        ["-select_streams", "v:0", "-show_entries", "packet=flags",
         "-of", "csv=p=0"], data).splitlines() if f.strip()]
    keys = [i for i, f in enumerate(flags) if f.startswith("K")]
    arranca = bool(flags) and flags[0].startswith("K")
    sep = (keys[1] - keys[0]) if len(keys) > 1 else None

    print(f"  codec: {codec} {dim}   segmento: {len(data)} bytes, {len(flags)} paquetes")
    print(f"  keyframes en {keys[:5]}" + (f"   separacion: {sep} fotogramas" if sep else ""))

    if codec != "h264":
        print(f"  VEREDICTO: necesita transcodificacion — el navegador no decodifica {codec}")
    elif not arranca:
        print("  VEREDICTO: necesita transcodificacion — el segmento no arranca en keyframe")
    else:
        print("  VEREDICTO: reproduce DIRECTO, sin transcodificar")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    load_env(ENV)  # reservado por si la cabecera pasa a exigir credenciales
    for nombre in sys.argv[1:]:
        revisar(nombre)
