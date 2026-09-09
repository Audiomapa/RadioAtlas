#!/usr/bin/env python3
"""
=====================================================================
TRANSMISOR FSK — POEMA VISUAL BERENGUER
=====================================================================
Envía el texto del poema como tonos de audio FSK (Frequency Shift
Keying) en vez de imágenes SSTV. Ventajas sobre SSTV:
- El texto sobrevive mucho mejor el canal acústico ruidoso
- Si llegan algunos errores, el receptor igual puede leer el texto
- El Método Berenguer se aplica en el receptor — lo que viaja por
  el aire es el texto crudo, la imagen emerge en la recepción

Protocolo:
  [PREÁMBULO 1s] [SYNC 2bytes] [LEN 2bytes] [CRC 2bytes] [TEXTO]

Parámetros FSK:
  Mark  (bit 1): 1200 Hz
  Space (bit 0): 2200 Hz
  Baud rate:     50 baudios (20ms/símbolo)
  Separación:    1000 Hz — muy distinguible con ruido de sala

Uso:
  python3 transmisor_fsk.py           # bucle continuo
  python3 transmisor_fsk.py --prueba  # un solo envío
  python3 transmisor_fsk.py --texto "Frase de prueba"
=====================================================================
"""

import argparse
import json
import os
import shutil
import struct
import subprocess
import time
from datetime import datetime, time as dtime

import numpy as np
from scipy.io import wavfile

# =====================================================================
# CONFIGURACIÓN
# =====================================================================
FREQ_MARK  = 1200   # Hz — bit 1
FREQ_SPACE = 2200   # Hz — bit 0
FS         = 44100  # Hz — sample rate (debe coincidir con el receptor)
BAUDRATE   = 50     # símbolos/segundo — 20ms por bit
SAMPLES_POR_SIMBOLO = int(FS / BAUDRATE)  # 882 muestras por bit

SYNC_WORD  = b'\xAB\xCD'   # 2 bytes de sync al inicio de cada mensaje
SILENCIO_ENTRE_ENVIOS_SEG = 100  # pausa entre transmisiones

FRASES_ARCHIVO = "frases.txt"
ESTADO_ARCHIVO = "estado_fsk.json"
OUTPUT_DIR     = "fsk_output_berenguer"

HORA_INICIO = dtime(9, 0)
HORA_FIN    = dtime(21, 0)
REPRODUCTOR = None   # None = detecta paplay/aplay automáticamente


# =====================================================================
# FRASES
# =====================================================================
def cargar_frases(ruta=FRASES_ARCHIVO):
    if os.path.exists(ruta):
        with open(ruta, encoding="utf-8") as f:
            frases = [l.strip() for l in f if l.strip()]
        if frases:
            return frases
    return ["El atávico sueño humano de las totalidades"]


def cargar_estado():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    if os.path.exists(ESTADO_ARCHIVO):
        with open(ESTADO_ARCHIVO) as f:
            return json.load(f)
    return {"indice_frase": 0, "envio": 0}


def guardar_estado(estado):
    with open(ESTADO_ARCHIVO, "w") as f:
        json.dump(estado, f, indent=2)


# =====================================================================
# MODULACIÓN FSK
# =====================================================================
def _tono(freq, n_muestras, fs=FS, fase=0.0):
    """Genera n_muestras de un tono puro a freq Hz."""
    t = np.arange(n_muestras) / fs
    return np.sin(2 * np.pi * freq * t + fase).astype(np.float32)


def _crc16(data: bytes) -> int:
    """CRC-16/CCITT para verificar integridad."""
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


