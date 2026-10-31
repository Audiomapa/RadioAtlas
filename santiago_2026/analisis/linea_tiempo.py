#!/usr/bin/env python3
"""Línea de tiempo de tonos con resolución de 5 ms.
Uso: python3 linea_tiempo.py archivo.wav [canal]
Imprime: inicio/fin de actividad, duración de cada racha de tono en el preámbulo
y la duración media del símbolo medida en las transiciones."""
import sys, wave
import numpy as np

ruta = sys.argv[1]; canal = int(sys.argv[2]) if len(sys.argv) > 2 else 0
t0 = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
t1 = float(sys.argv[4]) if len(sys.argv) > 4 else 1e9
with wave.open(ruta) as w:
    fs, nch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
    x = np.frombuffer(w.readframes(n), dtype={2: np.int16, 4: np.int32}[sw]).reshape(-1, nch)[:, canal]
x = x.astype(np.float32) / (2 ** (8 * sw - 1))
x = x[int(t0*fs):int(min(t1*fs, len(x)))]

win = int(fs * 0.010); hop = int(fs * 0.005)          # ventana 10 ms, paso 5 ms
t = np.arange(win)
def e(fr, B):
    c = np.cos(2*np.pi*fr*t/fs); s = np.sin(2*np.pi*fr*t/fs)
    return (B @ c) ** 2 + (B @ s) ** 2
nf = (len(x) - win) // hop
idx = np.arange(win)[None, :] + hop * np.arange(nf)[:, None]
B = x[idx] * np.hanning(win)
em, es = e(1200, B), e(2200, B)
tot = em + es
umbral_act = tot.max() * 1e-2
act = tot > umbral_act
lab = np.where(~act, '.', np.where(em > es, 'M', 'S'))
on = np.where(act)[0]
print(f"{ruta}: fs={fs}  actividad desde {on[0]*hop/fs:.3f}s hasta {on[-1]*hop/fs:.3f}s "
      f"({(on[-1]-on[0])*hop/fs:.2f}s)")
# rachas
rachas = []
cur, ini = lab[on[0]], on[0]
for i in range(on[0] + 1, on[-1] + 1):
    if lab[i] != cur:
        rachas.append((cur, ini, i - ini)); cur, ini = lab[i], i
rachas.append((cur, ini, on[-1] + 1 - ini))
print("primeras 40 rachas (tono, inicio s, duración ms):")
for r in rachas[:40]:
    print(f"  {r[0]} {r[1]*hop/fs:7.3f}  {r[2]*5:4d} ms")
ms = [r[2]*5 for r in rachas if r[0] in 'MS']
print(f"rachas M/S: {len(ms)}  mediana {np.median(ms):.0f} ms")
# texto compacto de 2 s desde el inicio
s = ''.join(lab[on[0]:on[0] + 400])
print("primeros 2 s (1 car = 5 ms):")
for k in range(0, len(s), 100): print("  " + s[k:k+100])
s = ''.join(lab[on[-1]-400:on[-1]+20])
print("últimos 2 s:")
for k in range(0, len(s), 100): print("  " + s[k:k+100])
