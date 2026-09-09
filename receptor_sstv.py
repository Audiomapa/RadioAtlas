#!/usr/bin/env python3
"""
=====================================================================
RECEPTOR SSTV — DECODIFICADOR + IMPRESIÓN (Raspberry Pi)
=====================================================================
Escucha el micrófono continuamente, detecta una transmisión SSTV en
formato Martin M1, la decodifica a imagen y la envía a imprimir en una
impresora térmica (rollo continuo, ESC/POS) vía USB.

IMPORTANTE — recomendación práctica:
La detección de tono y el decodificado son sensibles a ruido ambiente.
Si es posible, conecta la salida de audio del transmisor a la entrada
de audio (línea/mic) del receptor con un CABLE en vez de usar
parlante+micrófono al aire. Esto elimina casi todos los problemas de
ruido/eco de la sala y hace el decodificado mucho más confiable.

Requisitos: pip install -r requirements_receptor.txt
Además necesitas permisos para acceder al dispositivo USB de la
impresora (en Linux, agrega tu usuario al grupo correspondiente o
crea una regla udev — ver README).
=====================================================================
"""

import os
import json
import time
from datetime import datetime

import numpy as np
from scipy.signal import hilbert
import sounddevice as sd
from PIL import Image

# =====================================================================
# CONFIGURACIÓN
# =====================================================================
FS = 44100                     # frecuencia de muestreo (debe coincidir con el transmisor)
ANCHO_IMG = 320
ALTO_IMG = 256

# --- Timing Martin M1 (estándar) ---
SYNC_PULSO_SEG = 0.004862
SYNC_PORCHE_SEG = 0.000572
DURACION_CANAL_SEG = 0.146432
SEPARADOR_SEG = 0.000572
ORDEN_CANALES = ("G", "B", "R")  # Martin M1 transmite en orden Verde, Azul, Rojo

DURACION_LINEA_SEG = SYNC_PULSO_SEG + SYNC_PORCHE_SEG + 3 * (DURACION_CANAL_SEG + SEPARADOR_SEG)
DURACION_IMAGEN_SEG = DURACION_LINEA_SEG * ALTO_IMG  # ≈ 114 s

FREQ_SYNC = 1200
FREQ_NEGRO = 1500
FREQ_BLANCO = 2300

UMBRAL_DISPARO = 0.000021
                          # Calibrar SIEMPRE contra condiciones reales: deja correr
                          # /tmp/diagnostico_completo.py (o el modo verbose de este
                          # script) durante una transmisión completa con el volumen
                          # real de exhibición, y usa ~60-70% del "máx.visto" que
                          # reporte. Con un mic condensador + phantom power a nivel
                          # de sala razonable, valores típicos rondan 0.00001-0.001

UMBRAL_PUREZA = 0.082
                           # el espectro) tiene que ser el sonido para contar como sync
                           # real y no como ruido/voz ambiente. Un tono puro perfecto da
                           # ~0.5 con la fórmula usada acá; 0.08 deja margen para eco/
                           # distorsión de sala sin dejar pasar voces.

UMBRAL_CORRELACION = 0.35  # umbral mínimo de correlación normalizada con la plantilla
                            # del pulso de sync real (0-1). Exige que el bloque no solo
                            # tenga energía en 1200Hz sino que su forma se parezca a un
                            # tono puro de exactamente esa duración. Sube para más
                            # selectividad (menos falsos positivos); baja si deja de
                            # disparar con señal real débil.
MARGEN_BUSQUEDA_SEG = 0.010  # cuánto se busca alrededor de cada línea para corregir "deriva"

