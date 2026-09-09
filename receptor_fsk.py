#!/usr/bin/env python3
"""
=====================================================================
RECEPTOR FSK — POEMA VISUAL BERENGUER
=====================================================================
Escucha el canal acústico, detecta y decodifica mensajes FSK,
aplica el Método Berenguer al texto recibido para generar una
imagen visual, y la imprime en la impresora térmica.

El texto viaja por el aire (no una imagen) — la transformación
visual ocurre en la recepción. Mucho más robusto que SSTV para
canales acústicos ruidosos.

Uso:
  python3 receptor_fsk.py
=====================================================================
"""

import argparse
import os
import json
import time
import struct
from datetime import datetime

import numpy as np
import sounddevice as sd
from PIL import Image

# Importar el generador visual y la impresora
from metodo_berenguer import (
    cargar_frases,
    generar_segmento_secuencial,
    cargar_o_crear_estado_poema,
    guardar_estado_poema,
)

# =====================================================================
# CONFIGURACIÓN — debe coincidir con el transmisor
# =====================================================================
FREQ_MARK  = 1200   # Hz — bit 1
FREQ_SPACE = 2200   # Hz — bit 0
FS         = 44100  # Hz
BAUDRATE   = 50     # símbolos/segundo
SAMPLES_POR_SIMBOLO = int(FS / BAUDRATE)

SYNC_WORD  = b'\xAB\xCD'

# Umbral de energía mínima para considerar que hay señal (no silencio)
UMBRAL_ENERGIA = 0.00008  # ruido máx sala: 0.000033 → margen 2.4×

# Umbral de decisión mark vs space: si la energía en MARK supera
# a la de SPACE por este factor, es bit 1 (y viceversa)
UMBRAL_DECISION = 1.05

# =====================================================================
# HARDWARE — configuración confirmada en las pruebas
# =====================================================================
# Grabadora Roland R-07 como interfaz de audio (o Soundcraft Notepad-8FX
# cuando esté disponible). Índice 1 en sounddevice, 2 canales estéreo.
# Si cambia tras un reinicio, verificar con:
#   python3 -c "import sounddevice as sd; print(sd.query_devices())"
DISPOSITIVO_ENTRADA = 1
CANALES_ENTRADA     = 2

# --- Impresora térmica USB (confirmada con lsusb: ID 0483:5720) ---
# Endpoints confirmados: IN=0x81, OUT=0x03
# usblp debe estar en blacklist: /etc/modprobe.d/blacklist-usblp.conf
# Regla udev: /etc/udev/rules.d/99-escpos.rules → MODE="0666"
TIPO_IMPRESORA       = "usb"
IMPRESORA_VENDOR_ID  = 0x0483
IMPRESORA_PRODUCT_ID = 0x5720
IMPRESORA_IN_EP      = 0x81
IMPRESORA_OUT_EP     = 0x03
IMPRESORA_ANCHO_DOTS = 576   # papel 80mm a 203dpi
CORTAR_ENTRE_SEGMENTOS = False

IMPRIMIR_AUTOMATICAMENTE = True
INTERVALO_TELEMETRIA_SEG = 1.0

# La impresora alimenta el papel hacia abajo y cae/se amontona en el piso.
# Lo primero que se dibuja (arriba de la imagen) es lo primero que sale y
# termina más lejos de la impresora — al leer el papel caído queda al
# revés respecto a como se generó. Rotando 180° antes de imprimir (no en
# el PNG que se guarda, solo en lo que recibe la impresora) el papel
# caído queda legible de forma natural. Si se cambia el montaje físico
# de la impresora, poner esto en False.
INVERTIR_PARA_PAPEL_CAIDO = True

OUTPUT_DIR = "sstv_recibido"
ESTADO_BERENGUER = os.path.join(OUTPUT_DIR, "estado_berenguer.json")


