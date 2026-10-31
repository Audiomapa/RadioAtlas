#!/usr/bin/env python3
"""Mide cómo se mueve la sincronía de bits a lo largo de un paquete (sin referencia).
Uso: python3 deriva_tiempo.py grabacion.wav t_inicio t_fin
En cada tramo de 1 s busca el desfase (pasos de 10 muestras) que da bits más 'limpios'
(máxima media de |log(E1200/E2200)|, con niveles emparejados)."""
import sys, wave
import numpy as np
r, t0, t1 = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
with wave.open(r) as w:
    fs, nch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
    w.setpos(int(t0 * fs)); n = int((t1 - t0) * fs)
    x = np.frombuffer(w.readframes(n), dtype={2: np.int16, 4: np.int32}[sw]).reshape(-1, nch)[:, 0].astype(np.float32) / 2**(8*sw-1)
spb = fs // 50; t = np.arange(spb)
C = {f: (np.cos(2*np.pi*f*t/fs).astype(np.float32), np.sin(2*np.pi*f*t/fs).astype(np.float32)) for f in (1200, 2200)}
def E(B, f): c, s = C[f]; return (B @ c)**2 + (B @ s)**2
# niveles de referencia (primer segundo = preámbulo)
B0 = x[:fs].reshape(-1, spb)[: fs // spb]
ref = {f: np.percentile(E(B0, f), 75) for f in (1200, 2200)}
print(f"tramo(s)  desfase_óptimo(muestras)  claridad")
prev = None
for k in range(int((t1 - t0)) - 1):
    seg = x[k*fs : (k+1)*fs + spb]
    best = None
    for off in range(0, spb, 10):
        nb = (len(seg) - off) // spb
        B = seg[off:off + nb*spb].reshape(nb, spb)
        q = np.mean(np.abs(np.log((E(B, 1200)/ref[1200] + 1e-9) / (E(B, 2200)/ref[2200] + 1e-9))))
        if best is None or q > best[1]: best = (off, q)
    print(f"{t0+k:7.1f}   {best[0]:5d}   {best[1]:.2f}")