OUTPUT_DIR = "sstv_recibido"
LOG_PATH = os.path.join(OUTPUT_DIR, "log_recepcion.json")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# --- Impresora térmica ESC/POS ---
# TIPO_IMPRESORA: "usb" | "bluetooth_serial" | "network"
#   - "usb": impresora conectada por cable USB (vendor/product ID vía `lsusb`)
#   - "bluetooth_serial": impresora Bluetooth que se comporta como puerto serie
#     (SPP) — la gran mayoría de las térmicas de recibo Bluetooth baratas son así.
#     Requiere emparejar y "bindear" antes (ver README).
#   - "network": impresora con Wifi/Ethernet, se le habla por IP.
TIPO_IMPRESORA = "usb"

# --- usados si TIPO_IMPRESORA == "usb" ---
IMPRESORA_VENDOR_ID = 0x0483
IMPRESORA_PRODUCT_ID = 0x5720
# Algunas impresoras usan endpoints distintos a los que python-escpos asume
# por defecto (0x82 IN / 0x01 OUT), lo que da "Invalid endpoint address".
# Si te pasa eso, revisa los reales con:
#   lsusb -v -d VENDOR:PRODUCT | grep -A5 Endpoint
# y ponlos aquí. Deja en None para usar el comportamiento por defecto de la librería.
IMPRESORA_IN_EP = 0x81
IMPRESORA_OUT_EP = 0x03

# --- usados si TIPO_IMPRESORA == "bluetooth_serial" ---
IMPRESORA_MAC = "XX:XX:XX:XX:XX:XX"       # dirección MAC de la impresora (bluetoothctl)
IMPRESORA_RFCOMM_DEVICE = "/dev/rfcomm0"  # dispositivo serie creado al hacer el bind
IMPRESORA_BAUDRATE = 9600                 # muchas usan 9600; algunas 19200/115200 — ver manual

# --- usado si TIPO_IMPRESORA == "network" ---
IMPRESORA_IP = "192.168.1.100"

# Ancho de impresión en puntos (dots), según el papel de tu impresora térmica:
#   58mm de ancho ≈ 384 dots a 203dpi (el estándar de estas impresoras)
#   80mm de ancho ≈ 576 dots a 203dpi
# Revisa el manual de tu impresora si no estás seguro.
IMPRESORA_ANCHO_DOTS = 576

# Umbral de binarización antes de imprimir (0-255). La imagen decodificada
# por SSTV acústico tiene artefactos de color y ruido — convertirla a puro
# blanco/negro con este umbral elimina los artefactos y hace el texto legible.
# Valores más bajos (ej. 100): más negro → texto más grueso/visible pero
# fondo más sucio. Valores más altos (ej. 180): más blanco → texto más limpio
# pero puede perder trazos finos. 128 es el punto medio neutro.
UMBRAL_BINARIZACION = 128

# Si es False, NO corta el papel entre una impresión y la siguiente — las
# imágenes quedan una tira continua sin cortes ni espacios en blanco entre
# medio, formando el recorrido completo del río en un solo rollo.
CORTAR_ENTRE_SEGMENTOS = False

# Si es True, imprime "Segmento X — fecha/hora" antes de cada imagen. Déjalo
# en False para que la tira sea solo las imágenes, sin texto interrumpiendo.
IMPRIMIR_ETIQUETA_TEXTO = False

IMPRIMIR_AUTOMATICAMENTE = True

# --- Telemetría ---
DISPOSITIVO_ENTRADA = 1
MOSTRAR_NIVEL_AUDIO = True      # imprime el nivel de audio en vivo mientras escucha
INTERVALO_TELEMETRIA_SEG = 1.0  # cada cuánto imprimir el nivel de audio / progreso de captura

# Algunas interfaces de audio (ej. grabadoras profesionales tipo Zoom F3) no
# aceptan mono — solo graban en estéreo fijo. Si tu dispositivo de entrada da
# error de "formato/canales no disponible", cambia esto a 2; el script mezcla
# los canales a mono automáticamente antes de procesar, así el resto del
# decodificador no necesita cambios.
CANALES_ENTRADA = 2


def _marca_tiempo():
    return datetime.now().strftime("%H:%M:%S")


def log_etapa(etapa, mensaje):
    print(f"[{_marca_tiempo()}] [{etapa}] {mensaje}")


