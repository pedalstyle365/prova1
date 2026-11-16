import os
import csv
import io
import gzip
import json
import time
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

# ===== CONFIGURACIÓN =====
AWIN_FEED_URL = os.environ.get("AWIN_FEED_URL")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
FORCE = os.environ.get("FORCE", "0") == "1"   # 1 = ignora ventana y slot (solo pruebas)

MADRID = ZoneInfo("Europe/Madrid")
ESTADO_PATH = "published.json"
PRODUCTOS_POR_EJECUCION = 1
SEGUNDOS_ENTRE_MENSAJES = 3

# Ventanas amplias en hora de Madrid (toleran retrasos de GitHub de hasta ~3h)
VENTANAS = {
    "morning": range(10, 13),   # 10:00 - 12:59
    "evening": range(20, 23),   # 20:00 - 22:59
}


# ===== VENTANA HORARIA =====
def obtener_slot(ahora):
    """Devuelve 'morning', 'evening' o None según la hora de Madrid."""
    for slot, horas in VENTANAS.items():
        if ahora.hour in horas:
            return slot
    return None


# ===== ESTADO =====
def cargar_estado():
    if not os.path.exists(ESTADO_PATH):
        return {"publicados": []}
    try:
        with open(ESTADO_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"publicados": []}


def guardar_estado(estado):
    with open(ESTADO_PATH, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)


# ===== FEED =====
def descargar_feed(url):
    print(f"[{datetime.now()}] Descargando feed...")
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    contenido = gzip.decompress(r.content) if "gzip" in url.lower() else r.content
    texto = contenido.decode("utf-8-sig")
    print(f"[{datetime.now()}] Feed descargado ({len(texto)} caracteres).")
    return io.StringIO(texto)


# ===== PRECIOS =====
def parsear_precio(valor):
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
    s = f"{valor:,.2f}"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} €"


def elegir_precio_anterior(fila, precio_actual_num):
    """Devuelve (num, texto) solo si hay un precio anterior mayor. Si no, (None, None)."""
    if not precio_actual_num:
        return None, None
    for campo in ["product_price_old", "store_price", "rrp_price"]:
        valor = parsear_precio(fila.get(campo, ""))
        if valor and valor > precio_actual_num + 0.01:
            return valor, formatear_precio(valor)
    sp = parsear_precio(fila.get("savings_percent", ""))
    if sp:
        sp = abs(sp)
        if 0 < sp < 100:
            original = precio_actual_num / (1 - sp / 100)
            return original, formatear_precio(original)
    return None, None


# ===== TELEGRAM =====
def enviar_a_telegram(nombre, precio_antes_txt, precio_actual_txt, imagen_url, enlace):
    lineas = [f"<b>{nombre}</b>", ""]
    if precio_antes_txt:
        lineas.append(f"❌ Antes: <s>{precio_antes_txt}</s>")
    lineas.append(f"✅ Ahora: <b>{precio_actual_txt}</b>")
    caption = "\n".join(lineas)[:1024]

    reply_markup = json.dumps({
        "inline_keyboard": [[{"text": "🛒 Ver oferta", "url": enlace}]]
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
        return True
    print(f"[{datetime.now()}] ❌ Error Telegram: {r.status_code} - {r.text}")
    return False


# ===== PUBLICACIÓN =====
def publicar_lote(lector, publicados, maximo):
    enviados = 0
    for fila in lector:
        if enviados >= maximo:
            break
        pid = (fila.get("aw_product_id") or "").strip()
        nombre = (fila.get("product_name") or "").strip()
        imagen = (fila.get("merchant_image_url") or "").strip()
        enlace = (fila.get("aw_deep_link") or "").strip()
        precio_actual_num = parsear_precio(fila.get("search_price", ""))

        if not (pid and nombre and imagen and enlace and precio_actual_num and precio_actual_num > 0):
            continue
        if pid in publicados:
            continue

        _, precio_antes_txt = elegir_precio_anterior(fila, precio_actual_num)

        if enviar_a_telegram(
            nombre=nombre,
            precio_antes_txt=precio_antes_txt,
            precio_actual_txt=formatear_precio(precio_actual_num),
            imagen_url=imagen,
            enlace=enlace,
        ):
            publicados.add(pid)
            enviados += 1
            if enviados < maximo:
                time.sleep(SEGUNDOS_ENTRE_MENSAJES)
    return enviados


# ===== MAIN =====
def main():
    ahora = datetime.now(MADRID)
    estado = cargar_estado()

    if FORCE:
        slot = None
        print(f"[{ahora.strftime('%Y-%m-%d %H:%M')} Madrid] Modo FORCE activo (sin ventana ni slot).")
    else:
        slot = obtener_slot(ahora)
        if slot is None:
            print(f"[{ahora.strftime('%Y-%m-%d %H:%M')} Madrid] Fuera de ventana (hora {ahora.hour}). Saliendo sin publicar.")
            return
        hoy = ahora.strftime("%Y-%m-%d")
        if estado.get(f"last_{slot}") == hoy:
            print(f"[{ahora.strftime('%H:%M')} Madrid] Slot '{slot}' ya publicado hoy. Saliendo.")
            return

    publicados = set(estado.get("publicados", []))
    print(f"Estado cargado: {len(publicados)} productos ya publicados.")

    lector = csv.DictReader(descargar_feed(AWIN_FEED_URL))
    enviados = publicar_lote(lector, publicados, PRODUCTOS_POR_EJECUCION)

    if enviados == 0 and not FORCE:
        print("Sin productos nuevos. Reset del estado y segundo intento.")
        publicados = set()
        lector = csv.DictReader(descargar_feed(AWIN_FEED_URL))
        enviados = publicar_lote(lector, publicados, PRODUCTOS_POR_EJECUCION)

    estado["publicados"] = sorted(publicados)
    if slot:
        estado[f"last_{slot}"] = ahora.strftime("%Y-%m-%d")
    guardar_estado(estado)
    print(f"Publicados en esta ejecución: {enviados}")


if __name__ == "__main__":
    main()
