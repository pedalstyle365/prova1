import os
import csv
import io
import gzip
import json
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
PRODUCTOS_POR_EJECUCION = 1
SEGUNDOS_ENTRE_MENSAJES = 3
DESCUENTO_MINIMO = 10
MAX_IDS_GUARDADOS = 1000

SLOTS = {
    "slot_08": (8, 0),
    "slot_12": (12, 0),
    "slot_16": (16, 0),
    "slot_20": (20, 0),
}
MARGEN_SLOT_MIN = 90

# Palabras que excluyen un producto (targetas regalo, etc.)
EXCLUIR_TITULO = ["tarjeta regalo", "gift card", "tarjeta de regalo"]


def obtener_slot(ahora):
    for nombre, (h, m) in SLOTS.items():
        inicio = ahora.replace(hour=h, minute=m, second=0, microsecond=0)
        delta = (ahora - inicio).total_seconds() / 60
        if 0 <= delta <= MARGEN_SLOT_MIN:
            return nombre
    return None


def cargar_estado():
    if not os.path.exists(ESTADO_PATH):
        return {"publicados": []}
    try:
        with open(ESTADO_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"publicados": []}


def guardar_estado(estado):
    if len(estado.get("publicados", [])) > MAX_IDS_GUARDADOS:
        estado["publicados"] = estado["publicados"][-MAX_IDS_GUARDADOS:]
    with open(ESTADO_PATH, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)


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
        print("utf-8 falló, probando latin-1...")
        texto = contenido.decode("latin-1")
    print(f"[{datetime.now()}] Feed descargado ({len(texto)} caracteres).")
    return io.StringIO(texto)


def parsear_precio(valor):
    """'50.00 EUR' -> 50.0 ; '1.234,56' -> 1234.56 ; '' -> None"""
    if not valor:
        return None
    # Quitar 'EUR', espacios, '%'
    limpio = valor.strip().replace("EUR", "").replace("€", "").replace("%", "").strip()
    # Si tiene coma y punto, asumimos formato europeo (1.234,56)
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


def extraer_precios(fila):
    """
    Devuelve (precio_actual, precio_anterior) en float.
    - precio_actual = sale_price si existe y es > 0, si no price
    - precio_anterior = price si sale_price < price, si no None
    """
    price = parsear_precio(fila.get("price", ""))
    sale = parsear_precio(fila.get("sale_price", ""))
    if sale and sale > 0 and price and sale < price - 0.01:
        return sale, price
    return price, None


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

    # 1) Marca
    h_marca = normalizar_hashtag(fila.get("brand", ""))
    if h_marca and f"#{h_marca}".lower() not in vistos:
        hashtags.append(f"#{h_marca}")
        vistos.add(f"#{h_marca}".lower())

    # 2) Categoría (última parte del path de google_product_category)
    cat = (fila.get("google_product_category") or "").strip()
    cat = cat.split(">")[-1].strip() if cat else ""
    h_cat = normalizar_hashtag(cat)
    if h_cat and f"#{h_cat}".lower() not in vistos and len(hashtags) < 3:
        hashtags.append(f"#{h_cat}")
        vistos.add(f"#{h_cat}".lower())

    # 3) Relleno deportivo
    relleno = ["#Running", "#Trail", "#Zapatillas", "#Esport"]
    for r in relleno:
        if len(hashtags) >= 3:
            break
        if r.lower() not in vistos:
            hashtags.append(r)
            vistos.add(r.lower())

    return hashtags[:3]


def enviar_a_telegram(nombre, precio_antes_txt, precio_actual_txt, descuento_pct, imagen_url, enlace, hashtags):
    lineas = [f"<b>{nombre}</b>", ""]
    if precio_antes_txt:
        lineas.append(f"❌ Antes: <s>{precio_antes_txt}</s>")
    linea_ahora = f"✅ Ahora: <b>{precio_actual_txt}</b>"
    if descuento_pct:
        linea_ahora += f"  🔥 <b>-{descuento_pct}%</b>"
    lineas.append(linea_ahora)
    lineas.append("")
    lineas.append(" ".join(hashtags))
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
        print(f"[{datetime.now()}] ✅ Enviado: {nombre[:60]} (-{descuento_pct}%)")
        return True
    print(f"[{datetime.now()}] ❌ Error Telegram: {r.status_code} - {r.text}")
    return False


