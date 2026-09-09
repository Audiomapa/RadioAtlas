#!/usr/bin/env python3
"""
=====================================================================
TRANSMISOR SSTV — RECORRIDO DE RÍO (Raspberry Pi)
=====================================================================
Sigue el trazado real de un río (por defecto, el Chinchiná en Caldas),
lo divide en N puntos y transmite un mapa por SSTV (Martin M1) en cada
ciclo, avanzando punto a punto. El recorrido completo se reparte en
varios días, cada uno con una ventana horaria de operación.

El progreso se guarda en disco (estado_transmision.json), así que si
el proceso se reinicia (corte de luz, reinicio de la RPi, etc.) retoma
exactamente donde iba.

Requisitos: pip install -r requirements_transmisor.txt
=====================================================================
"""

import os
import json
import time
import math
import random
import shutil
import argparse
import subprocess
from datetime import datetime, timezone, time as dtime

import requests
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from PIL import Image
from pysstv.color import MartinM1
from scipy.io import wavfile

# =====================================================================
# CONFIGURACIÓN — lo único que normalmente hay que tocar
# =====================================================================
NOMBRE_RIO = "Chinchiná"          # nombre del río a buscar en OpenStreetMap
AREA_ADMINISTRATIVA = "Caldas"    # departamento/área donde buscarlo

DIAS_TOTALES = 3                  # en cuántos días se reparte el recorrido completo
HORAS_POR_DIA = 6                 # horas de operación por día
HORA_INICIO = dtime(9, 0)         # hora de inicio de la ventana diaria
HORA_FIN = dtime(15, 0)           # hora de fin de la ventana diaria (debe cubrir HORAS_POR_DIA)

TIEMPO_CICLO_ESTIMADO_SEG = 225   # ~114s de audio + ~100s de silencio tras el audio + margen de mapa

# Silencio DESPUÉS de que termina de sonar el audio (no se suma al audio, es
# el tiempo aparte). El receptor dispara casi al inicio del audio, captura
# ~118s en paralelo (termina solo ~4s después de que el audio deja de sonar),
# y luego decodifica ~60-65s más — el ciclo completo del receptor da ~181-183s.
# OJO: NO ajustes este número al límite exacto — con eso probamos 70s y el
# receptor terminaba llegando tarde a escuchar (por variación normal del
# tiempo de decodificación), enganchando la transmisión ya empezada en vez
# del inicio real, y desfasando todo en cascada de ahí en adelante. Dejar
# ~30s de margen real de sobra evita ese problema.
SILENCIO_ENTRE_CICLOS_SEG = 100

# "Zoom" del mapa: en vez de un valor fijo, se calcula automáticamente para que
# cada mapa cubra aproximadamente la distancia entre un punto y el siguiente —
# así cada captura conecta visualmente con la anterior, en vez de mostrar
# siempre "lo mismo" cuando los puntos quedan muy cerca entre sí.
# FACTOR_SOLAPE > 1.0 agrega un poco de superposición entre mapas consecutivos
# (para no perderse nada en las curvas del río); 1.0 = mapas exactamente
# contiguos, sin solaparse.
FACTOR_SOLAPE = 1.4
DELTA_MAPA_MINIMO = 0.004   # piso de zoom (~450m) para que nunca quede demasiado cerrado
DELTA_MAPA_MAXIMO = 0.03    # techo de zoom (~3.3km) para que nunca quede demasiado abierto

# El modo "campo_flujo_datos" necesita una curva con más cuerpo (suficientes
# vértices reales de OSM) para que la simulación se vea orgánica — con el
# piso general (~450m) a veces el recuadro queda con muy poca geometría real
# adentro. Este piso más generoso aplica SOLO a ese modo.
DELTA_MINIMA_CAMPO_FLUJO = 0.011   # ~2.4km de ancho mínimo

# Estilo del mapa transmitido:
#   "campo_flujo_datos": el modo nuevo — un campo de fuerzas (partículas +
#       curvas derivadas del río real) modulado por datos ambientales en
#       tiempo real (sismo, clima, hidrología, actividad geomagnética).
#       Blanco y negro puro, pensado para impresión térmica. Necesita numpy.
#   "hidrografia_aislada": dibuja SOLO ríos/quebradas/cuerpos de agua (datos
#       vectoriales reales de OpenStreetMap vía Overpass), sobre fondo blanco,
#       sin calles/curvas de nivel/etiquetas. No necesita contextily/rasterio.
#   "topografico_contextily": el mapa base completo (relieve, calles, etc.)
#       usando OpenTopoMap vía contextily — el modo original. Requiere
#       contextily+rasterio instalados.
MODO_MAPA = "campo_flujo_datos"
COLOR_AGUA = "#1a5fb4"   # color de líneas/cuerpos de agua en la capa aislada (modo hidrografia_aislada)
GROSOR_RIO_PRINCIPAL = 3.0
GROSOR_AFLUENTE = 1.2

# Coordenadas usadas para las consultas de clima/sismo (independientes del
# punto exacto del río que se esté transmitiendo — el clima/sismo regional
# no cambia sensiblemente en la escala de metros entre puntos consecutivos).
LAT_REGION = 5.0689   # Manizales, aprox.
LON_REGION = -75.5174

OUTPUT_DIR = "sstv_output"
CACHE_RUTA = os.path.join(OUTPUT_DIR, "ruta_rio.json")
ESTADO_PATH = os.path.join(OUTPUT_DIR, "estado_transmision.json")

# Reproducción de audio: se usa 'paplay' (PulseAudio/PipeWire) o 'aplay' (ALSA
# directo) como respaldo, vía línea de comandos — más confiable que PortAudio
# en sistemas con PipeWire. Deja en None para usar el reproductor por defecto
# del sistema y el volumen normal.
REPRODUCTOR_AUDIO = None  # ej. "/usr/bin/paplay"; None = autodetectar
VOLUMEN = 1.0              # 1.0 = volumen normal, 0.5 = mitad, 1.5 = 150%, etc. (solo con paplay)

os.makedirs(OUTPUT_DIR, exist_ok=True)

# =====================================================================
# 1. OBTENER EL TRAZADO REAL DEL RÍO (OpenStreetMap / Overpass API)
# =====================================================================
def _distancia_m(p1, p2):
    """Distancia aproximada en metros entre dos puntos (lat, lon)."""
    lat1, lon1 = p1
    lat2, lon2 = p2
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


