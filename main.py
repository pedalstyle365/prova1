import os
import csv
import io
import gzip
import json
import requests
from datetime import datetime

AWIN_FEED_URL = os.environ.get("AWIN_FEED_URL")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def descargar_feed(url):
    print(f"[{datetime.now()}] Descargando feed...")
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    contenido = gzip.decompress(r.content) if "gzip" in url.lower() else r.content
    texto = contenido.decode("utf-8-sig")
    print(f"[{datetime.now()}] Feed descargado ({len(texto)} caracteres).")
    return io.StringIO(texto)


def parsear_precio(valor):
    """'19.99' o '19,99' o '-20%' -> float. None si no es válido."""
    if not valor:
        return None
    try:
        return float(valor.strip().replace("%", "").replace(",", "."))
    except ValueError:
        return None


def formatear_precio(valor):
    """19.99 -> '19,99 €' ; 1234.5 -> '1.234,50 €'"""
    if valor is None:
        return ""
    s = f"{valor:,.2f}"                      # "19.99" o "1,234.50"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} €"


def elegir_precio_anterior(fila, precio_actual_num):
    """Devuelve (num, texto). None si no hay descuento real."""
    if not precio_actual_num:
        return None, None
    # 1) Campos explícitos de precio anterior
    for campo in ["product_price_old", "store_price", "rrp_price"]:
        valor = parsear_precio(fila.get(campo, ""))
        if valor and valor > precio_actual_num + 0.01:
            return valor, formatear_precio(valor)
    # 2) Fallback: calcular desde savings_percent
    sp = parsear_precio(fila.get("savings_percent", ""))
    if sp:
        sp = abs(sp)
        if 0 < sp < 100:
            original = precio_actual_num / (1 - sp / 100)
            return original, formatear_precio(original)
    return None, None


def enviar_a_telegram(nombre, precio_antes_txt, precio_actual_txt, imagen_url, enlace):
    caption = (
        f"<b>{nombre}</b>\n\n"
        f"❌ Antes: <s>{precio_antes_txt}</s>\n"
        f"✅ Ahora: <b>{precio_actual_txt}</b>"
    )[:1024]

    # Botón inline: abre tu enlace de afiliado sin preview de la web
    reply_markup = json.dumps({
        "inline_keyboard": [[
            {"text": "🛒 Ver oferta", "url": enlace}
        ]]
    })

    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "photo": imagen_url,
            "caption": caption,
            "parse_mode": "HTML",
            "reply_markup": reply_markup,
        },
        timeout=20,
    )
    if r.ok:
        print(f"[{datetime.now()}] ✅ Enviado: {nombre[:60]}")
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

        if not (nombre and imagen and enlace and precio_actual_num):
            continue

        _, precio_antes_txt = elegir_precio_anterior(fila, precio_actual_num)
        if not precio_antes_txt:
            continue  # solo publicamos si hay descuento real

        enviar_a_telegram(
            nombre=nombre,
            precio_antes_txt=precio_antes_txt,
            precio_actual_txt=formatear_precio(precio_actual_num),
            imagen_url=imagen,
            enlace=enlace,
        )
        return

    print(f"[{datetime.now()}] ⚠️ No se encontró producto con descuento válido.")


if __name__ == "__main__":
    main()
