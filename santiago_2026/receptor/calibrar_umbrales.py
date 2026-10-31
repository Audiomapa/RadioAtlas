#!/usr/bin/env python3
"""
=====================================================================
CALIBRADOR DE UMBRALES — energía y pureza del detector de sync SSTV
=====================================================================
En vez de ajustar UMBRAL_DISPARO/UMBRAL_PUREZA a mano por ensayo y
error, esta herramienta graba las dos métricas (energía en 1200Hz y
pureza tonal) durante distintos escenarios reales, y al final compara
las distribuciones para sugerir umbrales que separen bien "señal real"
de "ruido/voz".

MODO DE USO — dos pasos:

1) Graba cada escenario por separado (usa una etiqueta descriptiva):

   python3 calibrar_umbrales.py senal 60
       (mientras suena la transmisión SSTV real hacia el mic, 60s)

   python3 calibrar_umbrales.py ruido 30
       (mientras conversas/hay ruido ambiente normal de sala, 30s)

   Puedes repetir con más etiquetas si quieres (ej. "senal_lejos",
   "ruido_fuerte") — cada una queda guardada por separado.

2) Compara todo lo grabado y pide una sugerencia de umbrales:

   python3 calibrar_umbrales.py --reporte
=====================================================================
"""

import sys
import csv
import glob
import numpy as np

FS = 44100
FREQ_SYNC = 1200
DISPOSITIVO_ENTRADA = 1
CANALES_ENTRADA = 2


def energia_goertzel(muestras, freq, fs):
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
    energia_total = float(np.mean(np.asarray(muestras, dtype=np.float64) ** 2))
    if energia_total <= 1e-12:
        return 0.0
    return energia_1200hz / energia_total


def grabar_escenario(etiqueta, duracion_seg):
    import sounddevice as sd

    bloque = int(FS * 0.010)
    n_bloques = int(duracion_seg / 0.010)
    archivo = f"calibracion_{etiqueta}.csv"

    print(f"=== Grabando escenario '{etiqueta}' por {duracion_seg}s ({n_bloques} bloques) ===")
    print("Empieza AHORA con el audio/situación real de este escenario.")

    filas = []
    with sd.InputStream(samplerate=FS, channels=CANALES_ENTRADA, dtype="float32",
                         device=DISPOSITIVO_ENTRADA) as stream:
        for i in range(n_bloques):
            datos, _ = stream.read(bloque)
            muestras = datos.mean(axis=1) if CANALES_ENTRADA > 1 else datos[:, 0]
            e = energia_goertzel(muestras, FREQ_SYNC, FS)
            p = pureza_tonal(muestras, e)
            filas.append((e, p))
            if i % 100 == 0:
                print(f"  {i//100}s...  energía={e:.6f}  pureza={p:.3f}")

    with open(archivo, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["energia", "pureza"])
        w.writerows(filas)

    energias = [f[0] for f in filas]
    purezas = [f[1] for f in filas]
    print(f"\n✅ Guardado en {archivo}")
    print(f"   energía: máx={max(energias):.6f}  p95={np.percentile(energias,95):.6f}  mediana={np.median(energias):.6f}")
    print(f"   pureza:  máx={max(purezas):.3f}  p95={np.percentile(purezas,95):.3f}  mediana={np.median(purezas):.3f}")