# Varios espejos de Overpass, por si alguno bloquea, limita tasa o está caído.
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]

# Overpass exige un User-Agent identificable; sin esto algunos servidores
# devuelven 406/403 aunque la consulta sea correcta.
HEADERS_OVERPASS = {
    "User-Agent": "RadioAtlas-RioChinchina/1.0 (instalacion artistica; contacto: cambiar-por-tu-email@ejemplo.com)",
    "Accept": "application/json",
}


def _descargar_segmentos_osm(nombre_rio, area_admin, intentos_rondas=3, espera_entre_rondas=30):
    """
    Consulta Overpass API (probando varios espejos, con varias rondas de
    reintento completas) y devuelve segmentos [(lat,lon), ...]. La saturación
    pública de Overpass suele ser temporal — varias rondas con espera entre
    ellas es más resistente que solo un intento por espejo.
    """
    query = f"""
    [out:json][timeout:90];
    area["name"="{area_admin}"]["admin_level"="4"]->.searchArea;
    (
      way["waterway"~"river|stream"]["name"~"{nombre_rio}", i](area.searchArea);
    );
    out geom;
    """

    ultimo_error = None
    data = None
    for ronda in range(1, intentos_rondas + 1):
        for endpoint in OVERPASS_ENDPOINTS:
            try:
                print(f"[Ruta] (ronda {ronda}/{intentos_rondas}) Probando servidor Overpass: {endpoint}")
                resp = requests.post(
                    endpoint,
                    data={"data": query},
                    headers=HEADERS_OVERPASS,
                    timeout=120,
                )
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as e:
                print(f"[Ruta] Falló {endpoint}: {e}")
                ultimo_error = e
                time.sleep(2)
        if data is not None:
            break
        if ronda < intentos_rondas:
            print(f"[Ruta] Los tres espejos fallaron en esta ronda. "
                  f"Esperando {espera_entre_rondas}s antes de reintentar...")
            time.sleep(espera_entre_rondas)

    if data is None:
        raise RuntimeError(
            f"No se pudo consultar ningún servidor Overpass tras {intentos_rondas} rondas. "
            f"Último error: {ultimo_error}"
        )

    segmentos = []
    for el in data.get("elements", []):
        if el.get("type") == "way" and "geometry" in el:
            coords = [(pt["lat"], pt["lon"]) for pt in el["geometry"]]
            if len(coords) >= 2:
                segmentos.append(coords)

    if not segmentos:
        raise RuntimeError(
            f"No se encontraron segmentos del río '{nombre_rio}' en '{area_admin}'. "
            "Revisa el nombre exacto en OpenStreetMap."
        )
    return segmentos


def _unir_segmentos(segmentos):
    """
    Une segmentos de vía (way) en una sola polilínea continua, encadenando
    por cercanía de extremos. No es perfecto con afluentes/ramificaciones,
    pero da un recorrido razonable para un río principal.
    """
    segmentos = [list(s) for s in segmentos]
    ruta = segmentos.pop(0)

    while segmentos:
        extremo_final = ruta[-1]
        extremo_inicial = ruta[0]
        mejor_idx, mejor_modo, mejor_dist = None, None, float("inf")

        for i, seg in enumerate(segmentos):
            candidatos = [
                ("fin_inicio", _distancia_m(extremo_final, seg[0])),
                ("fin_fin", _distancia_m(extremo_final, seg[-1])),
                ("inicio_inicio", _distancia_m(extremo_inicial, seg[0])),
                ("inicio_fin", _distancia_m(extremo_inicial, seg[-1])),
            ]
            for modo, d in candidatos:
                if d < mejor_dist:
                    mejor_dist, mejor_idx, mejor_modo = d, i, modo

        seg = segmentos.pop(mejor_idx)
        if mejor_modo == "fin_inicio":
            ruta.extend(seg)
        elif mejor_modo == "fin_fin":
            ruta.extend(reversed(seg))
        elif mejor_modo == "inicio_inicio":
            ruta = list(reversed(seg)) + ruta
        else:  # inicio_fin
            ruta = seg + ruta

    return ruta


def _muestrear_puntos(ruta, n_puntos):
    """Devuelve (puntos equiespaciados a lo largo de la ruta, largo_total_metros)."""
    distancias = [0.0]
    for i in range(1, len(ruta)):
        distancias.append(distancias[-1] + _distancia_m(ruta[i - 1], ruta[i]))
    largo_total = distancias[-1]

    if n_puntos <= 1:
        return [ruta[0]], largo_total

    puntos = []
    for k in range(n_puntos):
        objetivo = largo_total * k / (n_puntos - 1)
        idx = next((i for i, d in enumerate(distancias) if d >= objetivo), len(ruta) - 1)
        if idx == 0:
            puntos.append(ruta[0])
            continue
        d0, d1 = distancias[idx - 1], distancias[idx]
        frac = 0 if d1 == d0 else (objetivo - d0) / (d1 - d0)
        lat = ruta[idx - 1][0] + frac * (ruta[idx][0] - ruta[idx - 1][0])
        lon = ruta[idx - 1][1] + frac * (ruta[idx][1] - ruta[idx - 1][1])
        puntos.append((lat, lon))

    return puntos, largo_total


def _calcular_delta_automatico(largo_total_m, n_puntos):
    """
    Calcula el 'zoom' (medio-ancho en grados) para que el ancho de cada mapa
    sea aproximadamente igual a la distancia entre puntos consecutivos
    (multiplicada por FACTOR_SOLAPE), para que las capturas conecten
    visualmente entre sí en vez de repetirse o dejar huecos.
    """
    if n_puntos <= 1:
        return DELTA_MAPA_MINIMO
    espaciado_m = largo_total_m / (n_puntos - 1)
    METROS_POR_GRADO = 111320  # aproximación válida para la escala de este proyecto
    espaciado_grados = espaciado_m / METROS_POR_GRADO
    delta = (espaciado_grados * FACTOR_SOLAPE) / 2
    return max(DELTA_MAPA_MINIMO, min(DELTA_MAPA_MAXIMO, delta))