def texto_a_audio(texto: str) -> np.ndarray:
    """
    Convierte un texto en audio FSK listo para reproducir.

    Estructura del paquete:
    - Preámbulo: 1s de tonos alternantes mark/space (permite al receptor
      calibrar el nivel y sincronizar la fase antes del mensaje real)
    - Sync: 2 bytes 0xAB 0xCD (marcador de inicio)
    - Longitud: 2 bytes (uint16 big-endian, longitud del texto en bytes)
    - CRC: 2 bytes del CRC-16 del payload
    - Payload: texto en UTF-8
    - Cola: 0.5s de silencio
    """
    payload = texto.encode("utf-8")
    crc = _crc16(payload)
    header = SYNC_WORD + struct.pack(">HH", len(payload), crc)
    paquete = header + payload

    muestras = []

    # Preámbulo: 2s de alternancia mark/space — margen extra para el detector
    # de ventana deslizante del receptor en canal acústico con bloques perdidos
    n_preamb = int(FS * 2.0)
    spb = SAMPLES_POR_SIMBOLO
    for i in range(n_preamb // spb):
        freq = FREQ_MARK if i % 2 == 0 else FREQ_SPACE
        muestras.append(_tono(freq, spb))

    # Datos bit a bit (MSB primero en cada byte)
    for byte in paquete:
        for bit_pos in range(7, -1, -1):
            bit = (byte >> bit_pos) & 1
            freq = FREQ_MARK if bit else FREQ_SPACE
            muestras.append(_tono(freq, spb))

    # Cola: silencio
    muestras.append(np.zeros(int(FS * 0.5), dtype=np.float32))

    audio = np.concatenate(muestras)
    # Normalizar a 80% de escala para no saturar
    audio = audio * 0.8
    return audio


# =====================================================================
# GENERACIÓN DE FRASES NUEVAS — DERIVA + CORTE
# =====================================================================
#
# Dos métodos de generación conviven con las frases literales del corpus:
#
#   DERIVA  — cadena de Markov orden 1: una palabra arrastra a la siguiente
#             según cómo se asocian en el poema original. El resultado
#             suena al corpus pero nunca existió en él.
#             Ejemplo: "mutados por una trama de instrucciones por segundo"
#
#   CORTE   — cut-up (Burroughs): primera mitad de una frase + segunda
#             mitad de otra distinta. Más radical, más rupturas.
#             Ejemplo: "un adentro sin afuera — que extraen los biomas"
#
# Probabilidades configurables — deben sumar 1.0:
PROB_LITERAL = 0.60   # frases del corpus tal cual
PROB_DERIVA  = 0.30   # generadas por Markov
PROB_CORTE   = 0.10   # generadas por cut-up


def construir_transiciones(frases):
    """
    Construye tabla de bigrams del corpus: para cada palabra,
    lista de palabras que la siguen en el texto original.
    Se llama una sola vez al arrancar.
    """
    palabras = []
    for frase in frases:
        palabras.extend(frase.split())
    tabla = {}
    for i in range(len(palabras) - 1):
        clave = palabras[i].lower()
        if clave not in tabla:
            tabla[clave] = []
        tabla[clave].append(palabras[i + 1])
    return tabla


def generar_deriva(frases, transiciones, min_pal=4, max_pal=9):
    """
    Genera una frase nueva siguiendo una cadena de Markov sobre el corpus.
    Arranca desde una palabra al azar (preferiblemente de mas de 4 letras,
    mas carga semantica) y sigue las transiciones del bigram.
    Si una palabra no tiene continuacion en la tabla, elige al azar del corpus.
    """
    todas = [p for f in frases for p in f.split()]
    candidatos = [p for p in transiciones if len(p) > 4]
    if not candidatos:
        candidatos = list(transiciones.keys())
    palabra = random.choice(candidatos)
    resultado = [palabra.capitalize()]
    for _ in range(random.randint(min_pal, max_pal) - 1):
        clave = palabra.lower()
        if clave in transiciones and transiciones[clave]:
            palabra = random.choice(transiciones[clave])
        else:
            palabra = random.choice(todas)
        resultado.append(palabra)
    return " ".join(resultado)


def generar_corte(frases):
    """
    Genera una frase nueva por cut-up: primera mitad de una frase +
    segunda mitad de otra distinta.
    Tecnica inspirada en Burroughs y en el espiritu de Berenguer.
    """
    if len(frases) < 2:
        return random.choice(frases)
    f1, f2 = random.sample(frases, 2)
    p1, p2 = f1.split(), f2.split()
    nueva = p1[: len(p1) // 2] + p2[len(p2) // 2 :]
    return " ".join(nueva) if nueva else random.choice(frases)


def elegir_frase(frases, transiciones, indice):
    """
    Elige la frase a transmitir segun las probabilidades configuradas.
    Devuelve (frase, tipo) donde tipo es 'LITERAL', 'DERIVA' o 'CORTE'.
    """
    r = random.random()
    if r < PROB_LITERAL:
        return frases[indice % len(frases)], "LITERAL"
    elif r < PROB_LITERAL + PROB_DERIVA:
        return generar_deriva(frases, transiciones), "DERIVA"
    else:
        return generar_corte(frases), "CORTE"


# =====================================================================
# REPRODUCCIÓN
# =====================================================================
def reproducir(audio: np.ndarray, ruta_wav: str):
    """Guarda el audio como WAV y lo reproduce."""
    audio_int = (audio * 32767).astype(np.int16)
    wavfile.write(ruta_wav, FS, audio_int)

    reproductor = REPRODUCTOR or shutil.which("paplay") or shutil.which("aplay")
    if not reproductor:
        raise RuntimeError("No se encontró paplay ni aplay.")
    print(f"[Audio] Reproduciendo con {reproductor} ({len(audio)/FS:.1f}s)")
    subprocess.run([reproductor, ruta_wav], check=True)


def enviar_frase(texto: str, numero: int, tipo: str = "LITERAL"):
    """Codifica y envía una frase como audio FSK."""
    print(f"\n[ENVÍO #{numero}] [{tipo}] '{texto[:60]}{'...' if len(texto)>60 else ''}'")
    audio = texto_a_audio(texto)
    duracion = len(audio) / FS
    print(f"[FSK] {len(texto.encode())} bytes → {duracion:.1f}s de audio "
          f"({BAUDRATE} baud, mark={FREQ_MARK}Hz, space={FREQ_SPACE}Hz)")
    ruta_wav = os.path.join(OUTPUT_DIR, "fsk_sonoro.wav")
    reproducir(audio, ruta_wav)
    print("[FSK] Transmisión completada.")


# =====================================================================
# VENTANA HORARIA
# =====================================================================
def dentro_de_ventana():
    ahora = datetime.now().time()
    return HORA_INICIO <= ahora <= HORA_FIN


def esperar_ventana():
    if dentro_de_ventana():
        return
    print(f"[{datetime.now().strftime('%H:%M')}] Fuera de ventana "
          f"({HORA_INICIO.strftime('%H:%M')}–{HORA_FIN.strftime('%H:%M')}). Esperando...")
    while not dentro_de_ventana():
        time.sleep(30)


# =====================================================================
# MAIN
# =====================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prueba", action="store_true",
                        help="Envía una sola frase sin tocar el estado guardado.")
    parser.add_argument("--texto", type=str, default=None,
                        help="Envía este texto específico en vez de las frases.")
    parser.add_argument("--reset", action="store_true",
                        help="Reinicia el estado desde el principio.")
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    frases = cargar_frases()
    transiciones = construir_transiciones(frases)
    print(f"=== TRANSMISOR FSK — POEMA VISUAL BERENGUER ===")
    print(f"frases: {len(frases)} | {BAUDRATE} baud | "
          f"mark={FREQ_MARK}Hz  space={FREQ_SPACE}Hz  sep=1000Hz")
    print(f"generacion: {int(PROB_LITERAL*100)}% literal | "
          f"{int(PROB_DERIVA*100)}% deriva | {int(PROB_CORTE*100)}% corte")

    if args.reset:
        if os.path.exists(ESTADO_ARCHIVO):
            os.remove(ESTADO_ARCHIVO)
        print("Estado reiniciado.")

    if args.texto:
        enviar_frase(args.texto, 0, tipo="MANUAL")
        raise SystemExit(0)

    if args.prueba:
        estado = cargar_estado()
        frase, tipo = elegir_frase(frases, transiciones, estado["indice_frase"])
        enviar_frase(frase, estado["envio"], tipo=tipo)
        print("✅ Prueba completada. Estado no modificado.")
        raise SystemExit(0)

    estado = cargar_estado()
    print(f"Retomando en frase {estado['indice_frase']}, envío #{estado['envio']}\n")

    try:
        while True:
            esperar_ventana()
            frase, tipo = elegir_frase(frases, transiciones, estado["indice_frase"])
            enviar_frase(frase, estado["envio"], tipo=tipo)
            estado["indice_frase"] += 1
            estado["envio"] += 1
            guardar_estado(estado)
            print(f"Esperando {SILENCIO_ENTRE_ENVIOS_SEG}s...")
            time.sleep(SILENCIO_ENTRE_ENVIOS_SEG)
    except KeyboardInterrupt:
        print("\nDetenido. Estado guardado.")
