#!/usr/bin/env python3
"""Genera tonos_prueba.wav (44100 Hz, 16 bit, mono, amplitud 0.8 igual que el transmisor).
Secuencia (s, 7.5 s en total, cabe en la pausa de 20 s del emisor):
  sil .5 | 1200 2 | sil .5 | 2200 2 | sil .5 | 2200 .5 | sil .5 | 1200 .5 | sil .5"""
import numpy as np, wave
FS = 44100
plan = [(0, .5), (1200, 2), (0, .5), (2200, 2), (0, .5), (2200, .5), (0, .5), (1200, .5), (0, .5)]
parts = []
for f, d in plan:
    n = int(FS * d); t = np.arange(n) / FS
    parts.append(np.zeros(n) if f == 0 else np.sin(2 * np.pi * f * t))
x = (np.concatenate(parts) * 0.8 * 32767).astype(np.int16)
with wave.open('tonos_prueba.wav', 'wb') as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(FS); w.writeframes(x.tobytes())
print('tonos_prueba.wav', len(x) / FS, 's')