def obtener_ruta_rio():
    """Obtiene (con caché en disco) la polilínea completa del río en OSM."""
    if os.path.exists(CACHE_RUTA):
        with open(CACHE_RUTA) as f:
            cache = json.load(f)
        if cache.get("rio") == NOMBRE_RIO and cache.get("area") == AREA_ADMINISTRATIVA:
            print(f"[Ruta] Usando trazado en caché de {NOMBRE_RIO} ({len(cache['ruta'])} puntos).")
            return [tuple(p) for p in cache["ruta"]]

    print(f"[Ruta] Descargando trazado de '{NOMBRE_RIO}' en '{AREA_ADMINISTRATIVA}' desde OpenStreetMap...")
    segmentos = _descargar_segmentos_osm(NOMBRE_RIO, AREA_ADMINISTRATIVA)
    ruta = _unir_segmentos(segmentos)

    with open(CACHE_RUTA, "w") as f:
        json.dump({"rio": NOMBRE_RIO, "area": AREA_ADMINISTRATIVA, "ruta": ruta}, f)

    print(f"[Ruta] Trazado listo: {len(ruta)} vértices.")
    return ruta


# =====================================================================
# 2. ESTADO / PROGRESO DEL RECORRIDO
# =====================================================================
def cargar_o_crear_estado():
    if os.path.exists(ESTADO_PATH):
        with open(ESTADO_PATH) as f:
            estado = json.load(f)
        print(f"[Estado] Retomando recorrido: punto {estado['indice_actual']}/{len(estado['puntos'])}.")
        return estado

    ruta = obtener_ruta_rio()
    segundos_totales = DIAS_TOTALES * HORAS_POR_DIA * 3600
    n_puntos = max(2, segundos_totales // TIEMPO_CICLO_ESTIMADO_SEG)
    puntos, largo_total_m = _muestrear_puntos(ruta, n_puntos)
    delta_mapa = _calcular_delta_automatico(largo_total_m, n_puntos)

    print(f"[Ruta] Largo total del río: {largo_total_m/1000:.1f} km. "
          f"Espaciado entre puntos: {largo_total_m/max(1,(n_puntos-1)):.0f} m. "
          f"Zoom por mapa (delta): {delta_mapa:.4f}° (~{delta_mapa*111320*2/1000:.2f} km de ancho).")

    estado = {
        "rio": NOMBRE_RIO,
        "creado": datetime.now().isoformat(),
        "dias_totales": DIAS_TOTALES,
        "horas_por_dia": HORAS_POR_DIA,
        "puntos": [list(p) for p in puntos],
        "delta_mapa": delta_mapa,
        "indice_actual": 0,
    }
    guardar_estado(estado)
    print(f"[Estado] Nuevo recorrido creado: {len(puntos)} puntos "
          f"para cubrir el río en {DIAS_TOTALES} días x {HORAS_POR_DIA}h.")
    return estado


def guardar_estado(estado):
    with open(ESTADO_PATH, "w") as f:
        json.dump(estado, f, indent=2)


# =====================================================================
# 3. VENTANA HORARIA DIARIA
# =====================================================================
def dentro_de_ventana(ahora=None):
    ahora = ahora or datetime.now()
    return HORA_INICIO <= ahora.time() <= HORA_FIN


def esperar_hasta_ventana():
    while not dentro_de_ventana():
        ahora = datetime.now()
        print(f"[Espera] Fuera de la ventana horaria ({HORA_INICIO}-{HORA_FIN}). "
              f"Hora actual: {ahora.strftime('%H:%M:%S')}. Reintentando en 5 min...")
        time.sleep(300)


# =====================================================================
# 4. GENERACIÓN DEL MAPA / IMAGEN / AUDIO (igual que antes, parametrizado)
# =====================================================================
def _consultar_overpass_bbox(query):
    """Ejecuta una query Overpass probando los espejos disponibles (orden aleatorio,
    para no perder tiempo siempre en el mismo servidor si está caído/lento)."""
    endpoints = OVERPASS_ENDPOINTS.copy()
    random.shuffle(endpoints)

    ultimo_error = None
    for endpoint in endpoints:
        try:
            print(f"    (consultando {endpoint} ...)")
            resp = requests.post(
                endpoint, data={"data": query}, headers=HEADERS_OVERPASS,
                timeout=(10, 25),  # (timeout de conexión, timeout de lectura) en segundos
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"    (falló: {e})")
            ultimo_error = e
            time.sleep(1)
    raise RuntimeError(f"No se pudo consultar Overpass para el bbox: {ultimo_error}")


def _obtener_hidrografia_bbox(lat, lon, delta):
    """
    Pide a Overpass SOLO las líneas de agua (waterway) y cuerpos de agua
    (natural=water) dentro del recuadro alrededor de (lat, lon). Devuelve
    (lineas_principales, lineas_secundarias, poligonos) como listas de
    listas de (lat, lon). "Principal" = coincide por nombre con NOMBRE_RIO
    (el Chinchiná); todo lo demás (afluentes, quebradas sin nombre, etc.)
    es "secundario".
    """
    sur, norte = lat - delta, lat + delta
    oeste, este = lon - delta, lon + delta
    bbox = f"{sur},{oeste},{norte},{este}"

    query = f"""
    [out:json][timeout:60];
    (
      way["waterway"]({bbox});
      way["natural"="water"]({bbox});
      relation["natural"="water"]({bbox});
    );
    out geom;
    """
    data = _consultar_overpass_bbox(query)

    nombre_buscado = NOMBRE_RIO.strip().lower()
    lineas_principales, lineas_secundarias, poligonos = [], [], []
    for el in data.get("elements", []):
        if "geometry" not in el:
            continue
        coords = [(pt["lat"], pt["lon"]) for pt in el["geometry"]]
        if len(coords) < 2:
            continue
        tags = el.get("tags", {})
        if tags.get("natural") == "water":
            poligonos.append(coords)
            continue
        nombre_via = (tags.get("name") or "").strip().lower()
        if nombre_buscado in nombre_via:
            lineas_principales.append(coords)
        else:
            lineas_secundarias.append(coords)
    return lineas_principales, lineas_secundarias, poligonos


def generar_capa_hidrografica_aislada(lat, lon, delta):
    """
    Genera un mapa con SOLO ríos/quebradas/cuerpos de agua reales (OSM),
    sin ningún mapa base debajo — fondo blanco liso. El Chinchiná (por
    nombre) se dibuja como cauce principal, más grueso; todo lo demás
    queda como contexto secundario más delgado.
    """
    print(f"[1/4] Consultando hidrografía real (Overpass, zoom {delta:.4f}°)...")
    try:
        lineas_principales, lineas_secundarias, poligonos = _obtener_hidrografia_bbox(lat, lon, delta)

        if not lineas_principales and not lineas_secundarias and not poligonos:
            print("[1/4] ⚠️  Overpass no devolvió agua en este recuadro — se omite este ciclo.")
            return None
        if not lineas_principales:
            print(f"[1/4] ⚠️  '{NOMBRE_RIO}' no aparece con ese nombre en este recuadro "
                  f"(puede ser un tramo sin etiqueta en OSM) — se dibuja solo lo secundario.")

        fig, ax = plt.subplots(figsize=(6, 6))
        fig.patch.set_facecolor("white")
        ax.set_facecolor("white")
        ax.set_xlim(lon - delta, lon + delta)
        ax.set_ylim(lat - delta, lat + delta)
        ax.axis("off")
        ax.set_aspect(1.0 / max(0.1, math.cos(math.radians(lat))))

        for coords in poligonos:
            ys = [p[0] for p in coords]
            xs = [p[1] for p in coords]
            ax.fill(xs, ys, color=COLOR_AGUA, alpha=0.35, linewidth=0)

        # Secundarias primero (más tenues, de fondo)
        for coords in lineas_secundarias:
            ys = [p[0] for p in coords]
            xs = [p[1] for p in coords]
            ax.plot(xs, ys, color=COLOR_AGUA, linewidth=GROSOR_AFLUENTE, alpha=0.5,
                     solid_capstyle="round")

        # El Chinchiná encima, como cauce principal
        for coords in lineas_principales:
            ys = [p[0] for p in coords]
            xs = [p[1] for p in coords]
            ax.plot(xs, ys, color=COLOR_AGUA, linewidth=GROSOR_RIO_PRINCIPAL,
                     solid_capstyle="round")

        imagen_path = os.path.join(OUTPUT_DIR, "mapa_capturado.jpg")
        plt.savefig(imagen_path, bbox_inches="tight", pad_inches=0, dpi=150, facecolor="white")
        plt.close(fig)
        return imagen_path
    except Exception as e:
        print(f"Error al generar la capa hidrográfica: {e}")
        return None


def obtener_kp_geomagnetico():
    """
    Índice Kp planetario (actividad geomagnética, 0-9), vía NOAA SWPC.
    Afecta directamente la propagación de radio HF — el mismo medio que usa
    esta instalación para transmitir.
    """
    try:
        resp = requests.get(
            "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json",
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        ultima = data[-1]
        # El formato exacto de NOAA a veces es lista-de-listas (con cabecera
        # en data[0]) y a veces lista-de-diccionarios — se manejan ambos.
        if isinstance(ultima, dict):
            valor = ultima.get("kp") or ultima.get("Kp") or ultima.get("estimated_kp")
        else:
            valor = ultima[1]
        kp = float(valor)
        return max(0.0, min(9.0, kp))
    except Exception as e:
        print(f"[Datos] ⚠️  No se pudo obtener el índice Kp (NOAA): {type(e).__name__}: {e}. "
              f"Usando valor neutro.")
        return 2.0


def obtener_clima(lat, lon):
    """Clima actual (Open-Meteo, sin necesidad de API key)."""
    try:
        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat, "longitude": lon,
                "current": "temperature_2m,precipitation,wind_speed_10m,relative_humidity_2m",
                "timezone": "auto",
            },
            timeout=15,
        )
        resp.raise_for_status()
        actual = resp.json()["current"]
        return {
            "temperatura": actual.get("temperature_2m", 18.0),
            "precipitacion": actual.get("precipitation", 0.0),
            "viento": actual.get("wind_speed_10m", 0.0),
            "humedad": actual.get("relative_humidity_2m", 70.0),
        }
    except Exception as e:
        print(f"[Datos] ⚠️  No se pudo obtener el clima (Open-Meteo): {e}. Usando valores típicos.")
        return {"temperatura": 18.0, "precipitacion": 0.0, "viento": 5.0, "humedad": 75.0}