# =====================================================================
# UTILIDADES
# =====================================================================
def log(etapa, msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [{etapa}] {msg}")


def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
    return crc


# ── Goertzel vectorizado ─────────────────────────────────────────────
# El Goertzel recursivo original (bucle Python de 882 iteraciones por
# bloque) tarda ~22s para procesar 8 offsets en una Pi.
# Matemáticamente equivale a calcular el módulo del DFT en el bin k:
#   E = (re² + im²) / N²   con   re = Σ x[n]·cos(2πkn/N)
#                                 im = Σ x[n]·sin(2πkn/N)
# Con numpy, eso son dos productos punto sobre arrays de 882 floats —
# 20-30× más rápido. Los vectores de fase se precomputan una sola vez.
_SPB = int(FS / BAUDRATE)   # 882 — coincide con SAMPLES_POR_SIMBOLO
_T   = np.arange(_SPB, dtype=np.float32)

def _precompute_goertzel_vectors():
    cos_v, sin_v = {}, {}
    for freq in (FREQ_MARK, FREQ_SPACE):
        k = int(0.5 + _SPB * freq / FS)
        phase = 2.0 * np.pi * k / _SPB
        cos_v[freq] = np.cos(phase * _T)
        sin_v[freq] = np.sin(phase * _T)
    return cos_v, sin_v

_GCOS, _GSIN = _precompute_goertzel_vectors()


def energia_goertzel(muestras, freq, fs):
    """Potencia de la señal en freq Hz. Vectorizado con numpy."""
    n = len(muestras)
    if n == 0:
        return 0.0
    if n == _SPB and freq in _GCOS:
        # Camino rápido: vectores de fase precomputados (caso habitual)
        re = float(np.dot(muestras, _GCOS[freq]))
        im = float(np.dot(muestras, _GSIN[freq]))
    else:
        # Camino general (bloques de tamaño distinto — no debería ocurrir)
        k = int(0.5 + n * freq / fs)
        t = np.arange(n, dtype=np.float32)
        phase = 2.0 * np.pi * k / n
        re = float(np.dot(muestras, np.cos(phase * t)))
        im = float(np.dot(muestras, np.sin(phase * t)))
    return (re * re + im * im) / (n * n)


# =====================================================================
# DEMODULADOR FSK
# =====================================================================
def detectar_bit(bloque):
    """
    Decide si el bloque es bit 1 (MARK=1200Hz) o bit 0 (SPACE=2200Hz).
    Devuelve (bit, energía_total) o None si la energía es demasiado baja.
    """
    e_mark  = energia_goertzel(bloque, FREQ_MARK,  FS)
    e_space = energia_goertzel(bloque, FREQ_SPACE, FS)
    energia_total = e_mark + e_space

    if energia_total < UMBRAL_ENERGIA:
        return None, energia_total  # silencio

    if e_mark > e_space * UMBRAL_DECISION:
        return 1, energia_total
    elif e_space > e_mark * UMBRAL_DECISION:
        return 0, energia_total
    else:
        # Ambiguo — decide por mayoría
        return (1 if e_mark > e_space else 0), energia_total


def bits_a_bytes(bits):
    """Convierte lista de bits (MSB primero) a bytes."""
    resultado = bytearray()
    for i in range(0, len(bits) - 7, 8):
        byte = 0
        for j in range(8):
            byte = (byte << 1) | bits[i + j]
        resultado.append(byte)
    return bytes(resultado)


def buscar_sync_en_bits(bits, sync=SYNC_WORD):
    """
    Busca el sync word en el flujo de bits probando los 8 posibles
    alineamientos de byte. El preámbulo tiene longitud variable (no
    siempre múltiplo de 8) así que el sync puede caer en cualquier
    posición de bit dentro de un byte.
    Devuelve el índice de bit donde comienza el primer byte del sync,
    o -1 si no se encuentra.
    """
    sync_bits = []
    for byte in sync:
        for i in range(7, -1, -1):
            sync_bits.append((byte >> i) & 1)

    n = len(sync_bits)
    for i in range(len(bits) - n):
        if bits[i:i+n] == sync_bits:
            return i  # índice del primer bit del sync
    return -1


def decodificar_paquete(bits):
    """
    Busca el sync word en el flujo de bits (no en bytes), extrae el
    mensaje y verifica el CRC.
    """
    idx_bit = buscar_sync_en_bits(bits, SYNC_WORD)
    if idx_bit < 0:
        return None, False

    # El mensaje empieza después del sync word
    inicio_datos = idx_bit + len(SYNC_WORD) * 8
    datos = bits_a_bytes(bits[inicio_datos:])

    if len(datos) < 4:
        return None, False

    longitud, crc_rx = struct.unpack(">HH", datos[:4])
    payload = datos[4:4 + longitud]

    if len(payload) < longitud:
        return None, False

    crc_calc = _crc16(payload)
    ok = (crc_calc == crc_rx)

    try:
        texto = payload.decode("utf-8", errors="replace")
    except Exception:
        return None, False

    return texto, ok


# =====================================================================
# DECODIFICACIÓN CON BÚSQUEDA DE OFFSET DE SÍMBOLO
# =====================================================================
def decodificar_con_offset(muestras_array):
    """
    El receptor y el transmisor no comparten reloj de símbolo. El bloque de
    882 muestras del receptor puede estar desfasado hasta 441 muestras
    (medio símbolo) respecto al transmisor, lo que hace que cada bloque
    capture mitad de un tono y mitad del siguiente → Goertzel ve ambas
    frecuencias iguales → decisión de bit errónea → alt=0.

    Solución: probar 8 offsets distintos (0, 110, 220, ..., 661 muestras)
    y devolver el primero que dé CRC válido. Si ninguno da CRC pero alguno
    decodifica un texto, devuelve ese con crc_ok=False.
    """
    muestras = np.array(muestras_array, dtype=np.float32)
    spb = SAMPLES_POR_SIMBOLO
    N_OFFSETS = 8
    mejor_texto, mejor_ok = None, False

    for off_idx in range(N_OFFSETS):
        offset = (off_idx * spb) // N_OFFSETS
        bits = []
        pos = offset
        while pos + spb <= len(muestras):
            bloque = muestras[pos:pos + spb]
            bit, _ = detectar_bit(bloque)
            bits.append(bit if bit is not None else 0)
            pos += spb

        texto, ok = decodificar_paquete(bits)
        if texto and ok:
            log("DECODIF", f"✓ offset={offset} ({off_idx+1}/{N_OFFSETS}) → CRC OK")
            return texto, True
        if texto and mejor_texto is None:
            mejor_texto = texto   # guardar candidato sin CRC por si no hay mejor

    if mejor_texto:
        log("DECODIF", "⚠️  Ningún offset dio CRC OK — usando mejor candidato")
        return mejor_texto, False
    return None, False


# =====================================================================
# ESCUCHA CONTINUA
# =====================================================================
def esperar_y_decodificar():
    """
    Escucha el micrófono y espera un mensaje FSK completo.

    Preamble detection: energía sostenida (no detección de bits) — evita
    el problema de alt=0 causado por el desfase de símbolo.

    Captura muestras crudas durante la recepción y llama a
    decodificar_con_offset() que prueba 8 alineaciones distintas.

    Devuelve (texto, crc_ok) cuando detecta y decodifica uno.
    """
    log("ESCUCHA", f"Esperando señal FSK (mark={FREQ_MARK}Hz, space={FREQ_SPACE}Hz, "
                    f"{BAUDRATE} baud)...")

    spb = SAMPLES_POR_SIMBOLO
    MAX_BLOQUES_SILENCIO = BAUDRATE * 2      # 2s de silencio = fin de mensaje
    MAX_MUESTRAS = FS * 14                   # 14s máximo de captura
    PREAMBULO_MIN_BLOQUES = 15               # 0.3s de energía continua

    energia_count = 0
    en_recepcion = False
    chunks_rx = []   # lista de arrays numpy — se concatenan al final
    bloques_silencio = 0

    with sd.InputStream(samplerate=FS, channels=CANALES_ENTRADA,
                        dtype="float32", device=DISPOSITIVO_ENTRADA) as stream:

        contador = 0
        while True:
            bloque, _ = stream.read(spb)
            muestras = bloque.mean(axis=1) if CANALES_ENTRADA > 1 else bloque[:, 0]
            _, energia = detectar_bit(muestras)

            contador += 1
            if contador % (BAUDRATE * INTERVALO_TELEMETRIA_SEG) == 0:
                estado = "RX" if en_recepcion else "escuchando"
                muestras_acum = sum(len(c) for c in chunks_rx) / FS
                log("ESCUCHA", f"energia={energia:.5f}  "
                               f"muestras={muestras_acum:.1f}s  "
                               f"estado={estado}")

            if not en_recepcion:
                if energia > UMBRAL_ENERGIA:
                    energia_count += 1
                    if energia_count >= PREAMBULO_MIN_BLOQUES:
                        log("ESCUCHA", f"✓ Preámbulo detectado "
                                       f"({energia_count} bloques con energía). "
                                       f"Capturando datos...")
                        en_recepcion = True
                        chunks_rx = []
                        bloques_silencio = 0
                else:
                    energia_count = max(0, energia_count - 2)

            else:
                chunks_rx.append(muestras.copy())

                if energia <= UMBRAL_ENERGIA:
                    bloques_silencio += 1
                else:
                    bloques_silencio = 0

                n_muestras = sum(len(c) for c in chunks_rx)

                if bloques_silencio >= MAX_BLOQUES_SILENCIO:
                    duracion = n_muestras / FS
                    log("DECODIF", f"Fin de señal — {duracion:.1f}s capturados. "
                                    f"Probando 8 offsets...")
                    muestras_rx = np.concatenate(chunks_rx)
                    texto, ok = decodificar_con_offset(muestras_rx)
                    if texto:
                        return texto, ok
                    log("DECODIF", "No se encontró paquete válido. Volviendo a escuchar.")
                    en_recepcion = False
                    chunks_rx = []
                    energia_count = 0
                    bloques_silencio = 0

                elif n_muestras > MAX_MUESTRAS:
                    log("DECODIF", "Límite de captura alcanzado. Probando 8 offsets...")
                    muestras_rx = np.concatenate(chunks_rx)
                    texto, ok = decodificar_con_offset(muestras_rx)
                    if texto:
                        return texto, ok
                    log("DECODIF", "No se encontró paquete válido. Reiniciando.")
                    en_recepcion = False
                    chunks_rx = []
                    energia_count = 0
                    bloques_silencio = 0


# =====================================================================
# IMPRESIÓN
# =====================================================================
def conectar_impresora():
    from escpos.printer import Usb
    kwargs = {}
    if IMPRESORA_IN_EP  is not None: kwargs["in_ep"]  = IMPRESORA_IN_EP
    if IMPRESORA_OUT_EP is not None: kwargs["out_ep"] = IMPRESORA_OUT_EP
    return Usb(IMPRESORA_VENDOR_ID, IMPRESORA_PRODUCT_ID, **kwargs)


def imprimir(imagen):
    if not IMPRIMIR_AUTOMATICAMENTE:
        log("IMPRESIÓN", "Omitida.")
        return
    try:
        impresora = conectar_impresora()
        w, h = imagen.size

        # La imagen ya viene a 576px de ancho (resolución nativa de la impresora).
        # Solo redimensionar si por algún motivo el ancho no coincide.
        if w != IMPRESORA_ANCHO_DOTS:
            nuevo_h = max(1, round(h * (IMPRESORA_ANCHO_DOTS / w)))
            img_r = imagen.resize((IMPRESORA_ANCHO_DOTS, nuevo_h), Image.LANCZOS)
            log("IMPRESIÓN", f"⚠️  Redimensionando {w}→{IMPRESORA_ANCHO_DOTS}px (inesperado)")
        else:
            img_r = imagen

        # Binarización: normalización por fila para compensar variaciones de
        # iluminación o fondo ligeramente gris en la imagen generada.
        from PIL import ImageFilter
        gris = np.array(img_r.convert("L"), dtype=np.float32)
        blanco = np.percentile(gris, 90, axis=1, keepdims=True)
        blanco = np.maximum(blanco, 1.0)
        gris_norm = np.clip(gris / blanco * 255, 0, 255)
        binaria = np.where(gris_norm < 195, 0, 255).astype(np.uint8)
        img_final = Image.fromarray(binaria, "L").filter(
            ImageFilter.MedianFilter(size=3)
        ).convert("RGB")

        # Rotar 180° solo lo que se manda a la impresora (el PNG guardado en
        # sstv_recibido/ queda en orientación normal para revisar en pantalla).
        if INVERTIR_PARA_PAPEL_CAIDO:
            img_final = img_final.rotate(180)

        impresora.image(img_final, center=False)
        impresora.ln(4)   # avance entre segmentos — espacio visible en el rollo
        if CORTAR_ENTRE_SEGMENTOS:
            impresora.cut()
        impresora.close()
        w_f, h_f = img_final.size
        log("IMPRESIÓN", f"✅ Impreso ({w_f}×{h_f} dots)")
    except Exception as e:
        log("IMPRESIÓN", f"❌ Error: {e}")


# =====================================================================
# PIPELINE PRINCIPAL
# =====================================================================
def procesar_texto(texto, frases_pool, estado_berenguer):
    """
    Recibe el texto decodificado, genera el segmento visual Berenguer y lo
    imprime. El texto recibido es el único material del segmento: se despliega
    repetido a través de N transformaciones consecutivas hasta completar los
    ~50mm del strip. Esto hace que cada frase transmitida genere su propio
    bloque visual coherente en el rollo, distinguible de los demás.

    Si el texto está vacío (paquete sin payload útil), se usa frases_pool
    para no interrumpir el rollo.

    Nota: los errores de CRC NO descartan el texto — el texto corrompido
    por el canal se procesa igual. La corrupción acústica es parte del
    poema; puede generar fragmentos visuales interesantes.
    """
    log("BERENGUER", f"Texto recibido: '{texto[:80]}'")

    # Un solo texto como pool → el segmento despliega esa frase en múltiples
    # transformaciones (índice % 10 distintos), como variaciones sobre un tema.
    frases_este_ciclo = [texto] if texto.strip() else frases_pool

    img, nuevo_idx, nuevo_off = generar_segmento_secuencial(
        frases_este_ciclo,
        indice_frase=estado_berenguer["indice_frase"],
        offset=estado_berenguer["offset"],
    )

    # Guardar PNG
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    n = estado_berenguer.get("segmento", 0)
    png_path = os.path.join(OUTPUT_DIR, f"berenguer_{n:04d}.png")
    img.save(png_path)
    log("BERENGUER", f"Imagen generada: {png_path}")

    imprimir(img)

    return {
        "indice_frase": nuevo_idx,
        "offset": nuevo_off,
        "segmento": n + 1,
    }


# =====================================================================
# CALIBRACIÓN DE UMBRALES
# =====================================================================
def calibrar_umbrales(duracion_seg=30):
    """
    Mide la energía Goertzel en FREQ_MARK y FREQ_SPACE durante `duracion_seg`
    segundos de solo ruido de sala (sin transmitir). Imprime los estadísticos
    necesarios para ajustar UMBRAL_ENERGIA y UMBRAL_DECISION.

    Uso:
        # 1. Solo ruido — no transmitir durante este paso
        python3 receptor_fsk.py --calibrar

        # 2. Con señal — transmitir una frase mientras corre el receptor normal
        #    y observar los logs de telemetría para ver las energías con señal.
    """
    log("CALIBRAR", f"Midiendo ruido de sala durante {duracion_seg}s")
    log("CALIBRAR", "NO transmitir durante este paso.")
    spb = SAMPLES_POR_SIMBOLO
    n_bloques = int(FS * duracion_seg / spb)
    e_mark_list, e_space_list, e_total_list = [], [], []

    with sd.InputStream(samplerate=FS, channels=CANALES_ENTRADA,
                        dtype="float32", device=DISPOSITIVO_ENTRADA) as stream:
        for i in range(n_bloques):
            bloque, _ = stream.read(spb)
            muestras = bloque.mean(axis=1) if CANALES_ENTRADA > 1 else bloque[:, 0]
            e_m = energia_goertzel(muestras, FREQ_MARK,  FS)
            e_s = energia_goertzel(muestras, FREQ_SPACE, FS)
            e_t = e_m + e_s
            e_mark_list.append(e_m)
            e_space_list.append(e_s)
            e_total_list.append(e_t)
            if (i + 1) % (BAUDRATE * 5) == 0:
                seg_transcurrido = (i + 1) * spb / FS
                log("CALIBRAR", f"{seg_transcurrido:.0f}s  "
                                f"mark={e_m:.6f}  space={e_s:.6f}  total={e_t:.6f}")

    e_max   = float(np.max(e_total_list))
    e_media = float(np.mean(e_total_list))
    e_p99   = float(np.percentile(e_total_list, 99))
    umbral_sug = e_p99 * 4.0

    log("CALIBRAR", "─── Resultado (solo ruido de sala) ──────────────────")
    log("CALIBRAR", f"Energía total — media: {e_media:.6f}  p99: {e_p99:.6f}  máx: {e_max:.6f}")
    log("CALIBRAR", f"Energía mark  — media: {np.mean(e_mark_list):.6f}  máx: {np.max(e_mark_list):.6f}")
    log("CALIBRAR", f"Energía space — media: {np.mean(e_space_list):.6f}  máx: {np.max(e_space_list):.6f}")
    log("CALIBRAR", "─── Valor sugerido ──────────────────────────────────")
    log("CALIBRAR", f"UMBRAL_ENERGIA = {umbral_sug:.6f}   (4× percentil 99 del ruido)")
    log("CALIBRAR", "Configura ese valor en este script y luego transmite")
    log("CALIBRAR", "una frase para verificar que la energía con señal lo supera.")
    log("CALIBRAR", "Con señal, la ratio mark/space debería ser > 3. Si es < 1.5")
    log("CALIBRAR", "considera bajar BAUDRATE o acercar parlante a la grabadora.")


# =====================================================================
# MAIN
# =====================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Receptor FSK — Poema Visual Berenguer")
    parser.add_argument(
        "--calibrar", action="store_true",
        help="Mide energía de ruido de sala durante 30s para calibrar UMBRAL_ENERGIA."
             " No transmitir durante este modo.",
    )
    parser.add_argument(
        "--duracion-calibracion", type=int, default=30, metavar="SEG",
        help="Duración de la calibración en segundos (default: 30).",
    )
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    if args.calibrar:
        calibrar_umbrales(duracion_seg=args.duracion_calibracion)
        raise SystemExit(0)

    # Cargar frases de respaldo (usadas cuando el texto recibido llega vacío)
    frases_pool = cargar_frases("frases.txt")

    # Estado del generador Berenguer
    estado_berenguer = cargar_o_crear_estado_poema(ESTADO_BERENGUER)

    print("=== RECEPTOR FSK — POEMA VISUAL BERENGUER ===")
    print(f"mark={FREQ_MARK}Hz  space={FREQ_SPACE}Hz  {BAUDRATE} baud")
    print(f"dispositivo entrada: {DISPOSITIVO_ENTRADA}  canales: {CANALES_ENTRADA}")
    print(f"imagen nativa: 576×400 dots (sin upscale)")
    print(f"segmentos recibidos hasta ahora: {estado_berenguer.get('segmento', 0)}")
    print(f"calibrar umbrales: python3 receptor_fsk.py --calibrar")

    try:
        while True:
            texto, crc_ok = esperar_y_decodificar()

            if not crc_ok:
                log("CRC", "⚠️  CRC no coincide — texto puede tener errores. "
                            "Se procesa igual (la corrupción es parte del poema).")
            else:
                log("CRC", "✅ CRC OK — texto íntegro.")

            estado_berenguer = procesar_texto(texto, frases_pool, estado_berenguer)
            guardar_estado_poema(estado_berenguer, ESTADO_BERENGUER)

    except KeyboardInterrupt:
        print("\nReceptor detenido. Estado guardado.")