# =====================================================================
# 1. DETECCIÓN DE TONO (Goertzel)
# =====================================================================
def _filtro_pasabanda(muestras, freq_centro, ancho_hz, fs):
    """
    Filtro pasa-banda IIR (Butterworth orden 2) centrado en freq_centro.
    Elimina casi toda la energía fuera de [freq_centro-ancho/2 .. freq_centro+ancho/2]
    antes de que llegue al detector — la voz tiene energía en todo el espectro,
    pero un tono puro de 1200Hz concentra casi todo dentro de ±100Hz. Aplicar
    este filtro primero hace que la señal SSTV "brille" relativamente mucho más
    que cualquier ruido de sala de banda ancha, aunque ambos lleguen al mismo nivel.
    """
    from scipy.signal import butter, lfilter
    nyq = fs / 2
    low = max(0.01, (freq_centro - ancho_hz / 2) / nyq)
    high = min(0.99, (freq_centro + ancho_hz / 2) / nyq)
    b, a = butter(2, [low, high], btype='band')
    return lfilter(b, a, muestras.astype(np.float64))


def _plantilla_sync(fs):
    """
    Genera la plantilla de referencia: un tono puro de 1200Hz durante exactamente
    el tiempo que dura un pulso de sync real de Martin M1 (4.862ms). La correlación
    cruzada con esta plantilla es específica no solo de la frecuencia sino también de
    la duración — una voz con un armónico en 1200Hz no va a tener exactamente esta
    forma durante exactamente este tiempo.
    """
    n = int(fs * SYNC_PULSO_SEG)
    t = np.arange(n) / fs
    plantilla = np.sin(2 * np.pi * FREQ_SYNC * t)
    return plantilla / (np.linalg.norm(plantilla) + 1e-12)


# Pre-generar la plantilla una sola vez al arrancar (es costoso calcularla en bucle)
_PLANTILLA_SYNC = _plantilla_sync(44100)


def correlacion_sync(muestras_filtradas):
    """
    Correlación normalizada entre las muestras filtradas y la plantilla del pulso
    de sync. Devuelve un valor en [0, 1] — 1 significa que el bloque es
    indistinguible de un tono puro de 1200Hz durante 4.862ms.
    La correlación se calcula sobre la ventana de tamaño igual a la plantilla,
    centrada en el bloque.
    """
    n_plantilla = len(_PLANTILLA_SYNC)
    n_muestras = len(muestras_filtradas)
    if n_muestras < n_plantilla:
        return 0.0
    # Tomar la ventana central del bloque del mismo tamaño que la plantilla
    inicio = (n_muestras - n_plantilla) // 2
    ventana = muestras_filtradas[inicio:inicio + n_plantilla].astype(np.float64)
    norma = np.linalg.norm(ventana)
    if norma < 1e-12:
        return 0.0
    ventana_norm = ventana / norma
    return float(np.abs(np.dot(ventana_norm, _PLANTILLA_SYNC)))


def energia_goertzel(muestras, freq, fs):
    """Potencia de la señal en `freq` sobre el bloque `muestras` (algoritmo de Goertzel)."""
    n = len(muestras)
    if n == 0:
        return 0.0
    k = int(0.5 + n * freq / fs)
    w = 2 * np.pi * k / n
    coef = 2 * np.cos(w)
    s0, s1, s2 = 0.0, 0.0, 0.0
    for muestra in muestras:
        s0 = muestra + coef * s1 - s2
        s2 = s1
        s1 = s0
    potencia = s1 ** 2 + s2 ** 2 - coef * s1 * s2
    return potencia / (n ** 2)