def obtener_sismo_reciente(lat, lon, radio_grados=1.5):
    """
    Magnitud del sismo más reciente cerca de la región (USGS — catálogo global
    confiable y bien documentado; el SGC colombiano no tiene un endpoint JSON
    simple confirmado al momento de escribir esto, así que se usa USGS como
    respaldo robusto — cualquier sismo regional relevante también aparece ahí).
    """
    try:
        resp = requests.get(
            "https://earthquake.usgs.gov/fdsnws/event/1/query",
            params={
                "format": "geojson",
                "starttime": (datetime.now(timezone.utc).date().isoformat()),
                "minlatitude": lat - radio_grados, "maxlatitude": lat + radio_grados,
                "minlongitude": lon - radio_grados, "maxlongitude": lon + radio_grados,
                "orderby": "time",
            },
            timeout=15,
        )
        resp.raise_for_status()
        features = resp.json().get("features", [])
        if not features:
            return 0.0
        return max(0.0, float(features[0]["properties"]["mag"] or 0.0))
    except Exception as e:
        print(f"[Datos] ⚠️  No se pudo obtener actividad sísmica (USGS): {e}. Usando valor neutro.")
        return 0.0


def obtener_hidrologia_proxy(clima):
    """
    Proxy de caudal/hidrología: NO hay (todavía) una estación IDEAM específica
    del Chinchiná confirmada con API simple, así que se aproxima con la
    precipitación reciente como indicador indirecto de caudal. Reemplazar
    esta función si se identifica una estación IDEAM real vía datos.gov.co.
    """
    precip = clima.get("precipitacion", 0.0)
    return max(0.0, min(100.0, precip * 12))


