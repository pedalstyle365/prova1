import os
import csv
import io
import gzip
import requests
from datetime import datetime

AWIN_FEED_URL = os.environ.get("AWIN_FEED_URL")

def descargar_feed(url):
    print(f"[{datetime.now()}] Descargando feed...")
    print(f"URL (primeros 120 chars): {url[:120]}")
    r = requests.get(url, timeout=120)
    print(f"HTTP status: {r.status_code}")
    print(f"Content-Length header: {r.headers.get('Content-Length', 'desconocido')}")
    print(f"Content-Type header: {r.headers.get('Content-Type', 'desconocido')}")
    print(f"Content-Encoding header: {r.headers.get('Content-Encoding', 'desconocido')}")
    r.raise_for_status()
    contenido = r.content
    print(f"Bytes recibidos: {len(contenido)}")
    print(f"Primeros 16 bytes: {contenido[:16]}")
    if contenido[:2] == b"\x1f\x8b":
        print("Detectado gzip, descomprimiendo...")
        contenido = gzip.decompress(contenido)
        print(f"Bytes tras descomprimir: {len(contenido)}")
    try:
        texto = contenido.decode("utf-8-sig")
    except UnicodeDecodeError:
        print("utf-8 falló, probando latin-1...")
        texto = contenido.decode("latin-1")
    print(f"[{datetime.now()}] Feed descargado ({len(texto)} caracteres).")
    print(f"Primeras 500 chars del texto:")
    print(texto[:500])
    print("...")
    return io.StringIO(texto)

def main():
    lector = csv.DictReader(descargar_feed(AWIN_FEED_URL))
    filas = list(lector)
    print(f"\n=== TOTAL FILAS: {len(filas)} ===")
    if filas:
        print("\n=== COLUMNAS ===")
        for k in filas[0].keys():
            print(f"  {k}")
        print("\n=== PRIMERA FILA ===")
        for k, v in filas[0].items():
            print(f"  {k}: {repr(v)[:200]}")
        print("\n=== SEGUNDA FILA ===")
        if len(filas) > 1:
            for k, v in filas[1].items():
                print(f"  {k}: {repr(v)[:200]}")
        print("\n=== ÚLTIMA FILA ===")
        for k, v in filas[-1].items():
            print(f"  {k}: {repr(v)[:200]}")
    else:
        print("No hay filas. El feed está vacío o mal formado.")
    print("\n=== FI ===")

if __name__ == "__main__":
    main()
