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

import subprocess as _subprocess

class ArecordStream:
    """Lee audio via arecord (para dispositivos que PortAudio no enumera como entrada)."""
    _BYTES = 4  # S32_LE
    def __init__(self, device, samplerate, channels, blocksize):
        self.channels  = channels
        self.blocksize = blocksize
        self._bpb      = blocksize * channels * self._BYTES
        alsa_dev       = f'hw:{device},0' if isinstance(device, int) else str(device)
        self._proc     = _subprocess.Popen(
            ['arecord', '-D', alsa_dev, '-f', 'S32_LE',
             '-r', str(samplerate), '-c', str(channels), '-t', 'raw'],
            stdout=_subprocess.PIPE, stderr=_subprocess.DEVNULL)
    def read(self, frames):
        import numpy as _np
        need = frames * self.channels * self._BYTES
        raw = self._proc.stdout.read(need)
        if len(raw) < need:
            raise IOError(f"arecord terminó inesperadamente ({len(raw)}/{need} bytes)")
        arr = _np.frombuffer(raw, dtype=_np.int32).astype(_np.float32) / 2147483648.0
        return arr.reshape(frames, self.channels), False
    def close(self):
        try:
            self._proc.terminate()
            self._proc.wait(timeout=3)
        except Exception:
            try: self._proc.kill()
            except: pass
            try: self._proc.wait(timeout=1)
            except: pass
    def __enter__(self): return self
    def __exit__(self, *a): self.close()

from PIL import Image