def obtener_datos_ambientales(lat, lon):
    """Recolecta los cuatro datos y los normaliza a escala 0-100 para el campo de flujo."""
    print("[Datos] Consultando fuentes en tiempo real (NOAA, Open-Meteo, USGS)...")
    kp = obtener_kp_geomagnetico()
    clima = obtener_clima(lat, lon)
    magnitud = obtener_sismo_reciente(lat, lon)
    hidro = obtener_hidrologia_proxy(clima)

    datos = {
        "sismo": max(0.0, min(100.0, magnitud / 6.0 * 100)),
        "clima": max(0.0, min(100.0, clima["temperatura"] / 30.0 * 100)),
        "hidro": hidro,
        "kp": max(0.0, min(100.0, kp / 9.0 * 100)),
        "_crudos": {"magnitud": magnitud, "temperatura": clima["temperatura"],
                    "precipitacion": clima["precipitacion"], "kp": kp},
    }
    print(f"[Datos] sismo=M{magnitud:.1f}  clima={clima['temperatura']:.1f}°C  "
          f"precipitación={clima['precipitacion']:.1f}mm  Kp={kp:.1f}")
    return datos


# =====================================================================
# 3b. CAMPO DE FLUJO MODULADO POR DATOS (modo "campo_flujo_datos")
# =====================================================================
def _hash2(x, y):
    n = np.sin(x*127.1 + y*311.7) * 43758.5453
    return n - np.floor(n)


def _noise2d(x, y):
    xi, yi = np.floor(x), np.floor(y)
    xf, yf = x - xi, y - yi
    u = xf*xf*(3 - 2*xf)
    v = yf*yf*(3 - 2*yf)
    a, b = _hash2(xi, yi), _hash2(xi+1, yi)
    c, d = _hash2(xi, yi+1), _hash2(xi+1, yi+1)
    return a + (b-a)*u + (c-a)*v + (a-b-c+d)*u*v


def _curl(x, y, freq, t):
    eps = 1.5
    n1 = _noise2d(x*freq, (y+eps)*freq + t)
    n2 = _noise2d(x*freq, (y-eps)*freq + t)
    n3 = _noise2d((x+eps)*freq, y*freq + t)
    n4 = _noise2d((x-eps)*freq, y*freq + t)
    return (n1 - n2) / (2*eps), -(n3 - n4) / (2*eps)


def _proyectar_a_lienzo(lineas, lat, lon, delta, ancho=640, alto=380):
    """Convierte coordenadas (lat, lon) al espacio de lienzo local (píxeles abstractos)."""
    resultado = []
    for coords in lineas:
        pts = []
        for la, lo in coords:
            x = (lo - (lon - delta)) / (2*delta) * ancho
            y = (lat + delta - la) / (2*delta) * alto
            pts.append((x, y))
        resultado.append(pts)
    return resultado


def _resamplear_polilinea(pts, n):
    """Reduce/aumenta una polilínea a n puntos equiespaciados por longitud real."""
    if len(pts) < 2:
        return pts * n if pts else [(0, 0)] * n
    arr = np.array(pts)
    d = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(arr, axis=0).T))])
    if d[-1] == 0:
        return [tuple(arr[0])] * n
    objetivo = np.linspace(0, d[-1], n)
    xs = np.interp(objetivo, d, arr[:, 0])
    ys = np.interp(objetivo, d, arr[:, 1])
    return list(zip(xs, ys))


def _simular_campo_flujo(path_px, datos, ancho=640, alto=380,
                          n_particulas=350, n_pasos=160, semilla=42):
    """Devuelve una lista de segmentos [(x0,y0),(x1,y1)] listos para LineCollection."""
    rng = np.random.default_rng(semilla)
    n = len(path_px)
    path_arr = np.array(path_px)
    tangentes = np.zeros_like(path_arr)
    tangentes[1:-1] = path_arr[2:] - path_arr[:-2]
    tangentes[0] = path_arr[1] - path_arr[0]
    tangentes[-1] = path_arr[-1] - path_arr[-2]
    normas = np.hypot(tangentes[:, 0], tangentes[:, 1])
    normas[normas == 0] = 1
    tangentes = tangentes / normas[:, None]

    sismo, clima, hidro, kp = datos["sismo"], datos["clima"], datos["hidro"], datos["kp"]
    spring_k = 0.02
    hidro_speed = 0.15 + (hidro/100)*1.6
    clima_amp = 0.7 + (clima/100)*3.2
    clima_freq = 0.012
    sismo_amp = (sismo/100)*2.6
    kp_jitter = 0.6 + (kp/100)**1.6 * 3.2

    # Avance del ancla por paso: se calcula para SIEMPRE recorrer el trazado
    # completo dentro de n_pasos, sin importar la hidrología del día — el dato
    # real solo modula la velocidad relativa (0.5x a 2x), no si llega a cubrir
    # todo el río o no. Sin este piso, un día sin lluvia dejaba a las
    # partículas pintando una y otra vez el mismo tramo en vez de recorrerlo.
    avance_base = n / n_pasos
    avance_mult = 0.5 + (hidro/100) * 1.5

    n_fuentes = 1 + int(sismo/100*5)
    fuente_idx = rng.integers(0, n, size=n_fuentes)
    fuentes = path_arr[fuente_idx]
    fuentes_fase = rng.random(n_fuentes) * 10

    anchors = rng.integers(0, n, size=n_particulas).astype(float)
    xs = path_arr[anchors.astype(int), 0] + rng.uniform(-3, 3, n_particulas)
    ys = path_arr[anchors.astype(int), 1] + rng.uniform(-3, 3, n_particulas)

    segmentos = []
    for paso in range(n_pasos):
        t = paso * 0.02
        ai = np.clip(anchors.astype(int), 0, n-1)
        fx = (path_arr[ai, 0] - xs) * spring_k + tangentes[ai, 0] * hidro_speed
        fy = (path_arr[ai, 1] - ys) * spring_k + tangentes[ai, 1] * hidro_speed

        dx = xs[:, None] - fuentes[None, :, 0]
        dy = ys[:, None] - fuentes[None, :, 1]
        dist2 = dx*dx + dy*dy + 40
        pulso = 0.5 + 0.5*np.sin(t*3 + fuentes_fase)[None, :]
        mag = sismo_amp * 260 / dist2 * pulso
        radial_x = np.sum(dx/np.sqrt(dist2) * mag, axis=1)
        radial_y = np.sum(dy/np.sqrt(dist2) * mag, axis=1)
        burst = np.sum(mag, axis=1)

        cx, cy = _curl(xs, ys, clima_freq, t*0.15)
        clima_mult = 1 + np.minimum(burst*0.4, 3)
        fx += cx * clima_amp * clima_mult + radial_x
        fy += cy * clima_amp * clima_mult + radial_y

        angulo = _hash2(xs*0.7 + t*40, ys*0.7) * 2*np.pi
        fx += np.cos(angulo) * kp_jitter * rng.uniform(0.4, 1.0, n_particulas)
        fy += np.sin(angulo) * kp_jitter * rng.uniform(0.4, 1.0, n_particulas)

        nx, ny = xs + fx, ys + fy
        segmentos.extend(zip(zip(xs, ys), zip(nx, ny)))
        xs, ys = nx, ny
        anchors = np.minimum(n-1, anchors + avance_base * avance_mult)

        fuera = (anchors >= n-1) | (xs < -20) | (xs > ancho+20) | (ys < -20) | (ys > alto+20)
        if np.any(fuera):
            idxs = np.where(fuera)[0]
            nuevas_a = rng.integers(0, n, size=len(idxs))
            for j, i in enumerate(idxs):
                a = nuevas_a[j]
                xs[i] = path_arr[a, 0] + rng.uniform(-3, 3)
                ys[i] = path_arr[a, 1] + rng.uniform(-3, 3)
                anchors[i] = a

    return segmentos, path_arr, tangentes


