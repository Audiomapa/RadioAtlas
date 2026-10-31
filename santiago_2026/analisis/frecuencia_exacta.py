#!/usr/bin/env python3
"""Frecuencia exacta de los tonos largos de rec_tonos.wav por pendiente de fase.
Uso: python3 frecuencia_exacta.py rec_tonos.wav  (mismo alineado que analizar_tonos.py)"""
import sys, wave
import numpy as np
with wave.open(sys.argv[1]) as w:
    fs, nch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
    x = np.frombuffer(w.readframes(n), dtype={2: np.int16, 4: np.int32}[sw]).reshape(-1, nch)[:, 0].astype(np.float64)
h = int(fs * 0.005)
g = lambda s, f: abs(np.sum(s * np.exp(-2j*np.pi*f*np.arange(len(s))/fs)))**2
e12 = np.array([g(x[i:i+2*h], 1200) for i in range(0, len(x)-2*h, h)])
x = x[np.argmax(e12 > np.median(e12)*100)*h:]
for f0, a in ((1200, 0.2), (2200, 2.7)):
    seg = x[int(a*fs):int((a+1.6)*fs)]
    L = int(0.05*fs); ph = []
    for k in range(0, len(seg)-L, L):
        b = seg[k:k+L]; ph.append(np.angle(np.sum(b*np.exp(-2j*np.pi*f0*(np.arange(L)+k)/fs))))
    ph = np.unwrap(ph); t = np.arange(len(ph))*L/fs
    df = np.polyfit(t, ph, 1)[0] / (2*np.pi)
    print(f"tono {f0} Hz: medido {f0+df:.3f} Hz  -> desvío {1e6*df/f0:+.0f} ppm")
