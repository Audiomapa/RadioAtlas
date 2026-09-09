#!/usr/bin/env python3
"""
=====================================================================
TRANSMISOR SSTV — POEMA VISUAL BERENGUER (Raspberry Pi)
=====================================================================
Genera imágenes de poesía visual con el Método Berenguer
(metodo_berenguer.py + frases.txt) y las transmite en bucle
por SSTV (Martin M1), avanzando de forma continua y sin repetición
de diagramación.

El estado del poema (qué frase va, en qué punto del corte) se
guarda en estado_poema.json — si el proceso se reinicia retoma
exactamente donde estaba, sin cortar el poema.

Uso:
    python3 transmisor_berenguer.py           # bucle completo
    python3 transmisor_berenguer.py --prueba  # un solo ciclo, sin tocar estado

Requisitos (mismos que transmisor_rio.py más Pillow):
    pip install Pillow pysstv scipy
=====================================================================
"""

import os
import json
import time
import shutil
import argparse
import subprocess
from datetime import datetime, time as dtime

from PIL import Image
from pysstv.color import MartinM1
from scipy.io import wavfile

# Berenguer vive al lado de este script (o en la misma carpeta)
from metodo_berenguer import (
    cargar_frases,
    generar_segmento_secuencial,
    ANCHO,
    ALTO_SEGMENTO_SSTV,
)

# =====================================================================
# CONFIGURACIÓN — lo único que normalmente hay que tocar
# =====================================================================
FRASES_ARCHIVO = "frases.txt"     # frases base del poema visual

DIAS_TOTALES = 3                  # duración total de la instalación en días
HORAS_POR_DIA = 6                 # horas de operación por día
HORA_INICIO = dtime(00, 0)         # inicio de la ventana horaria diaria
HORA_FIN = dtime(21, 0)           # fin de la ventana horaria diaria

# Silencio DESPUÉS de que termina de sonar el audio — tiempo que el receptor
# necesita para terminar de capturar + decodificar + imprimir antes de
# volver a estar escuchando. Debe ser ≥ ~70s para no desfasar el pipeline.
SILENCIO_ENTRE_CICLOS_SEG = 100

# Reproductor de audio (None = detecta paplay/aplay automáticamente)
REPRODUCTOR_AUDIO = None
VOLUMEN = 1.0                     # 1.0 = volumen normal, 2.0 = el doble

OUTPUT_DIR = "sstv_output_berenguer"
ESTADO_POEMA = os.path.join(OUTPUT_DIR, "estado_poema.json")

# =====================================================================
# ESTADO — carga / guarda el punto del poema donde quedó
# =====================================================================
def cargar_estado():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    if os.path.exists(ESTADO_POEMA):
        with open(ESTADO_POEMA) as f:
            return json.load(f)
    return {"indice_frase": 0, "offset": 0, "segmento": 0}


def guardar_estado(estado):
    with open(ESTADO_POEMA, "w") as f:
        json.dump(estado, f, indent=2)


# =====================================================================
# PIPELINE: poema → imagen → SSTV → audio → transmisión
# =====================================================================
def generar_imagen_poema(frases, estado):
    """Genera el segmento siguiente del poema visual (320x256) y lo guarda."""
    print("[1/4] Generando segmento de poema visual (Método Berenguer)...")
    try:
        img, nuevo_idx, nuevo_off = generar_segmento_secuencial(
            frases,
            indice_frase=estado["indice_frase"],
            offset=estado["offset"],
        )
        # Guardar PNG del segmento (registro visual, no el que se procesa para SSTV)
        n = estado.get("segmento", 0)
        png_path = os.path.join(OUTPUT_DIR, f"poema_{n:04d}.png")
        img.save(png_path)
        print(f"       Segmento {n} guardado: {png_path} "
              f"(frase {nuevo_idx}, offset {nuevo_off}px)")
        return img, nuevo_idx, nuevo_off
    except Exception as e:
        print(f"Error al generar imagen del poema: {e}")
        return None, None, None


def procesar_para_sstv(img):
    """Redimensiona a 320x256 si hace falta y convierte a RGB."""
    print("[2/4] Adaptando dimensiones a 320x256...")
    try:
        img_proc = img.convert("RGB").resize((320, 256), Image.Resampling.LANCZOS)
        proc_path = os.path.join(OUTPUT_DIR, "poema_procesado.png")
        img_proc.save(proc_path)
        return proc_path
    except Exception as e:
        print(f"Error al procesar la imagen: {e}")
        return None