def publicar_lote(lector, publicados, maximo):
    enviados = 0
    filas_leidas = 0
    motivos = {
        "sin_campos": 0,
        "ya_publicado": 0,
        "sin_oferta": 0,
        "descuento_bajo": 0,
        "excluido": 0,
        "sin_stock": 0,
    }

    for fila in lector:
        filas_leidas += 1
        if enviados >= maximo:
            break

        pid = (fila.get("id") or "").strip()
        nombre = (fila.get("title") or "").strip()
        imagen = (fila.get("image_link") or "").strip()
        enlace = (fila.get("aw_deep_link") or "").strip()

        if not (pid and nombre and imagen and enlace):
            motivos["sin_campos"] += 1
            continue
        if pid in publicados:
            motivos["ya_publicado"] += 1
            continue

        # Excluir targetas regalo y similares
        nombre_lower = nombre.lower()
        if any(x in nombre_lower for x in EXCLUIR_TITULO):
            motivos["excluido"] += 1
            continue

        # Solo productos en stock
        if (fila.get("availability") or "").strip().lower() != "in_stock":
            motivos["sin_stock"] += 1
            continue

        precio_actual, precio_antes = extraer_precios(fila)
        if not precio_actual or precio_antes is None:
            motivos["sin_oferta"] += 1
            continue

        descuento_pct = round((precio_antes - precio_actual) / precio_antes * 100)
        if descuento_pct < DESCUENTO_MINIMO:
            motivos["descuento_bajo"] += 1
            continue

        hashtags = construir_hashtags(fila)

        if enviar_a_telegram(
            nombre=nombre,
            precio_antes_txt=formatear_precio(precio_antes),
            precio_actual_txt=formatear_precio(precio_actual),
            descuento_pct=descuento_pct,
            imagen_url=imagen,
            enlace=enlace,
            hashtags=hashtags,
        ):
            publicados.add(pid)
            enviados += 1
            if enviados < maximo:
                time.sleep(SEGUNDOS_ENTRE_MENSAJES)
        else:
            print("Deteniendo lote por error de Telegram.")
            break

    print(f"\n=== RESUMEN ===")
    print(f"Filas leídas: {filas_leidas}")
    print(f"Motivos de descarte: {motivos}")
    print(f"Enviados: {enviados}")
    print("================\n")
    return enviados


def main():
    ahora = datetime.now(MADRID)
    estado = cargar_estado()
    manual = EVENT_NAME != "schedule"

    if manual:
        slot = None
        print(f"[{ahora.strftime('%Y-%m-%d %H:%M')} Madrid] Ejecución MANUAL: publica sin restricciones.")
    else:
        slot = obtener_slot(ahora)
        hoy = ahora.strftime("%Y-%m-%d")
        if slot is None:
            print(f"[{ahora.strftime('%Y-%m-%d %H:%M')} Madrid] Fuera de ventana de slot. Saliendo.")
            return
        if estado.get(f"last_{slot}") == hoy:
            print(f"[{ahora.strftime('%Y-%m-%d %H:%M')} Madrid] Slot '{slot}' ya publicado hoy. Saliendo.")
            return
        print(f"[{ahora.strftime('%Y-%m-%d %H:%M')} Madrid] Ejecución automática: slot '{slot}' pendiente.")

    publicados = set(estado.get("publicados", []))
    print(f"Estado cargado: {len(publicados)} productos ya publicados.")

    lector = csv.DictReader(descargar_feed(AWIN_FEED_URL))
    enviados = publicar_lote(lector, publicados, PRODUCTOS_POR_EJECUCION)

    if enviados == 0 and not manual:
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
