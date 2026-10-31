#!/usr/bin/env python3
"""FFT de un WAV: amplitud en 1200/2200 Hz y en +/-25 Hz alrededor.
Uso: python3 fft_bandas.py archivo.wav [canal]
Solo numpy + wave. Analiza solo el tramo con señal (bloques de 20 ms sobre el umbral)."""
import sys, wave
import numpy as np

ruta = sys.argv[1]
canal = int(sys.argv[2]) if len(sys.argv) > 2 else 0

with wave.open(ruta) as w:
    fs, nch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
    raw = w.readframes(n)
dt = {2: np.int16, 4: np.int32}[sw]
x = np.frombuffer(raw, dtype=dt).astype(np.float64) / (2 ** (8 * sw - 1))
x = x.reshape(-1, nch)[:, canal]

spb = fs // 50
nb = len(x) // spb
rms = np.sqrt(np.mean(x[:nb * spb].reshape(nb, spb) ** 2, axis=1))
umbral = max(rms.max() * 0.1, 1e-6)
activos = np.where(rms > umbral)[0]
print(f"archivo={ruta}  fs={fs}  canales={nch}  bits={8*sw}  dur={len(x)/fs:.2f}s")
print(f"pico abs={np.abs(x).max():.4f}  rms max bloque={rms.max():.4f}")
if len(activos) == 0:
    print("sin señal"); sys.exit()
a, b = activos[0], activos[-1] + 1
seg = x[a * spb:b * spb]
print(f"tramo con señal: bloques {a}-{b-1}  ({len(seg)/fs:.2f}s)")

N = len(seg)
spec = np.abs(np.fft.rfft(seg * np.hanning(N))) * 2 / np.sum(np.hanning(N))
f = np.fft.rfftfreq(N, 1 / fs)

def amp(fc, ancho=3.0):
    m = (f > fc - ancho) & (f < fc + ancho)
    i = np.argmax(spec[m]); return f[m][i], spec[m][i]

f22, a22 = amp(2200); f12, a12 = amp(1200)
for fc in (1175, 1200, 1225, 2175, 2200, 2225):
    fp, ap = amp(fc)
    ref = a22 if fc > 1700 else a12
    print(f"  {fc:5d} Hz -> pico {fp:8.2f} Hz  amp={ap:.6f}  ({100*ap/ref:5.1f}% del tono {'2200' if fc>1700 else '1200'})")