def generar_audio_sstv(imagen_path):
    """Codifica la imagen como audio Martin M1."""
    print("[3/4] Codificando en frecuencias SSTV (Martin M1)...")
    try:
        img = Image.open(imagen_path)
        sstv = MartinM1(img, 44100, 16)
        audio_path = os.path.join(OUTPUT_DIR, "poema_sonoro.wav")
        sstv.write_wav(audio_path)
        return audio_path
    except Exception as e:
        print(f"Error en codificación SSTV: {e}")
        return None


def reproducir_audio(archivo_audio):
    """Reproduce el WAV por la salida de audio del sistema."""
    print("[4/4] 🔊 TRANSMITIENDO...")
    try:
        _, datos = wavfile.read(archivo_audio)
        pico = float(abs(datos).max())
        print(f"[Audio] Amplitud pico: {pico:.0f} "
              f"({'⚠️ parece silencio' if pico < 100 else 'OK'})")

        reproductor = REPRODUCTOR_AUDIO or shutil.which("paplay") or shutil.which("aplay")
        if reproductor is None:
            raise RuntimeError(
                "No se encontró 'paplay' ni 'aplay'. "
                "Instala con: sudo apt install pulseaudio-utils alsa-utils"
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


def ejecutar_ciclo(frases, estado):
    """Genera un segmento del poema → codifica → transmite. Devuelve el nuevo estado."""
    img, nuevo_idx, nuevo_off = generar_imagen_poema(frases, estado)
    if img is None:
        return estado

    proc_path = procesar_para_sstv(img)
    if proc_path is None:
        return estado

    audio_path = generar_audio_sstv(proc_path)
    if audio_path is None:
        return estado

    reproducir_audio(audio_path)

    return {
        "indice_frase": nuevo_idx,
        "offset": nuevo_off,
        "segmento": estado.get("segmento", 0) + 1,
    }


# =====================================================================
# VENTANA HORARIA — igual que en transmisor_rio.py
# =====================================================================
def dentro_de_ventana():
    ahora = datetime.now().time()
    return HORA_INICIO <= ahora <= HORA_FIN


def esperar_hasta_ventana():
    if dentro_de_ventana():
        return
    ahora = datetime.now()
    print(f"[{ahora.strftime('%H:%M')}] Fuera de ventana horaria "
          f"({HORA_INICIO.strftime('%H:%M')}–{HORA_FIN.strftime('%H:%M')}). "
          f"Esperando...")
    while not dentro_de_ventana():
        time.sleep(30)
    print(f"[{datetime.now().strftime('%H:%M')}] Ventana activa. Comenzando transmisión.")


# =====================================================================
# MODO DE PRUEBA — un solo ciclo sin tocar el estado guardado
# =====================================================================
def correr_prueba(frases):
    print("\n=== MODO PRUEBA — un solo ciclo sin afectar el estado guardado ===")
    estado_prueba = cargar_estado()
    print(f"Estado actual: segmento {estado_prueba.get('segmento',0)}, "
          f"frase {estado_prueba['indice_frase']}, offset {estado_prueba['offset']}px\n")
    ejecutar_ciclo(frases, estado_prueba)
    print("\n✅ Prueba completada. El estado guardado no fue modificado.")


# =====================================================================
# BUCLE PRINCIPAL
# =====================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Transmisor SSTV del poema visual Berenguer."
    )
    parser.add_argument(
        "--prueba", action="store_true",
        help="Corre un solo ciclo de inmediato sin tocar el estado guardado."
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="Reinicia el poema desde el principio (borra estado_poema.json)."
    )
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    frases = cargar_frases(FRASES_ARCHIVO)
    print(f"📖 {len(frases)} frases cargadas desde '{FRASES_ARCHIVO}'")

    if args.reset:
        if os.path.exists(ESTADO_POEMA):
            os.remove(ESTADO_POEMA)
            print("✅ Estado reiniciado. El poema arranca desde el principio.")
        else:
            print("No había estado guardado, arrancando de cero igual.")

    if args.prueba:
        correr_prueba(frases)
        raise SystemExit(0)

    print("=== TRANSMISOR SSTV — POEMA VISUAL BERENGUER ===")

    estado = cargar_estado()
    print(f"Retomando en segmento {estado.get('segmento', 0)} "
          f"(frase {estado['indice_frase']}, offset {estado['offset']}px)\n")

    try:
        while True:
            esperar_hasta_ventana()

            n = estado.get("segmento", 0)
            print(f"\n--- Segmento {n} "
                  f"[frase {estado['indice_frase']}, offset {estado['offset']}px] ---")

            estado = ejecutar_ciclo(frases, estado)
            guardar_estado(estado)

            print(f"Esperando {SILENCIO_ENTRE_CICLOS_SEG} segundos de silencio...")
            time.sleep(SILENCIO_ENTRE_CICLOS_SEG)

    except KeyboardInterrupt:
        print("\nInstalación detenida manualmente. El progreso quedó guardado.")
