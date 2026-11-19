import os
import csv
import io
import gzip
import json
import random
import re
import time
import unicodedata
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

# ===== CONFIGURACIÓN =====
AWIN_FEED_URL = os.environ.get("AWIN_FEED_URL")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

EVENT_NAME = os.environ.get("GITHUB_EVENT_NAME", "workflow_dispatch")

MADRID = ZoneInfo("Europe/Madrid")
ESTADO_PATH = "published.json"
HISTORIAL_PATH = "price_history.json"
PRODUCTOS_POR_EJECUCION = 1
SEGUNDOS_ENTRE_MENSAJES = 3
DESCUENTO_MINIMO = 10
MAX_IDS_GUARDADOS = 1000
MAX_HISTORIAL = 5000

SLOTS = {
    "slot_08": (8, 0),
    "slot_12": (12, 0),
    "slot_16": (16, 0),
    "slot_20": (20, 0),
}
MARGEN_SLOT_MIN = 90

EXCLUIR_TITULO = ["tarjeta regalo", "gift card", "tarjeta de regalo"]

TELEGRAM_TIMEOUT = 30
TELEGRAM_REINTENTOS = 3
TELEGRAM_ESPERA_REINTENTO = 5


# ===== SLOT =====
def obtener_slot(ahora):
    for nombre, (h, m) in SLOTS.items():
        inicio = ahora.replace(hour=h, minute=m, second=0, microsecond=0)
        delta = (ahora - inicio).total_seconds() / 60
        if 0 <= delta <= MARGEN_SLOT_MIN:
            return nombre
    return None


# ===== ESTADO =====
def cargar_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def guardar_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def cargar_estado():
    return cargar_json(ESTADO_PATH, {"publicados": []})


def guardar_estado(estado):
    if len(estado.get("publicados", [])) > MAX_IDS_GUARDADOS:
        estado["publicados"] = estado["publicados"][-MAX_IDS_GUARDADOS:]
    guardar_json(ESTADO_PATH, estado)


def cargar_historial():
    return cargar_json(HISTORIAL_PATH, {})


def guardar_historial(historial):
    if len(historial) > MAX_HISTORIAL:
        claves = list(historial.keys())[-MAX_HISTORIAL:]
        historial = {k: historial[k] for k in claves}
    guardar_json(HISTORIAL_PATH, historial)


# ===== FEED =====
def descargar_feed(url):
    print(f"[{datetime.now()}] Descargando feed...")
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    contenido = r.content
    if contenido[:2] == b"\x1f\x8b":
        print("Detectado gzip, descomprimiendo...")
        contenido = gzip.decompress(contenido)
    try:
        texto = contenido.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = contenido.decode("latin-1")
    print(f"[{datetime.now()}] Feed descargado ({len(texto)} caracteres).")
    return io.StringIO(texto)


# ===== PRECIOS =====
def parsear_precio(valor):
    if valor is None or valor == "":
        return None
    limpio = str(valor).strip().replace("EUR", "").replace("€", "").replace("%", "").strip()
    if "," in limpio and "." in limpio:
        limpio = limpio.replace(".", "").replace(",", ".")
    elif "," in limpio:
        limpio = limpio.replace(",", ".")
    try:
        return float(limpio)
    except ValueError:
        return None


def formatear_precio(valor):
    if valor is None:
        return ""
    s = f"{valor:,.2f}"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} €"


# ===== HASHTAGS =====
def normalizar_hashtag(texto):
    if not texto:
        return ""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^0-9A-Za-z\s]", " ", texto)
    palabras = [p.capitalize() for p in texto.split() if p]
    return "".join(palabras)