# Importar el generador visual y la impresora
from metodo_berenguer_v2 import (
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
FS         = 48000  # Hz
BAUDRATE   = 50     # símbolos/segundo
SAMPLES_POR_SIMBOLO = int(FS / BAUDRATE)

SYNC_WORD  = b'\xAB\xCD'

# Umbral de energía mínima para considerar que hay señal (no silencio)
UMBRAL_ENERGIA = 0.00008  # ruido máx sala: 0.000033 → margen 2.4×

# Umbral de decisión mark vs space: si la energía en MARK supera
# a la de SPACE por este factor, es bit 1 (y viceversa)
UMBRAL_DECISION = 1.05

# --- Corrección del canal acústico (medido 31/10/2026, ver ~/analisis_fsk) ---
# 1) En la sala 2200 Hz llega ~10 dB más fuerte que 1200 Hz: se mide el nivel
#    de cada tono en el preámbulo (alterna 1200/2200) y se emparejan.
# 2) Tras cada tono queda una cola ~-8 dB durante >40 ms: se resta una
#    fracción ALFA de la energía del bit anterior.
# 3) El reloj del emisor va ~+400 ppm respecto al receptor: se prueban
#    varios desvíos. El CRC decide cuál combinación es la buena.
NORMALIZAR_CON_PREAMBULO = True
BLOQUES_REF_PREAMBULO = (2, 50)      # bloques de la captura dentro del preámbulo
ALFAS_COLA = (0.1, 0.2, 0.0)
PPM_BUSQUEDA = (400, 500, 300, 600, 200, 0)
N_OFFSETS = 16
LEN_MAX = 200                        # paquetes con LEN mayor se descartan (falso SYNC)

# =====================================================================
# HARDWARE — configuración confirmada en las pruebas
# =====================================================================
# Grabadora Roland R-07 como interfaz de audio (o Soundcraft Notepad-8FX
# cuando esté disponible). Índice 1 en sounddevice, 2 canales estéreo.
# Si cambia tras un reinicio, verificar con:
#   python3 -c "import sounddevice as sd; print(sd.query_devices())"
DISPOSITIVO_ENTRADA = 2
CANALES_ENTRADA = 2

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

# Probabilidad de que todas las transformaciones del segmento usen la misma
# frase recibida (comportamiento original: variaciones sobre un tema).
# El complemento (1 - PROB_REPETIR_FRASE) es la probabilidad de que cada
# transformación use una frase distinta del pool (comportamiento nuevo).
# Rango recomendado: 0.20–0.30 para que la repetición sea rara pero aparezca.
PROB_REPETIR_FRASE = 0.25

OUTPUT_DIR = "sstv_recibido"
ESTADO_BERENGUER = os.path.join(OUTPUT_DIR, "estado_berenguer.json")


# =====================================================================
# UTILIDADES
# =====================================================================
def log(etapa, msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [{etapa}] {msg}")


# Registro permanente en disco: una línea por señal recibida (~2 MB en 3 meses).
# Columnas: fecha-hora ISO <TAB> estado (CRC_OK / CRC_FALLA / SIN_PAQUETE) <TAB> texto
REGISTRO_RECEPCIONES = os.path.expanduser("~/registro_recepciones.tsv")

def registrar(estado, texto=""):
    try:
        limpio = (texto or "").replace("\t", " ").replace("\n", " ").replace("\r", " ")
        with open(REGISTRO_RECEPCIONES, "a", encoding="utf-8", errors="replace") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')}\t{estado}\t{limpio}\n")
    except Exception as e:
        log("REGISTRO", f"no se pudo escribir: {e}")

def berenguer_a_texto(texto, idx):
    """Representación en texto de la transformación para display_receptor.py"""
    palabras = texto.split()
    if not palabras:
        return [texto]
    t = idx % 10
    if t == 0:
        n, pk = len(palabras), len(palabras) // 2
        return [' '.join(w.upper() if i == pk else w.lower() for i, w in enumerate(palabras))]
    elif t == 1:
        mid = max(1, len(palabras) // 2)
        f1, f2 = ' '.join(palabras[:mid]), ' '.join(palabras[mid:])
        return [f1, '', ' ' * (len(f1) + 4) + f2]
    elif t == 2:
        return [c for c in texto if c != ' ']
    elif t == 3:
        return [' ' * (i * 3) + w for i, w in enumerate(palabras)]
    elif t == 4:
        n = len(palabras)
        return [' ' * (max(0, n-1-i) * 3) + w for i, w in enumerate(palabras)]
    elif t == 5:
        mid = max(1, len(palabras) // 2)
        return [' '.join(palabras[:mid]), ' '.join(w[::-1] for w in reversed(palabras[mid:]))]
    elif t == 6:
        return [' ' * (i * 5) + w for i, w in enumerate(palabras)]
    elif t == 7:
        pos = [0, 16, 36, 54]
        return [' ' * pos[i % 4] + w for i, w in enumerate(palabras[:4])]
    elif t == 8:
        espina = max(palabras, key=len).upper()
        return [espina] + ['   ' + w for w in palabras if w.upper() != espina]
    elif t == 9:
        lineas, col = [], 0
        for ch in texto:
            if ch == ' ':
                col += 2
                continue
            lineas.append(' ' * col + ch)
            col += 1
        return lineas
    return [texto]



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
    if longitud == 0 or longitud >= LEN_MAX:
        return None, False
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
DIR_FALLOS = os.path.expanduser("~/fallos_fsk")
MAX_FALLOS = 30         # se conservan los 30 audios de fallo más recientes (~1.3 MB c/u)

def guardar_fallo(muestras):
    """Guarda el audio de una recepción fallida (WAV 16 bit) para analizarla después."""
    try:
        import wave
        os.makedirs(DIR_FALLOS, exist_ok=True)
        ruta = os.path.join(DIR_FALLOS, datetime.now().strftime("fallo_%Y%m%d_%H%M%S.wav"))
        pcm = (np.clip(muestras, -1.0, 1.0) * 32767).astype(np.int16)
        with wave.open(ruta, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(FS)
            w.writeframes(pcm.tobytes())
        viejos = sorted(f for f in os.listdir(DIR_FALLOS) if f.startswith("fallo_"))
        for f in viejos[:-MAX_FALLOS]:
            os.remove(os.path.join(DIR_FALLOS, f))
        log("DECODIF", f"audio del fallo guardado: {ruta}")
    except Exception as e:
        log("DECODIF", f"no se pudo guardar el audio del fallo: {e}")


SEG_TRAMO = 25          # símbolos por tramo de seguimiento
SEG_RANGO = 120         # corrimiento máximo entre tramos (muestras)
REJILLA = 20            # resolución de la tabla de energías (muestras)


def _tabla_energias(muestras):
    """Energía en 1200 y 2200 Hz de una ventana de un símbolo que empieza en
    cada múltiplo de REJILLA muestras. Se calcula una sola vez por captura y
    todos los intentos de sincronía leen de aquí."""
    spb = SAMPLES_POR_SIMBOLO
    n = (len(muestras) - spb) // REJILLA + 1
    if n <= 0:
        return np.zeros(0, np.float32), np.zeros(0, np.float32)
    V = np.lib.stride_tricks.sliding_window_view(muestras, spb)[::REJILLA]
    e_m = np.empty(n, np.float32); e_s = np.empty(n, np.float32)
    n2 = float(spb * spb)
    for a in range(0, n, 2000):
        B = np.ascontiguousarray(V[a:a + 2000])
        e_m[a:a + len(B)] = ((B @ _GCOS[FREQ_MARK]) ** 2 + (B @ _GSIN[FREQ_MARK]) ** 2) / n2
        e_s[a:a + len(B)] = ((B @ _GCOS[FREQ_SPACE]) ** 2 + (B @ _GSIN[FREQ_SPACE]) ** 2) / n2
    return e_m, e_s


def _niveles_ref(t_m, t_s, idx_ref):
    return (max(float(np.percentile(t_m[idx_ref], 75)), 1e-12),
            max(float(np.percentile(t_s[idx_ref], 75)), 1e-12))


def _bits_con_seguimiento(t_m, t_s, offset, alfa):
    """Decide bits reajustando el punto de muestreo en cada tramo de SEG_TRAMO
    símbolos: elige el corrimiento (±SEG_RANGO respecto del tramo anterior) con
    mayor contraste medio |E1200-E2200|/(E1200+E2200), con niveles emparejados."""
    spb = SAMPLES_POR_SIMBOLO
    a_ref, b_ref = BLOQUES_REF_PREAMBULO
    n_tabla = len(t_m)
    n_total = ((n_tabla - 1) * REJILLA - offset - SEG_RANGO) // spb - 2
    if n_total <= b_ref:
        return None
    ref_m, ref_s = _niveles_ref(t_m, t_s, (offset + np.arange(a_ref, b_ref) * spb) // REJILLA)
    cand = np.arange(-SEG_RANGO, SEG_RANGO + 1, REJILLA)
    bits, d, prev_m, prev_s = [], 0, 0.0, 0.0
    for k0 in range(0, n_total, SEG_TRAMO):
        k = np.arange(k0, min(k0 + SEG_TRAMO, n_total))
        pos = offset + (d + cand)[:, None] + (k * spb)[None, :]
        validos = (pos[:, 0] >= 0) & (pos[:, -1] // REJILLA < n_tabla)
        if not validos.any():
            break
        idx = pos[validos] // REJILLA
        e_m = t_m[idx] / ref_m; e_s = t_s[idx] / ref_s
        c = np.mean(np.abs(e_m - e_s) / (e_m + e_s + 1e-12), axis=1)
        j = int(np.argmax(c))
        d = int((d + cand)[validos][j])
        e_m, e_s = e_m[j], e_s[j]
        m, s = e_m.copy(), e_s.copy()
        if alfa:
            m -= alfa * np.concatenate(([prev_m], e_m[:-1]))
            s -= alfa * np.concatenate(([prev_s], e_s[:-1]))
        prev_m, prev_s = float(e_m[-1]), float(e_s[-1])
        bits.extend((m > s).astype(int).tolist())
    return bits


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
    mejor_texto, mejor_ok = None, False
    a_ref, b_ref = BLOQUES_REF_PREAMBULO
    t_m, t_s = _tabla_energias(muestras)
    n_tabla = len(t_m)

    for ppm in PPM_BUSQUEDA:
        paso = spb / (1 + ppm * 1e-6)   # símbolo del emisor en muestras del receptor
        for off_idx in range(N_OFFSETS):
            offset = (off_idx * spb) // N_OFFSETS
            nb = int(((n_tabla - 1) * REJILLA - offset) // paso) + 1
            if nb <= b_ref:
                continue
            idx = np.rint((offset + np.arange(nb) * paso) / REJILLA).astype(np.int64)
            idx = idx[idx < n_tabla]
            e_m, e_s = t_m[idx], t_s[idx]
            if NORMALIZAR_CON_PREAMBULO:
                e_m = e_m / max(np.percentile(e_m[a_ref:b_ref], 75), 1e-12)
                e_s = e_s / max(np.percentile(e_s[a_ref:b_ref], 75), 1e-12)
            for alfa in ALFAS_COLA:
                m, s = e_m.copy(), e_s.copy()
                if alfa:
                    m[1:] -= alfa * e_m[:-1]
                    s[1:] -= alfa * e_s[:-1]
                bits = (m > s).astype(int).tolist()

                texto, ok = decodificar_paquete(bits)
                if texto and ok:
                    log("DECODIF", f"✓ offset={offset} ppm={ppm} cola={alfa} → CRC OK")
                    return texto, True
                if texto and mejor_texto is None:
                    mejor_texto = texto   # candidato sin CRC por si no hay mejor

    # Segundo intento: seguir la sincronía tramo a tramo (el reloj del emisor
    # no se corre a ritmo constante — medido 31/10/2026).
    for off_idx in range(N_OFFSETS):
        offset = (off_idx * spb) // N_OFFSETS
        for alfa in ALFAS_COLA:
            bits = _bits_con_seguimiento(t_m, t_s, offset, alfa)
            if bits is None:
                continue
            texto, ok = decodificar_paquete(bits)
            if texto and ok:
                log("DECODIF", f"✓ seguimiento offset={offset} cola={alfa} → CRC OK")
                return texto, True
            if texto and mejor_texto is None:
                mejor_texto = texto

    guardar_fallo(muestras)
    if mejor_texto:
        log("DECODIF", "⚠️  Ninguna sincronía dio CRC OK — usando mejor candidato")
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
    MAX_MUESTRAS = FS * 17                   # 17s máximo de captura
    PREAMBULO_MIN_BLOQUES = 25               # 0.5s de energía continua (antes 15)

    energia_count = 0
    en_recepcion = False
    chunks_rx = []   # lista de arrays numpy — se concatenan al final
    bloques_silencio = 0

    with ArecordStream(DISPOSITIVO_ENTRADA, FS, CANALES_ENTRADA, spb) as stream:

        contador = 0
        while True:
            bloque, _ = stream.read(spb)
            muestras = bloque[:, 0]  # canal 0 (mic mono en entrada L)
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
                                    f"Probando sincronías...")
                    muestras_rx = np.concatenate(chunks_rx)
                    texto, ok = decodificar_con_offset(muestras_rx)
                    if texto:
                        return texto, ok
                    log("DECODIF", "No se encontró paquete válido. Volviendo a escuchar.")
                    registrar("SIN_PAQUETE")
                    en_recepcion = False
                    chunks_rx = []
                    energia_count = 0
                    bloques_silencio = 0

                elif n_muestras > MAX_MUESTRAS:
                    log("DECODIF", "Límite de captura alcanzado. Probando sincronías...")
                    muestras_rx = np.concatenate(chunks_rx)
                    texto, ok = decodificar_con_offset(muestras_rx)
                    if texto:
                        return texto, ok
                    log("DECODIF", "No se encontró paquete válido. Reiniciando.")
                    registrar("SIN_PAQUETE")
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

    import random as _random

    if not texto.strip():
        # Sin payload útil: usar pool completo para no interrumpir el rollo
        pool = list(frases_pool)
        modo = "POOL"
    elif _random.random() < PROB_REPETIR_FRASE:
        # MODO REPETICIÓN (25%): todas las transformaciones usan la misma
        # frase recibida — variaciones tipográficas sobre un único tema.
        pool = [texto]
        modo = "REPETICIÓN"
    else:
        # MODO POOL (75%): cada transformación usa una frase distinta.
        # La frase recibida se inyecta en la posición actual del pool
        # para que aparezca como protagonista en la primera transformación.
        pool = list(frases_pool)
        pos = estado_berenguer["indice_frase"] % len(pool)
        pool[pos] = texto
        modo = "POOL"

    log("BERENGUER", f"Modo visual: {modo} ({int(PROB_REPETIR_FRASE*100)}% rep / "
                     f"{int((1-PROB_REPETIR_FRASE)*100)}% pool)")

    img, nuevo_idx, nuevo_off = generar_segmento_secuencial(
        pool,
        indice_frase=estado_berenguer["indice_frase"],
        offset=0,
    )

    # Guardar PNG
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    n = estado_berenguer.get("segmento", 0)
    png_path = os.path.join(OUTPUT_DIR, f"berenguer_{n:04d}.png")
    img.save(png_path)
    log("BERENGUER", f"Imagen generada: {png_path}")

    # ── Display receptor: bloque Berenguer ──
    idx_trans = estado_berenguer["indice_frase"] % 10
    print('[BERENGUER_START]', flush=True)
    for linea in berenguer_a_texto(texto, idx_trans):
        print(linea, flush=True)
    print('[BERENGUER_END]', flush=True)
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

    with ArecordStream(DISPOSITIVO_ENTRADA, FS, CANALES_ENTRADA, spb) as stream:
        for i in range(n_bloques):
            bloque, _ = stream.read(spb)
            muestras = bloque[:, 0]  # canal 0 (mic mono en entrada L)
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
            registrar("CRC_OK" if crc_ok else "CRC_FALLA", texto)

            estado_berenguer = procesar_texto(texto, frases_pool, estado_berenguer)
            guardar_estado_poema(estado_berenguer, ESTADO_BERENGUER)

    except KeyboardInterrupt:
        print("\nReceptor detenido. Estado guardado.")