def pureza_tonal(muestras, energia_1200hz):
    """
    Qué fracción de la energía TOTAL del bloque está concentrada en 1200Hz.
    Se calcula sobre las muestras ORIGINALES (no filtradas) para que la
    comparación sea honesta — si ya filtramos antes de Goertzel, la pureza
    sería artificialmente alta incluso para ruido de banda ancha.
    """
    energia_total = float(np.mean(np.asarray(muestras, dtype=np.float64) ** 2))
    if energia_total <= 1e-12:
        return 0.0
    return energia_1200hz / energia_total


def _normalizar(audio):
    audio = audio.astype(np.float64)
    maximo = np.max(np.abs(audio)) or 1.0
    return audio / maximo


def diagnostico_entrada_audio():
    """Muestra los dispositivos de entrada disponibles y cuál se va a usar."""
    log_etapa("INICIO", "Dispositivos de entrada de audio disponibles:")
    try:
        for i, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] > 0:
                marca = " <-- se va a usar" if (
                    (DISPOSITIVO_ENTRADA is None and i == sd.default.device[0])
                    or (DISPOSITIVO_ENTRADA == i)
                ) else ""
                print(f"    [{i}] {dev['name']} ({dev['max_input_channels']} canales){marca}")
    except Exception as e:
        log_etapa("INICIO", f"⚠️  No se pudo listar dispositivos de audio: {e}")


def conectar_impresora():
    """Abre la conexión a la impresora según TIPO_IMPRESORA. Lanza excepción si falla."""
    from escpos.printer import Usb, Serial, Network

    if TIPO_IMPRESORA == "usb":
        if IMPRESORA_VENDOR_ID == 0x0000 or IMPRESORA_PRODUCT_ID == 0x0000:
            raise RuntimeError("IMPRESORA_VENDOR_ID/PRODUCT_ID no configurados (siguen en 0x0000).")
        kwargs = {}
        if IMPRESORA_IN_EP is not None:
            kwargs["in_ep"] = IMPRESORA_IN_EP
        if IMPRESORA_OUT_EP is not None:
            kwargs["out_ep"] = IMPRESORA_OUT_EP
        return Usb(IMPRESORA_VENDOR_ID, IMPRESORA_PRODUCT_ID, **kwargs)

    elif TIPO_IMPRESORA == "bluetooth_serial":
        if IMPRESORA_MAC == "XX:XX:XX:XX:XX:XX":
            raise RuntimeError("IMPRESORA_MAC no configurada. Edítala con la MAC real de la impresora.")
        if not os.path.exists(IMPRESORA_RFCOMM_DEVICE):
            raise RuntimeError(
                f"{IMPRESORA_RFCOMM_DEVICE} no existe todavía. Antes de correr el script, "
                f"vincula el Bluetooth con: sudo rfcomm bind 0 {IMPRESORA_MAC} 1  "
                f"(ver README para el emparejamiento completo)."
            )
        return Serial(devfile=IMPRESORA_RFCOMM_DEVICE, baudrate=IMPRESORA_BAUDRATE, timeout=5)

    elif TIPO_IMPRESORA == "network":
        return Network(IMPRESORA_IP)

    else:
        raise RuntimeError(f"TIPO_IMPRESORA desconocido: '{TIPO_IMPRESORA}' "
                            f"(debe ser 'usb', 'bluetooth_serial' o 'network').")


def diagnostico_impresora():
    """Intenta conectar a la impresora al arrancar, para no descubrir el problema recién al imprimir."""
    if not IMPRIMIR_AUTOMATICAMENTE:
        log_etapa("INICIO", "Impresión automática DESACTIVADA (IMPRIMIR_AUTOMATICAMENTE=False).")
        return
    log_etapa("INICIO", f"Probando conexión a impresora (tipo={TIPO_IMPRESORA})...")
    try:
        impresora = conectar_impresora()
        impresora.close()
        log_etapa("INICIO", f"✅ Impresora conectada correctamente (tipo={TIPO_IMPRESORA}).")
    except Exception as e:
        log_etapa("INICIO", f"❌ No se pudo conectar a la impresora: {e}")