def generar_reporte():
    archivos = glob.glob("calibracion_*.csv")
    if not archivos:
        print("No hay ningún calibracion_*.csv todavía. Graba al menos un escenario primero.")
        return

    datos_por_escenario = {}
    for archivo in archivos:
        etiqueta = archivo.replace("calibracion_", "").replace(".csv", "")
        energias, purezas = [], []
        with open(archivo) as f:
            r = csv.DictReader(f)
            for fila in r:
                energias.append(float(fila["energia"]))
                purezas.append(float(fila["pureza"]))
        datos_por_escenario[etiqueta] = (np.array(energias), np.array(purezas))

    print("=== Resumen por escenario ===")
    for etiqueta, (energias, purezas) in datos_por_escenario.items():
        print(f"\n[{etiqueta}] ({len(energias)} bloques)")
        print(f"  energía: mín={energias.min():.6f}  p50={np.percentile(energias,50):.6f}  "
              f"p95={np.percentile(energias,95):.6f}  máx={energias.max():.6f}")
        print(f"  pureza:  mín={purezas.min():.3f}  p50={np.percentile(purezas,50):.3f}  "
              f"p95={np.percentile(purezas,95):.3f}  máx={purezas.max():.3f}")

    # Heurística: si hay un escenario que contenga "senal" y otro que NO,
    # los trata como "señal real" vs "ruido", y sugiere un umbral que quede
    # entre ambos, con margen.
    etiquetas_senal = [k for k in datos_por_escenario if "senal" in k]
    etiquetas_ruido = [k for k in datos_por_escenario if "senal" not in k]

    if etiquetas_senal and etiquetas_ruido:
        energia_senal = np.concatenate([datos_por_escenario[k][0] for k in etiquetas_senal])
        pureza_senal = np.concatenate([datos_por_escenario[k][1] for k in etiquetas_senal])
        energia_ruido = np.concatenate([datos_por_escenario[k][0] for k in etiquetas_ruido])
        pureza_ruido = np.concatenate([datos_por_escenario[k][1] for k in etiquetas_ruido])

        # Los pulsos de sync ocurren cada ~446ms — en vez de un percentil fijo
        # (que se diluye con ruido de piso si los pulsos son escasos), se
        # estima cuántos bloques de pico esperar según la duración grabada y
        # se toman esos, con margen.
        PERIODO_LINEA_SEG = 0.446
        n_bloques_senal = len(energia_senal)
        duracion_estimada_seg = n_bloques_senal * 0.010
        n_picos_esperados = max(3, int(duracion_estimada_seg / PERIODO_LINEA_SEG))
        n_top = min(n_bloques_senal, n_picos_esperados * 2)  # con margen

        idx_ordenado = np.argsort(energia_senal)[::-1][:n_top]
        umbral_alto_senal = np.percentile(energia_senal[idx_ordenado], 50)
        pureza_en_picos = pureza_senal[idx_ordenado]

        techo_ruido_energia = np.percentile(energia_ruido, 99.5)
        techo_ruido_pureza = np.percentile(pureza_ruido, 99.5)

        print("\n=== SUGERENCIA DE UMBRALES ===")
        if umbral_alto_senal > techo_ruido_energia:
            sugerido_energia = (umbral_alto_senal + techo_ruido_energia) / 2 * 0.7
            print(f"UMBRAL_DISPARO sugerido: {sugerido_energia:.6f}")
            print(f"  (pico real de señal ~{umbral_alto_senal:.6f}, techo de ruido ~{techo_ruido_energia:.6f})")
        else:
            print("⚠️  El pico de energía de la señal real NO supera claramente el techo del ruido.")
            print(f"   señal p99={umbral_alto_senal:.6f} vs ruido p99.5={techo_ruido_energia:.6f}")
            print("   Esto sugiere que hay que mejorar la señal física (acercar mic, subir volumen)")
            print("   antes de que cualquier umbral funcione de forma confiable.")

        if len(pureza_en_picos) > 0 and np.median(pureza_en_picos) > techo_ruido_pureza:
            sugerido_pureza = (np.median(pureza_en_picos) + techo_ruido_pureza) / 2
            print(f"\nUMBRAL_PUREZA sugerido: {sugerido_pureza:.3f}")
            print(f"  (pureza típica en picos de señal real ~{np.median(pureza_en_picos):.3f}, "
                  f"techo de ruido ~{techo_ruido_pureza:.3f})")
        else:
            print("\n⚠️  La pureza de la señal real en sus picos no se separa claramente del ruido.")
    else:
        print("\nPara la sugerencia automática, graba al menos un escenario con 'senal' en el "
              "nombre (ej. 'senal') y otro sin esa palabra (ej. 'ruido', 'voz').")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--reporte":
        generar_reporte()
    elif len(sys.argv) >= 3:
        etiqueta = sys.argv[1]
        duracion = float(sys.argv[2])
        grabar_escenario(etiqueta, duracion)
    else:
        print(__doc__)
