import os
import csv
import io
import gzip
import json
import time
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

AWIN_FEED_URL = os.environ.get("AWIN_FEED_URL")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
FORCE = os.environ.get("FORCE", "0") == "1"
SCHEDULE_SLOT = os.environ.get("SCHEDULE_SLOT") or None

MADRID = ZoneInfo("Europe/Madrid")
ESTADO_PATH = "published.json"
PRODUCTOS_POR_EJECUCION = 1
SEGUNDOS_ENTRE_MENSAJES = 3


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


def descargar_feed(url):
    print(f"[{datetime.now()}] Descargando feed...")
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    contenido = gzip.decompress(r.content) if "gzip" in url.lower() else r.content
    texto = contenido.decode("utf-8-sig")
    print(f"[{datetime.now()}] Feed descargado ({len(texto)} caracteres).")
    return io.StringIO(texto)


def parsear_precio(valor):
    if not valor:
        return None
    try:
        return float(valor.strip().replace("%", "").replace(",", "."))
    except ValueError:
        return None


def formatear_precio(valor):
    if valor is None:
        return ""
    s = f"{valor:,.2f}"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} €"


def elegir_precio_anterior(fila, precio_actual_num):
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


def main():
    ahora = datetime.now(MADRID)
    estado = cargar_estado()
    slot = None if FORCE else SCHEDULE_SLOT

    if FORCE:
        print(f"[{ahora.strftime('%Y-%m-%d %H:%M')} Madrid] Modo FORCE activo (sin control de slot).")
    else:
        print(f"[{ahora.strftime('%Y-%m-%d %H:%M')} Madrid] Sin restricción horaria; slot: {slot or 'manual'}.")
        if slot:
            hoy = ahora.strftime("%Y-%m-%d")
            if estado.get(f"last_{slot}") == hoy:
                print(f"Slot '{slot}' ya publicado hoy. Saliendo.")
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
