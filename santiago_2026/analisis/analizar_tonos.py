#!/usr/bin/env python3
"""Analiza la grabación de tonos_prueba.wav en el receptor.
Uso: python3 analizar_tonos.py grabacion.wav
Busca el inicio del primer tono, y para cada tramo mide energía 1200/2200 (Goertzel 20 ms),
picos del espectro en los tonos largos y la caída de energía tras cada corte (pasos de 5 ms)."""
import sys, wave
import numpy as np
with wave.open(sys.argv[1]) as w:
    fs, nch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
    x = np.frombuffer(w.readframes(n), dtype={2: np.int16, 4: np.int32}[sw]).reshape(-1, nch)[:, 0].astype(np.float64) / 2**(8*sw-1)
print(f"pico={np.abs(x).max():.4f}")

def g(seg, f):
    t = np.arange(len(seg))
    return ((seg @ np.cos(2*np.pi*f*t/fs))**2 + (seg @ np.sin(2*np.pi*f*t/fs))**2) / len(seg)**2

# inicio: primer bloque de 5 ms con energía 1200 > 100x la mediana
h = int(fs * 0.005)
e12 = np.array([g(x[i:i+h*2], 1200) for i in range(0, len(x) - 2*h, h)])
ini = np.argmax(e12 > np.median(e12) * 100) * h
print(f"inicio del primer tono en {ini/fs:.3f}s de la grabación")
x = x[ini:]
db = lambda v: 10 * np.log10(max(v, 1e-15))

def tramo(nombre, a, b):
    seg = x[int(a*fs):int(b*fs)]
    spb = fs // 50; nb = len(seg) // spb
    m = [g(seg[k*spb:(k+1)*spb], 1200) for k in range(nb)]
    s = [g(seg[k*spb:(k+1)*spb], 2200) for k in range(nb)]
    print(f"{nombre:28s} E1200={db(np.median(m)):6.1f} dB  E2200={db(np.median(s)):6.1f} dB  "
          f"(1200-2200 = {db(np.median(m))-db(np.median(s)):+.1f} dB)")
    return seg

tramo("tono 1200 (0.2-1.8)", 0.2, 1.8)
tramo("silencio (2.1-2.4)", 2.1, 2.4)
tramo("tono 2200 (2.7-4.3)", 2.7, 4.3)
tramo("silencio (4.6-4.9)", 4.6, 4.9)

for nombre, a in (("tono 1200 largo", 0.2), ("tono 2200 largo", 2.7)):
    seg = x[int(a*fs):int((a+1.6)*fs)]
    sp = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))); f = np.fft.rfftfreq(len(seg), 1/fs)
    sp = 20 * np.log10(sp / sp.max() + 1e-12)
    picos = [i for i in range(1, len(sp)-1) if sp[i] > sp[i-1] and sp[i] >= sp[i+1] and sp[i] > -50 and 50 < f[i] < 10000]
    picos = sorted(picos, key=lambda i: -sp[i])[:8]
    print(f"{nombre}: picos (Hz, dB rel.) " + "  ".join(f"{f[i]:.0f}:{sp[i]:.0f}" for i in sorted(picos)))

# caída tras cada corte (tiempos relativos al inicio del primer tono)
for nombre, tc, f in (("corte 1200 @2.0s", 2.0, 1200), ("corte 2200 @4.5s", 4.5, 2200),
                      ("corte 2200 @5.5s", 5.5, 2200), ("corte 1200 @6.5s", 6.5, 1200)):
    ref = g(x[int((tc-0.1)*fs):int((tc-0.1)*fs)+2*h], f)
    vals = [db(g(x[int(tc*fs)+k*h - h:int(tc*fs)+k*h + h], f)) - db(ref) for k in range(0, 9)]
    print(f"{nombre}: dB vs tono, cada 5 ms desde el corte: " + " ".join(f"{v:6.1f}" for v in vals))