# =====================================================================
# 2. ESCUCHA CONTINUA Y CAPTURA POR DISPARO (VOX)
# =====================================================================
def esperar_y_capturar_transmision():
    """
    Monitorea el micrófono en bloques pequeños buscando el pulso de sync
    (1200 Hz). Cuando lo detecta, graba la duración completa de una
    imagen (con margen) y la devuelve como array numpy.

    El detector usa cuatro criterios en cascada, todos deben cumplirse:
    1. Energía Goertzel en 1200Hz > UMBRAL_DISPARO
    2. Pureza tonal > UMBRAL_PUREZA (no dispersa como voz)
    3. Correlación con plantilla sync > UMBRAL_CORRELACION (forma correcta)
    4. Periodicidad: el bloque candidato se confirma esperando que aparezca
       otro bloque similar ~446ms después (el período real de una línea
       Martin M1). Un grito o impulso no se repite — el sync sí.
    """
    log_etapa("ESCUCHA", "Esperando pulso de sync SSTV (1200 Hz)...")
    bloque = int(FS * 0.010)  # bloques de 10 ms
    bloques_por_telemetria = max(1, int(INTERVALO_TELEMETRIA_SEG / 0.010))

    # Ventana de verificación de periodicidad: cuántos bloques de 10ms
    # esperar después del primer candidato para buscar el siguiente pulso.
    # 446ms / 10ms = ~44 bloques; buscamos entre los bloques 30-60 para
    # dar margen a variaciones de timing (jitter de sala/reproducción).
    BLOQUES_PERIODO_MIN = 30   # ~300ms
    BLOQUES_PERIODO_MAX = 60   # ~600ms

    with sd.InputStream(samplerate=FS, channels=CANALES_ENTRADA, dtype="float32",
                         device=DISPOSITIVO_ENTRADA) as stream:
        contador_bloques = 0
        energia_maxima_vista = 0.0

        def medir_bloque():
            """Lee un bloque y devuelve (muestras, e, p, corr, es_candidato)."""
            datos, _ = stream.read(bloque)
            muestras = datos.mean(axis=1) if CANALES_ENTRADA > 1 else datos[:, 0]
            muestras_f = _filtro_pasabanda(muestras, FREQ_SYNC, 200, FS)
            e = energia_goertzel(muestras_f, FREQ_SYNC, FS)
            p = pureza_tonal(muestras, e)
            corr = correlacion_sync(muestras_f) if (e > UMBRAL_DISPARO and p > UMBRAL_PUREZA) else 0.0
            candidato = e > UMBRAL_DISPARO and p > UMBRAL_PUREZA and corr > UMBRAL_CORRELACION
            return muestras, e, p, corr, candidato

        while True:
            muestras, e, p, corr, es_candidato = medir_bloque()
            energia_maxima_vista = max(energia_maxima_vista, e)
            contador_bloques += 1

            if MOSTRAR_NIVEL_AUDIO and contador_bloques % bloques_por_telemetria == 0:
                barra_len = min(40, int(e / max(UMBRAL_DISPARO, 0.001) * 20))
                barra = "█" * barra_len
                log_etapa("ESCUCHA", f"nivel={e:.3f}  pureza={p:.2f}  "
                                      f"corr={corr:.2f}  máx={energia_maxima_vista:.3f}  {barra}")

            if es_candidato:
                # Criterio 4: verificar periodicidad — esperar y buscar otro
                # pulso en la ventana de ~300-600ms. Si no aparece, era un
                # impulso aislado (voz/grito) y se descarta sin disparar.
                log_etapa("ESCUCHA", f"Candidato detectado (e={e:.4f}, p={p:.2f}, corr={corr:.2f}) "
                                      f"— verificando periodicidad ({BLOQUES_PERIODO_MIN*10}-"
                                      f"{BLOQUES_PERIODO_MAX*10}ms)...")

                confirmado = False
                for i in range(BLOQUES_PERIODO_MAX):
                    _, e2, p2, corr2, candidato2 = medir_bloque()
                    if i >= BLOQUES_PERIODO_MIN and candidato2:
                        log_etapa("ESCUCHA", f"✓ Periodicidad confirmada en bloque +{i*10}ms "
                                              f"(e={e2:.4f}, p={p2:.2f}, corr={corr2:.2f})")
                        confirmado = True
                        break

                if confirmado:
                    log_etapa("DISPARO", f"¡Pulso de sync confirmado! "
                                          f"Iniciando grabación de {DURACION_IMAGEN_SEG:.0f}s...")
                    break
                else:
                    log_etapa("ESCUCHA", "Descartado — no se confirmó periodicidad "
                                          "(probablemente impulso de voz o ruido transitorio).")

        # Fase 2: grabar la duración completa de la imagen + margen
        duracion_captura = DURACION_IMAGEN_SEG + 4.0
        n_muestras = int(FS * duracion_captura)
        capturado = []
        leidas = 0
        t_inicio_captura = time.time()
        siguiente_reporte = t_inicio_captura + INTERVALO_TELEMETRIA_SEG * 5  # cada ~5s

        while leidas < n_muestras:
            faltan = min(bloque, n_muestras - leidas)
            datos, _ = stream.read(faltan)
            muestras = datos.mean(axis=1) if CANALES_ENTRADA > 1 else datos[:, 0]
            capturado.append(muestras.copy())
            leidas += faltan

            if time.time() >= siguiente_reporte:
                transcurrido = time.time() - t_inicio_captura
                log_etapa("CAPTURA", f"grabando... {transcurrido:.0f}s / {duracion_captura:.0f}s")
                siguiente_reporte += INTERVALO_TELEMETRIA_SEG * 5

    log_etapa("CAPTURA", "Grabación completa.")
    return np.concatenate(capturado)


