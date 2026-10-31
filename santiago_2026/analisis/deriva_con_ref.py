#!/usr/bin/env python3
"""Sincronía real a lo largo de un paquete, usando los bits enviados como referencia.
Uso: python3 deriva_con_ref.py grabacion.wav t_aprox_inicio "texto enviado"
1) Construye la secuencia exacta de símbolos del emisor (preámbulo 100 + cabecera + texto).
2) Encuentra el inicio del paquete en la grabación (pasos de 10 muestras).
3) Para cada tramo de 50 símbolos, busca el corrimiento (±400 muestras, pasos de 10)
   con menos errores, con niveles emparejados. Imprime corrimiento y errores por tramo."""
import sys, wave, struct
import numpy as np

def crc16(d):
    c = 0xFFFF
    for b in d:
        c ^= b << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) if c & 0x8000 else (c << 1); c &= 0xFFFF
    return c

r, tap, txt = sys.argv[1], float(sys.argv[2]), sys.argv[3]
p = txt.encode('utf-8')
pkt = b'\xAB\xCD' + struct.pack('>HH', len(p), crc16(p)) + p
sim = [1 if i % 2 == 0 else 0 for i in range(100)] + [(b >> i) & 1 for b in pkt for i in range(7, -1, -1)]
sim = np.array(sim)
with wave.open(r) as w:
    fs, nch = w.getframerate(), w.getnchannels()
    a = max(0, int((tap - 1.5) * fs)); w.setpos(a)
    n = int((len(sim) * 0.02 + 4) * fs)
    x = np.frombuffer(w.readframes(n), dtype=np.int32).reshape(-1, nch)[:, 0].astype(np.float32) / 2**31
spb = fs // 50; t = np.arange(spb)
C = {f: (np.cos(2*np.pi*f*t/fs).astype(np.float32), np.sin(2*np.pi*f*t/fs).astype(np.float32)) for f in (1200, 2200)}
def bits_en(pos, k0, k1, ref):
    ini = (pos + np.arange(k0, k1) * spb).astype(int)
    ok = (ini >= 0) & (ini + spb <= len(x))
    B = x[ini[ok][:, None] + t[None, :]]
    m = ((B @ C[1200][0])**2 + (B @ C[1200][1])**2) / ref[0]
    s = ((B @ C[2200][0])**2 + (B @ C[2200][1])**2) / ref[1]
    return (m > s).astype(int), ok
# niveles: buscar inicio grueso por energía y medir en el preámbulo
e = np.array([np.sum(x[i:i+spb]**2) for i in range(0, len(x) - spb, spb)])
i0 = int(np.argmax(e > e.max() * 0.05)) * spb
pre = x[i0 + 10*spb: i0 + 80*spb].reshape(-1, spb)
ref = [np.percentile((pre @ C[f][0])**2 + (pre @ C[f][1])**2, 75) for f in (1200, 2200)]
# inicio exacto: preámbulo final + sync + LEN (símbolos 90..132)
best = None
for pos in range(i0 - 2*spb, i0 + 4*spb, 10):
    b, ok = bits_en(pos, 90, 132, ref)
    err = int(np.sum(b != sim[90:132][ok]))
    if best is None or err < best[1]: best = (pos, err)
pos0 = best[0]
print(f"inicio paquete: {(a + pos0)/fs:.3f}s  errores en sync/LEN: {best[1]}  símbolos: {len(sim)}")
print("tramo(símbolos)  corrimiento(muestras)  errores/50")
for k0 in range(100, len(sim), 50):
    k1 = min(k0 + 50, len(sim)); res = []
    for d in range(-400, 401, 10):
        b, ok = bits_en(pos0 + d, k0, k1, ref)
        if ok.sum() < (k1 - k0): continue
        res.append((int(np.sum(b != sim[k0:k1])), abs(d), d))
    res.sort()
    print(f"  {k0:4d}-{k1:4d}       {res[0][2]:+5d}            {res[0][0]:2d}   (con d=0: {[r_[0] for r_ in res if r_[2]==0][0]})")
