import os
import csv
import io
import gzip
import requests
from datetime import datetime

# ===== CONFIGURACIÓN =====
AWIN_FEED_URL = os.environ.get("AWIN_FEED_URL")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def descargar_feed(url: str) -> io.StringIO:
    """Descarga el feed (GZIP) de Awin y lo devuelve como stream de texto."""
    print(f"[{datetime.now()}] Descargando feed...")
    r = requests.get(url, timeout=120)
    r.raise_for_status()

    contenido = gzip.decompress(r.content) if "gzip" in url.lower() else r.content
    texto = contenido.decode("utf-8-sig")
    print(f"[{datetime.now()}] Feed descargado ({len(texto)} caracteres).")
    return io.StringIO(texto)


def parsear_precio(valor: str):
    """Convierte '19.99' o '19,99' a float. Devuelve None si no es válido."""
    if not valor:
        return None
    try:
        return float(valor.replace(",", ".").strip())
    except ValueError:
        return None


def elegir_precio_anterior(fila: dict, precio_actual_num: float):
    """Devuelve (precio_anterior_num, texto_mostrar) usando la mejor fuente disponible."""
    candidatos = ["store_price", "product_price_old", "rrp_price"]
    for campo in candidatos:
        valor = parsear_precio(fila.get(campo, ""))
        if valor and precio_actual_num and valor > precio_actual_num:
            return valor, f"{valor:.2f} €"
    return None, None


def enviar_a_telegram(nombre, precio_antes_txt, precio_actual_txt, imagen_url, enlace):
    """Envía el producto al canal con sendPhoto + HTML."""
    partes = [f"<b>{nombre}</b>", ""]
    if precio_antes_txt:
        partes.append(f"❌ Antes: <s>{precio_antes_txt}</s>")
    partes.append(f"✅ Ahora: <b>{precio_actual_txt}</b>")
    partes.append("")
    partes.append(f"🛒 <a href='{enlace}'>Ver oferta</a>")
    caption = "\n".join(partes)[:1024]

    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "photo": imagen_url,
            "caption": caption,
            "parse_mode": "HTML",
        },
        timeout=20,
    )
    if r.ok:
        print(f"[{datetime.now()}] ✅ Enviado a Telegram: {nombre[:60]}")
    else:
        print(f"[{datetime.now()}] ❌ Error Telegram: {r.status_code} - {r.text}")


def main():
    stream = descargar_feed(AWIN_FEED_URL)
    lector = csv.DictReader(stream)

    for fila in lector:
        nombre = (fila.get("product_name") or "").strip()
        imagen = (fila.get("merchant_image_url") or "").strip()
        enlace = (fila.get("aw_deep_link") or "").strip()
        precio_actual_num = parsear_precio(fila.get("search_price", ""))
        precio_actual_txt = (fila.get("display_price") or "").strip() or (
            f"{precio_actual_num:.2f} €" if precio_actual_num else ""
        )

        if not (nombre and imagen and enlace and precio_actual_num):
            continue

        _, precio_antes_txt = elegir_precio_anterior(fila, precio_actual_num)

        enviar_a_telegram(
            nombre=nombre,
            precio_antes_txt=precio_antes_txt,
            precio_actual_txt=precio_actual_txt,
            imagen_url=imagen,
            enlace=enlace,
        )
        return  # Solo un producto en esta prueba

    print(f"[{datetime.now()}] ⚠️ No se encontró ningún producto válido en el feed.")


if __name__ == "__main__":
    main()