# =====================================================================
# 3. DECODIFICACIÓN MARTIN M1
# =====================================================================
def _mejor_offset_sync(audio, centro_muestra, margen_muestras):
    """
    Busca, alrededor de centro_muestra, el mejor punto de inicio del pulso de
    sync. Vectorizado con numpy (suma de correlación I/Q vía suma acumulada)
    en vez de llamar energia_goertzel en un bucle Python por cada posición
    candidata — eso era el cuello de botella que hacía tardar ~100s por imagen.
    """
    inicio = max(0, centro_muestra - margen_muestras)
    fin = min(len(audio), centro_muestra + margen_muestras)
    ventana_sync = int(FS * SYNC_PULSO_SEG)
    paso = max(1, ventana_sync // 8)

    segmento = audio[inicio:fin]
    if len(segmento) < ventana_sync:
        return centro_muestra

    n = len(segmento)
    idx = np.arange(n)
    fase = 2 * np.pi * FREQ_SYNC * idx / FS
    prod_c = segmento * np.cos(fase)
    prod_s = segmento * np.sin(fase)

    cs_c = np.concatenate(([0.0], np.cumsum(prod_c)))
    cs_s = np.concatenate(([0.0], np.cumsum(prod_s)))

    posiciones = np.arange(0, n - ventana_sync, paso)
    if len(posiciones) == 0:
        return centro_muestra
    suma_c = cs_c[posiciones + ventana_sync] - cs_c[posiciones]
    suma_s = cs_s[posiciones + ventana_sync] - cs_s[posiciones]
    energia = suma_c**2 + suma_s**2

    mejor_idx = int(np.argmax(energia))
    return inicio + int(posiciones[mejor_idx])


def _frecuencia_instantanea(segmento, fs):
    analitica = hilbert(segmento)
    fase = np.unwrap(np.angle(analitica))
    freq_inst = np.diff(fase) / (2.0 * np.pi) * fs
    return np.concatenate([freq_inst, [freq_inst[-1]]])  # mismo largo que el segmento


def _freq_a_pixeles(segmento_audio, fs, ancho_px):
    freq_inst = _frecuencia_instantanea(segmento_audio, fs)
    # Promediar la frecuencia instantánea dentro de cada intervalo de píxel
    bordes = np.linspace(0, len(freq_inst), ancho_px + 1).astype(int)
    pixeles = np.zeros(ancho_px, dtype=np.uint8)
    for i in range(ancho_px):
        tramo = freq_inst[bordes[i]:bordes[i + 1]]
        if len(tramo) == 0:
            valor_freq = FREQ_NEGRO
        else:
            valor_freq = np.median(tramo)
        valor = (valor_freq - FREQ_NEGRO) / (FREQ_BLANCO - FREQ_NEGRO) * 255
        pixeles[i] = np.clip(valor, 0, 255)
    return pixeles


def decodificar_martin_m1(audio):
    """Decodifica un buffer de audio (una imagen completa) a un objeto PIL.Image RGB."""
    audio = _normalizar(audio)

    canal_g = np.zeros((ALTO_IMG, ANCHO_IMG), dtype=np.uint8)
    canal_b = np.zeros((ALTO_IMG, ANCHO_IMG), dtype=np.uint8)
    canal_r = np.zeros((ALTO_IMG, ANCHO_IMG), dtype=np.uint8)

    margen_muestras = int(FS * MARGEN_BUSQUEDA_SEG)
    puntero_esperado = 0

    for linea in range(ALTO_IMG):
        inicio_sync = _mejor_offset_sync(audio, puntero_esperado, margen_muestras)
        cursor = inicio_sync + int(FS * (SYNC_PULSO_SEG + SYNC_PORCHE_SEG))

        for nombre_canal in ORDEN_CANALES:
            n_muestras_canal = int(FS * DURACION_CANAL_SEG)
            segmento = audio[cursor:cursor + n_muestras_canal]
            if len(segmento) < n_muestras_canal:
                segmento = np.pad(segmento, (0, n_muestras_canal - len(segmento)))

            pixeles = _freq_a_pixeles(segmento, FS, ANCHO_IMG)
            if nombre_canal == "G":
                canal_g[linea] = pixeles
            elif nombre_canal == "B":
                canal_b[linea] = pixeles
            else:
                canal_r[linea] = pixeles

            cursor += n_muestras_canal + int(FS * SEPARADOR_SEG)

        puntero_esperado = inicio_sync + int(FS * DURACION_LINEA_SEG)

    rgb = np.dstack([canal_r, canal_g, canal_b])
    return Image.fromarray(rgb, mode="RGB")


# =====================================================================
# 4. IMPRESIÓN EN IMPRESORA TÉRMICA (ESC/POS)
# =====================================================================
def imprimir_imagen(imagen, numero_segmento, total_segmentos=None):
    if not IMPRIMIR_AUTOMATICAMENTE:
        log_etapa("IMPRESIÓN", "Omitida (IMPRIMIR_AUTOMATICAMENTE=False).")
        return
    t0 = time.time()
    try:
        log_etapa("IMPRESIÓN", f"Conectando a impresora (tipo={TIPO_IMPRESORA})...")
        impresora = conectar_impresora()

        ancho_orig, alto_orig = imagen.size
        nuevo_alto = max(1, round(alto_orig * (IMPRESORA_ANCHO_DOTS / ancho_orig)))
        imagen_ajustada = imagen.resize((IMPRESORA_ANCHO_DOTS, nuevo_alto), Image.LANCZOS)

        # Pipeline de limpieza optimizado para texto SSTV acústico:
        #
        # Las bandas horizontales de ruido (el artefacto más característico
        # del SSTV captado por micrófono) suben o bajan el nivel de brillo
        # de toda una fila por igual. La normalización por fila las elimina
        # usando el percentil 90 de cada fila como referencia de "blanco" —
        # después de normalizar, el blanco de cada fila es siempre 255 sin
        # importar cuánto ruido horizontal tenía esa fila originalmente.
        #
        # Luego un unsharp mask enfatiza los bordes del texto antes de
        # aplicar un umbral global simple (que ahora sí funciona bien porque
        # el nivel de blanco ya está estandarizado en toda la imagen).
        from PIL import ImageFilter
        import numpy as np

        gris = np.array(imagen_ajustada.convert("L"), dtype=np.float32)

        # Paso 1: normalización por fila
        blanco = np.percentile(gris, 90, axis=1, keepdims=True)
        blanco = np.maximum(blanco, 1.0)
        gris_norm = np.clip(gris / blanco * 255, 0, 255)

        # Paso 2: unsharp mask para enfatizar texto
        sharp = gris_norm

        # Paso 3: umbral global (funciona bien post-normalización)
        binaria = np.where(sharp < 195, 0, 255).astype(np.uint8)

        # Paso 4: mediana para limpiar píxeles aislados
        imagen_limpia = Image.fromarray(binaria, "L").filter(ImageFilter.MedianFilter(size=3))
        imagen_final = imagen_limpia.convert("RGB")

        if IMPRIMIR_ETIQUETA_TEXTO:
            etiqueta = f"Segmento {numero_segmento}"
            if total_segmentos:
                etiqueta += f"/{total_segmentos}"
            etiqueta += f" — {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            impresora.set(align="center")
            impresora.text(etiqueta + "\n")

        impresora.image(imagen_final, center=False)

        if CORTAR_ENTRE_SEGMENTOS:
            impresora.cut()

        impresora.close()
        log_etapa("IMPRESIÓN", f"✅ Segmento {numero_segmento} impreso "
                                f"({IMPRESORA_ANCHO_DOTS}x{nuevo_alto} dots, {time.time() - t0:.1f}s).")
    except Exception as e:
        log_etapa("IMPRESIÓN", f"❌ Error al imprimir: {e}")


# =====================================================================
# 5. LOG DE RECEPCIÓN
# =====================================================================
def cargar_log():
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH) as f:
            return json.load(f)
    return {"recibidos": 0, "historial": []}