def _simular_voz_derivada(path_arr, tangentes, datos, config, ancho, alto, n_pasos=170):
    """Una curva que arranca en el río y se deja llevar por el mismo campo con su propio 'temperamento'."""
    n = len(path_arr)
    pts = path_arr.copy().astype(float)
    sismo, clima, hidro, kp = datos["sismo"], datos["clima"], datos["hidro"], datos["kp"]
    for paso in range(n_pasos):
        t = paso * 0.02
        fx = (path_arr[:, 0] - pts[:, 0]) * config["spring_k"]
        fy = (path_arr[:, 1] - pts[:, 1]) * config["spring_k"]
        fx += tangentes[:, 0] * (0.15 + hidro/100*1.6) * config["hidro_w"]
        fy += tangentes[:, 1] * (0.15 + hidro/100*1.6) * config["hidro_w"]
        cx, cy = _curl(pts[:, 0] + config["seed_off"], pts[:, 1] + config["seed_off"], 0.012, t*0.15)
        # Piso mínimo (0.7): incluso en un día ambientalmente calmado (clima
        # bajo), la voz sigue teniendo textura propia — nunca queda plana.
        fx += cx * (0.7 + clima/100*3.2) * config["clima_w"]
        fy += cy * (0.7 + clima/100*3.2) * config["clima_w"]
        angulo = _hash2(pts[:, 0]*0.7 + t*40 + config["seed_off"], pts[:, 1]*0.7) * 2*np.pi
        # Piso mínimo (0.6) también en el temblor geomagnético — mismo motivo.
        kp_jitter = (0.6 + (kp/100)**1.6 * 3.2) * config["kp_w"]
        fx += np.cos(angulo) * kp_jitter * 0.6
        fy += np.sin(angulo) * kp_jitter * 0.6
        pts[:, 0] += fx
        pts[:, 1] += fy
    return pts