def construir_hashtags(fila):
    hashtags = ["#RunningOfertes"]
    vistos = {"#runningofertes"}
    h_marca = normalizar_hashtag(fila.get("brand", ""))
    if h_marca and f"#{h_marca}".lower() not in vistos:
        hashtags.append(f"#{h_marca}")
        vistos.add(f"#{h_marca}".lower())
    cat = (fila.get("google_product_category") or "").strip()
    cat = cat.split(">")[-1].strip() if cat else ""
    h_cat = normalizar_hashtag(cat)
    if h_cat and f"#{h_cat}".lower() not in vistos and len(hashtags) < 3:
        hashtags.append(f"#{h_cat}")
        vistos.add(f"#{h_cat}".lower())
    relleno = ["#Running", "#Trail", "#Zapatillas", "#Esport"]
    for r in relleno:
        if len(hashtags) >= 3:
            break
        if r.lower() not in vistos:
            hashtags.append(r)
            vistos.add(r.lower())
    return hashtags[:3]


# ===== TELEGRAM =====
def enviar_texto(caption, enlace):
    reply_markup = json.dumps({
        "inline_keyboard": [[{"text": "🛒 Ver oferta", "url": enlace}]]
    })
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": caption,
                "parse_mode": "HTML",
                "reply_markup": reply_markup,
                "disable_web_page_preview": "true",
            },
            timeout=TELEGRAM_TIMEOUT,
        )
        if r.ok:
            print(f"[{datetime.now()}] ✅ Texto enviado (fallback)")
            return True
        print(f"[{datetime.now()}] ❌ Error texto: {r.status_code} - {r.text[:200]}")
    except requests.exceptions.RequestException as e:
        print(f"[{datetime.now()}] ❌ Timeout texto: {e}")
    return False


def enviar_foto(nombre, caption, imagen_bytes, enlace):
    reply_markup = json.dumps({
        "inline_keyboard": [[{"text": "🛒 Ver oferta", "url": enlace}]]
    })
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "caption": caption,
        "parse_mode": "HTML",
        "reply_markup": reply_markup,
    }
    for intento in range(1, TELEGRAM_REINTENTOS + 1):
        try:
            print(f"[DEBUG] sendPhoto: imagen={len(imagen_bytes)} bytes, intento {intento}")
            files = {"photo": ("foto.jpg", imagen_bytes, "image/jpeg")}
            r = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
                data=payload,
                files=files,
                timeout=TELEGRAM_TIMEOUT,
            )
            if r.ok:
                print(f"[{datetime.now()}] ✅ Foto enviada (intento {intento}): {nombre[:60]}")
                return True
            print(f"[DEBUG] Telegram respondió {r.status_code}: {r.text[:200]}")
            if 400 <= r.status_code < 500:
                print(f"[{datetime.now()}] ❌ Error 4xx (no reintentable)")
                return False
            print(f"[{datetime.now()}] ⚠️ Error {r.status_code}, reintentando...")
        except requests.exceptions.RequestException as e:
            print(f"[{datetime.now()}] ⚠️ Timeout/error en intento {intento}: {e}")
        if intento < TELEGRAM_REINTENTOS:
            time.sleep(TELEGRAM_ESPERA_REINTENTO)
    print(f"[{datetime.now()}] ❌ sendPhoto falló tras {TELEGRAM_REINTENTOS} intentos")
    return False


def enviar_a_telegram(nombre, precio_antes_txt, precio_actual_txt, descuento_pct, imagen_bytes, enlace, hashtags):
    lineas = [f"<b>{nombre}</b>", ""]
    if precio_antes_txt and descuento_pct:
        lineas.append(f"❌ Antes: <s>{precio_antes_txt}</s>")
        lineas.append(f"✅ Ahora: <b>{precio_actual_txt}</b>  🔥 <b>-{descuento_pct}%</b>")
    else:
        lineas.append(f"💶 Precio: <b>{precio_actual_txt}</b>")
    lineas.append("")
    lineas.append(" ".join(hashtags))
    caption = "\n".join(lineas)[:1024]

    if imagen_bytes and enviar_foto(nombre, caption, imagen_bytes, enlace):
        return True
    print(f"[{datetime.now()}] 🔄 Fallback a texto para: {nombre[:60]}")
    return enviar_texto(caption, enlace)