def guardar_log(log):
    with open(LOG_PATH, "w") as f:
        json.dump(log, f, indent=2)


# =====================================================================
# BUCLE PRINCIPAL
# =====================================================================
if __name__ == "__main__":
    print("=== RECEPTOR SSTV — DECODIFICADOR DEL RECORRIDO DEL RÍO ===")
    diagnostico_entrada_audio()
    diagnostico_impresora()
    log = cargar_log()
    log_etapa("INICIO", f"Segmentos recibidos hasta ahora: {log['recibidos']}.")

    try:
        while True:
            audio = esperar_y_capturar_transmision()

            t0 = time.time()
            log_etapa("DECODIFICACIÓN", "Decodificando imagen (esto toma unos segundos)...")
            try:
                imagen = decodificar_martin_m1(audio)
                log_etapa("DECODIFICACIÓN", f"Listo ({time.time() - t0:.1f}s).")
            except Exception as e:
                log_etapa("DECODIFICACIÓN", f"❌ Error al decodificar: {e}. Descartando esta captura.")
                continue

            log["recibidos"] += 1
            nombre_archivo = os.path.join(
                OUTPUT_DIR, f"segmento_{log['recibidos']:04d}.png"
            )
            imagen.save(nombre_archivo)
            tamano_kb = os.path.getsize(nombre_archivo) / 1024
            log["historial"].append({
                "numero": log["recibidos"],
                "archivo": nombre_archivo,
                "hora": datetime.now().isoformat(),
            })
            guardar_log(log)
            log_etapa("GUARDADO", f"Imagen #{log['recibidos']} guardada: {nombre_archivo} ({tamano_kb:.0f} KB)")

            imprimir_imagen(imagen, log["recibidos"])
            log_etapa("CICLO", f"Segmento {log['recibidos']} completo. Volviendo a escuchar.\n")

    except KeyboardInterrupt:
        print("\nReceptor detenido manualmente.")