def generar_campo_flujo_datos(lat, lon, delta):
    """
    Genera el mapa en modo 'campo_flujo_datos': partículas + voces derivadas
    del trazado real del río, moduladas por sismo/clima/hidrología/Kp reales.
    Blanco y negro puro, pensado para impresión térmica.
    """
    delta = max(delta, DELTA_MINIMA_CAMPO_FLUJO)
    print(f"[1/4] Consultando hidrografía real e datos ambientales (zoom {delta:.4f}°)...")
    try:
        lineas_principales, lineas_secundarias, _ = _obtener_hidrografia_bbox(lat, lon, delta)
        lineas_geo = lineas_principales if lineas_principales else lineas_secundarias
        if not lineas_geo:
            print("[1/4] ⚠️  Sin hidrografía en este recuadro — se omite este ciclo.")
            return None

        # El río puede llegar partido en varios segmentos (ways) separados
        # dentro del recuadro — se unen en una sola polilínea continua en vez
        # de quedarse solo con el fragmento más largo (que dejaba huecos).
        rio_unido = _unir_segmentos(lineas_geo) if len(lineas_geo) > 1 else lineas_geo[0]

        ANCHO, ALTO = 640, 380
        lineas_px = _proyectar_a_lienzo([rio_unido], lat, lon, delta, ANCHO, ALTO)
        path_px = _resamplear_polilinea(lineas_px[0], 130)

        datos = obtener_datos_ambientales(LAT_REGION, LON_REGION)

        segmentos, path_arr, tangentes = _simular_campo_flujo(path_px, datos, ANCHO, ALTO)

        voces_cfg = [
            {"spring_k": 0.08, "hidro_w": 1.3, "clima_w": 0.5, "kp_w": 0.3, "seed_off": 0,
             "dash": None, "width": 1.8},
            {"spring_k": 0.018, "hidro_w": 0.3, "clima_w": 0.7, "kp_w": 1.5, "seed_off": 50,
             "dash": (6, 3), "width": 1.1},
            {"spring_k": 0.035, "hidro_w": 0.6, "clima_w": 1.7, "kp_w": 0.6, "seed_off": 120,
             "dash": (1, 3), "width": 1.3},
        ]

        fig, ax = plt.subplots(figsize=(6, 6))
        ax.set_facecolor("white")
        fig.patch.set_facecolor("white")
        ax.set_xlim(0, ANCHO)
        ax.set_ylim(ALTO, 0)
        ax.axis("off")

        lc = LineCollection(segmentos, colors="black", linewidths=0.7, alpha=0.16)
        ax.add_collection(lc)

        voz_principal = None
        for i, cfg in enumerate(voces_cfg):
            pts = _simular_voz_derivada(path_arr, tangentes, datos, cfg, ANCHO, ALTO)
            xs, ys = pts[:, 0], pts[:, 1]
            if cfg["dash"] is None:
                ax.plot(xs, ys, color="black", linewidth=cfg["width"], solid_capstyle="round")
                voz_principal = pts
            else:
                ax.plot(xs, ys, color="black", linewidth=cfg["width"], dashes=cfg["dash"])

        crudos = datos["_crudos"]
        etiquetas = [
            f"M{crudos['magnitud']:.1f}",
            f"{crudos['temperatura']:.1f}°",
            f"{crudos['precipitacion']:.1f}mm",
            f"Kp{crudos['kp']:.1f}",
        ]
        if voz_principal is not None:
            paso = max(1, len(voz_principal) // (len(etiquetas)+1))
            for k, etiqueta in enumerate(etiquetas):
                i = min(len(voz_principal)-2, paso*(k+1))
                p0, p1 = voz_principal[max(0,i-1)], voz_principal[min(len(voz_principal)-1,i+1)]
                ang = math.atan2(p1[1]-p0[1], p1[0]-p0[0])
                if ang > math.pi/2 or ang < -math.pi/2:
                    ang += math.pi
                ax.text(voz_principal[i][0], voz_principal[i][1], etiqueta,
                         fontsize=8, rotation=math.degrees(ang), rotation_mode="anchor",
                         ha="center", va="center", color="black", alpha=0.75)

        imagen_path = os.path.join(OUTPUT_DIR, "mapa_capturado.jpg")
        plt.savefig(imagen_path, bbox_inches="tight", pad_inches=0, dpi=150, facecolor="white")
        plt.close(fig)
        return imagen_path
    except Exception as e:
        print(f"Error al generar el campo de flujo: {e}")
        return None


def descargar_mapa_hidrico_real(lat, lon, delta):
    print(f"[1/4] Generando mapa topográfico/hídrico (zoom {delta:.4f}°)...")
    try:
        import contextily as cx  # import perezoso: solo se necesita en este modo

        fig, ax = plt.subplots(figsize=(6, 6))
        ax.set_xlim(lon - delta, lon + delta)
        ax.set_ylim(lat - delta, lat + delta)
        ax.axis('off')

        cx.add_basemap(ax, crs="EPSG:4326", source=cx.providers.OpenTopoMap)

        imagen_path = os.path.join(OUTPUT_DIR, "mapa_capturado.jpg")
        plt.savefig(imagen_path, bbox_inches='tight', pad_inches=0, dpi=150)
        plt.close(fig)
        return imagen_path
    except Exception as e:
        print(f"Error al generar el mapa topográfico: {e}")
        return None


def procesar_imagen_para_sstv(imagen_path):
    print("[2/4] Adaptando dimensiones a 320x256...")
    try:
        img = Image.open(imagen_path)
        img_redimensionada = img.resize((320, 256), Image.Resampling.LANCZOS)
        proc_path = os.path.join(OUTPUT_DIR, "mapa_procesado.png")
        img_redimensionada.save(proc_path)
        return proc_path
    except Exception as e:
        print(f"Error al procesar la imagen: {e}")
        return None


def generar_audio_sstv(imagen_procesada_path):
    print("[3/4] Codificando mapa en frecuencias SSTV (Martin M1)...")
    try:
        img = Image.open(imagen_procesada_path)
        sstv = MartinM1(img, 44100, 16)
        audio_path = os.path.join(OUTPUT_DIR, "mapa_sonoro.wav")
        sstv.write_wav(audio_path)
        return audio_path
    except Exception as e:
        print(f"Error en codificación SSTV: {e}")
        return None


def reproducir_audio_sstv(archivo_audio):
    print("[4/4] 🔊 TRANSMITIENDO...")
    try:
        # Diagnóstico: si el máximo de la señal es ~0, el wav está en silencio
        # (problema de codificación), no de reproducción.
        frecuencia, datos = wavfile.read(archivo_audio)
        pico = float(abs(datos).max())
        print(f"[Audio] Amplitud pico del WAV: {pico:.0f} "
              f"({'⚠️ parece silencio, revisa la generación del audio' if pico < 100 else 'OK'})")

        reproductor = REPRODUCTOR_AUDIO or shutil.which("paplay") or shutil.which("aplay")
        if reproductor is None:
            raise RuntimeError(
                "No se encontró 'paplay' ni 'aplay'. Instala con: "
                "sudo apt install pulseaudio-utils alsa-utils"
            )

        comando = [reproductor]
        if os.path.basename(reproductor).startswith("paplay") and VOLUMEN != 1.0:
            comando.append(f"--volume={int(min(max(VOLUMEN, 0), 2) * 65536)}")
        comando.append(archivo_audio)

        print(f"[Audio] Reproduciendo con: {' '.join(comando)}")
        subprocess.run(comando, check=True)
        print("Transmisión finalizada.")
    except Exception as e:
        print(f"Error en la reproducción de audio: {e}")


# =====================================================================
# MODO DE PRUEBA — un solo ciclo inmediato, sin tocar estado ni ventana
# =====================================================================
def ejecutar_ciclo(lat, lon, delta, etiqueta=""):
    """Genera mapa -> imagen -> audio -> reproduce, para un punto dado."""
    print(f"\n--- {etiqueta} ({lat:.5f}, {lon:.5f}) ---")

    if MODO_MAPA == "campo_flujo_datos":
        archivo_imagen = generar_campo_flujo_datos(lat, lon, delta)
    elif MODO_MAPA == "hidrografia_aislada":
        archivo_imagen = generar_capa_hidrografica_aislada(lat, lon, delta)
    else:
        archivo_imagen = descargar_mapa_hidrico_real(lat, lon, delta)

    if not archivo_imagen:
        return False
    imagen_lista = procesar_imagen_para_sstv(archivo_imagen)
    if not imagen_lista:
        return False
    archivo_audio = generar_audio_sstv(imagen_lista)
    if not archivo_audio:
        return False
    reproducir_audio_sstv(archivo_audio)
    return True


def correr_prueba_rapida():
    """
    Corre un único ciclo completo de inmediato: descarga/usa el trazado del
    río, toma el PRIMER punto, genera el mapa (con el mismo zoom automático
    que usaría el recorrido real, calculado a partir de la configuración
    actual de días/horas) y transmite. Ignora la ventana horaria y NO
    modifica estado_transmision.json — pensado para validar la instalación
    (audio, mapas, permisos) sin afectar el progreso real.
    """
    print("=== MODO PRUEBA: un solo ciclo inmediato, sin afectar el progreso guardado ===")
    ruta = obtener_ruta_rio()
    segundos_totales = DIAS_TOTALES * HORAS_POR_DIA * 3600
    n_puntos = max(2, segundos_totales // TIEMPO_CICLO_ESTIMADO_SEG)
    _, largo_total_m = _muestrear_puntos(ruta, n_puntos)
    delta = _calcular_delta_automatico(largo_total_m, n_puntos)
    print(f"[Prueba] Con la configuración actual ({DIAS_TOTALES} días x {HORAS_POR_DIA}h), "
          f"el recorrido tendría {n_puntos} puntos y cada mapa usaría zoom {delta:.4f}° "
          f"(~{delta*111320*2/1000:.2f} km de ancho).")

    lat, lon = ruta[0]
    ok = ejecutar_ciclo(lat, lon, delta, etiqueta="PRUEBA")
    if ok:
        print("\n✅ Ciclo de prueba completado. Si escuchaste el audio y viste el mapa, "
              "la instalación está lista para el recorrido real (corre sin --prueba).")
    else:
        print("\n❌ El ciclo de prueba falló en algún paso — revisa los mensajes de error arriba.")


def correr_modo_diagnostico(n_frames, incluir_audio=False):
    """
    Genera n_frames mapas CONSECUTIVOS del recorrido (con el mismo zoom
    automático que usaría la instalación real) y los guarda numerados en
    diagnostico_output/, para poder revisarlos uno al lado del otro y
    confirmar que cada uno conecta visualmente con el siguiente. Por
    defecto NO genera audio (mucho más rápido) — usa incluir_audio=True
    si además quieres escuchar cada segmento.

    No toca estado_transmision.json ni depende de tener parlante conectado
    (si incluir_audio=False), así que es seguro correrlo en cualquier
    computador, no solo en la Raspberry Pi.
    """
    carpeta_diag = "diagnostico_output"
    os.makedirs(carpeta_diag, exist_ok=True)

    print(f"=== MODO DIAGNÓSTICO: generando {n_frames} mapas consecutivos ===")
    ruta = obtener_ruta_rio()
    segundos_totales = DIAS_TOTALES * HORAS_POR_DIA * 3600
    n_puntos_totales = max(2, segundos_totales // TIEMPO_CICLO_ESTIMADO_SEG)
    puntos, largo_total_m = _muestrear_puntos(ruta, n_puntos_totales)
    delta = _calcular_delta_automatico(largo_total_m, n_puntos_totales)

    print(f"[Diagnóstico] Recorrido completo tendría {n_puntos_totales} puntos. "
          f"Zoom por mapa: {delta:.4f}° (~{delta*111320*2/1000:.2f} km de ancho). "
          f"Generando los primeros {n_frames}...\n")

    n_frames = min(n_frames, len(puntos))
    exitosos = 0
    for i in range(n_frames):
        lat, lon = puntos[i]
        print(f"--- Frame {i + 1}/{n_frames} ({lat:.5f}, {lon:.5f}) ---")

        if MODO_MAPA == "campo_flujo_datos":
            archivo_imagen = generar_campo_flujo_datos(lat, lon, delta)
        elif MODO_MAPA == "hidrografia_aislada":
            archivo_imagen = generar_capa_hidrografica_aislada(lat, lon, delta)
        else:
            archivo_imagen = descargar_mapa_hidrico_real(lat, lon, delta)

        if not archivo_imagen:
            print(f"  ⚠️  Frame {i + 1} falló, se omite.\n")
            continue

        destino = os.path.join(carpeta_diag, f"mapa_{i + 1:04d}.jpg")
        shutil.copy(archivo_imagen, destino)
        print(f"  Guardado: {destino}")
        exitosos += 1

        if incluir_audio:
            imagen_lista = procesar_imagen_para_sstv(archivo_imagen)
            if imagen_lista:
                archivo_audio = generar_audio_sstv(imagen_lista)
                if archivo_audio:
                    destino_audio = os.path.join(carpeta_diag, f"audio_{i + 1:04d}.wav")
                    shutil.copy(archivo_audio, destino_audio)
                    reproducir_audio_sstv(archivo_audio)

        print()
        time.sleep(1)  # ser considerado con la API de Overpass entre consultas seguidas

    print(f"✅ Diagnóstico completo: {exitosos}/{n_frames} mapas generados en '{carpeta_diag}/'.")
    print("Ábrelos en orden (mapa_0001.jpg, mapa_0002.jpg, ...) para confirmar la continuidad visual.")


# =====================================================================
# BUCLE PRINCIPAL
# =====================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Transmisor SSTV del recorrido de un río."
    )
    parser.add_argument(
        "--prueba", action="store_true",
        help="Corre un solo ciclo de inmediato (ignora ventana horaria, no toca el progreso guardado)."
    )
    parser.add_argument(
        "--diagnostico", type=int, metavar="N", default=None,
        help="Genera N mapas consecutivos numerados en diagnostico_output/ (sin audio por defecto) "
             "para revisar la continuidad visual del recorrido, sin tocar el progreso guardado."
    )
    parser.add_argument(
        "--diagnostico-audio", action="store_true",
        help="Usado junto con --diagnostico: además genera y reproduce el audio de cada frame."
    )
    args = parser.parse_args()

    if args.diagnostico is not None:
        correr_modo_diagnostico(args.diagnostico, incluir_audio=args.diagnostico_audio)
        raise SystemExit(0)

    if args.prueba:
        correr_prueba_rapida()
        raise SystemExit(0)

    print("=== TRANSMISOR SSTV — RECORRIDO DEL RÍO", NOMBRE_RIO.upper(), "===")

    estado = cargar_o_crear_estado()
    puntos = estado["puntos"]
    delta_mapa = estado.get("delta_mapa", DELTA_MAPA_MINIMO)

    try:
        while estado["indice_actual"] < len(puntos):
            esperar_hasta_ventana()

            idx = estado["indice_actual"]
            lat, lon = puntos[idx]
            ejecutar_ciclo(lat, lon, delta_mapa, etiqueta=f"PUNTO {idx + 1}/{len(puntos)}")

            estado["indice_actual"] += 1
            guardar_estado(estado)

            print(f"Esperando {SILENCIO_ENTRE_CICLOS_SEG} segundos de silencio...")
            time.sleep(SILENCIO_ENTRE_CICLOS_SEG)

        print("\n🌊 Recorrido completo del río transmitido. Fin de la instalación.")

    except KeyboardInterrupt:
        print("\nInstalación artística detenida manualmente. El progreso quedó guardado.")
