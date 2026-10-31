#!/usr/bin/env python3
"""Decodifica un WAV FSK limpio (el que genera el emisor) y escribe su texto.
Uso: python3 texto_de_wav.py a.wav [b.wav ...]  -> imprime 'archivo<TAB>CRC<TAB>texto'"""
import sys, wave, struct
import numpy as np

def crc16(d):
    c = 0xFFFF
    for b in d:
        c ^= b << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) if c & 0x8000 else (c << 1); c &= 0xFFFF
    return c

for ruta in sys.argv[1:]:
    with wave.open(ruta) as w:
        fs, sw, n = w.getframerate(), w.getsampwidth(), w.getnframes()
        x = np.frombuffer(w.readframes(n), dtype={2: np.int16, 4: np.int32}[sw]).astype(np.float32)
    spb = fs // 50; nb = len(x) // spb
    B = x[:nb * spb].reshape(nb, spb); t = np.arange(spb)
    e = lambda f: (B @ np.cos(2*np.pi*f*t/fs)) ** 2 + (B @ np.sin(2*np.pi*f*t/fs)) ** 2
    bits = (e(1200) > e(2200)).astype(int).tolist()
    sync = [int(c) for c in format(0xABCD, '016b')]
    i = next(k for k in range(len(bits) - 16) if bits[k:k+16] == sync)
    by = bytes(int(''.join(map(str, bits[k:k+8])), 2) for k in range(i, len(bits) - 7, 8))
    L, c = struct.unpack('>HH', by[2:6]); p = by[6:6+L]
    print(f"{ruta}\t{'OK' if crc16(p) == c else 'FALLA'}\t{p.decode('utf-8', 'replace')}")