# ===== CANDIDATOS =====
def recopilar_candidatos(lector, publicados):
    candidatos = []
    for fila in lector:
        pid = (fila.get("id") or "").strip()
        nombre = (fila.get("title") or "").strip()
        imagen = (fila.get("image_link") or "").strip()
        enlace = (fila.get("aw_deep_link") or "").strip()
        availability = (fila.get("availability") or "").strip().lower()

        if not (pid and nombre and imagen and enlace):
            continue
        if pid in publicados:
            continue
        if availability != "in_stock":
            continue
        if any(x in nombre.lower() for x in EXCLUIR_TITULO):
            continue

        candidatos.append({
            "pid": pid,
            "nombre": nombre,
            "imagen": imagen,
            "enlace": enlace,
            "fila": fila,
        })
    print(f"Candidatos: {len(candidatos)}")
    return candidatos


# ===== PUBLICACIÓN =====
def publicar_lote(candidatos, publicados, historial, maximo):
    enviados = 0
    random.shuffle(candidatos)

    for c in candidatos:
        if enviados >= maximo:
            break

        pid = c["pid"]
        precio_actual = parsear_precio(c["fila"].get("price", ""))
        if not precio_actual:
            continue

        precio_max_visto = historial.get(pid)

        descuento_pct = None
        precio_antes_txt = None
        if precio_max_visto and precio_max_visto > precio_actual + 0.01:
            desc_calc = round((precio_max_visto - precio_actual) / precio_max_visto * 100)
            if desc_calc >= DESCUENTO_MINIMO:
                descuento_pct = desc_calc
                precio_antes_txt = formatear_precio(precio_max_visto)

        if precio_max_visto is None or precio_actual > precio_max_visto:
            historial[pid] = precio_actual

        imagen_bytes = None
        try:
            r = requests.get(c["imagen"], timeout=20)
            if r.ok and len(r.content) > 0:
                imagen_bytes = r.content
        except Exception as e:
            print(f"[{datetime.now()}] ⚠️ Error descargando imagen: {e}")

        hashtags = construir_hashtags(c["fila"])

        if enviar_a_telegram(
            nombre=c["nombre"],
            precio_antes_txt=precio_antes_txt,
            precio_actual_txt=formatear_precio(precio_actual),
            descuento_pct=descuento_pct,
            imagen_bytes=imagen_bytes,
            enlace=c["enlace"],
            hashtags=hashtags,
        ):
            publicados.add(pid)
            enviados += 1
            if enviados < maximo:
                time.sleep(SEGUNDOS_ENTRE_MENSAJES)
        else:
            print("Deteniendo lote por error de Telegram.")
            break
    return enviados


# ===== MAIN =====
def main():
    ahora = datetime.now(MADRID)
    estado = cargar_estado()
    historial = cargar_historial()
    manual = EVENT_NAME != "schedule"

    if manual:
        slot = None
        print(f"[{ahora.strftime('%Y-%m-%d %H:%M')} Madrid] Ejecución MANUAL.")
    else:
        slot = obtener_slot(ahora)
        hoy = ahora.strftime("%Y-%m-%d")
        if slot is None:
            print(f"[{ahora.strftime('%Y-%m-%d %H:%M')} Madrid] Fuera de ventana. Saliendo.")
            return
        if estado.get(f"last_{slot}") == hoy:
            print(f"[{ahora.strftime('%Y-%m-%d %H:%M')} Madrid] Slot '{slot}' ya publicado. Saliendo.")
            return
        print(f"[{ahora.strftime('%Y-%m-%d %H:%M')} Madrid] Slot '{slot}' pendiente.")

    publicados = set(estado.get("publicados", []))
    print(f"Publicados: {len(publicados)} | Historial: {len(historial)}")

    lector = csv.DictReader(descargar_feed(AWIN_FEED_URL))
    candidatos = recopilar_candidatos(lector, publicados)

    enviados = publicar_lote(candidatos, publicados, historial, PRODUCTOS_POR_EJECUCION)

    estado["publicados"] = sorted(publicados)
    if slot:
        estado[f"last_{slot}"] = ahora.strftime("%Y-%m-%d")
    guardar_estado(estado)
    guardar_historial(historial)
    print(f"Publicados en esta ejecución: {enviados}")


if __name__ == "__main__":
    main()
