#!/usr/bin/env python3
"""Mide errores de bit reales en una grabación del receptor.
Uso: python3 ber_paquetes.py grabacion.wav textos_enviados.txt [canal]
- Detector idéntico a receptor_fsk_v2.py (Goertzel en bin k, bloque = FS/50).
- Prueba 48 offsets (pasos de 20 muestras) por paquete.
- Compara contra los textos enviados (uno por línea) y reporta el mejor.
Solo numpy + wave; lee el archivo por tramos para no cargar RAM."""
import sys, wave, struct
import numpy as np

MARK, SPACE, BAUD = 1200, 2200, 50
SYNC = b'\xAB\xCD'

def crc16(d):
    c = 0xFFFF
    for b in d:
        c ^= b << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) if c & 0x8000 else (c << 1)
            c &= 0xFFFF
    return c

def a_bits(bs):
    return [(b >> i) & 1 for b in bs for i in range(7, -1, -1)]

def leer(ruta, canal):
    with wave.open(ruta) as w:
        fs, nch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        dt = {2: np.int16, 4: np.int32}[sw]
        partes = []
        while True:
            raw = w.readframes(fs * 5)
            if not raw: break
            partes.append((np.frombuffer(raw, dtype=dt).reshape(-1, nch)[:, canal]
                           .astype(np.float32) / (2 ** (8 * sw - 1))))
    return fs, np.concatenate(partes)

import os
NORM = os.environ.get('NORM', '0') == '1'      # 1 = emparejar niveles con el preámbulo
ALFA = float(os.environ.get('ALFA', '0'))      # >0 = restar cola del símbolo anterior
PPM = float(os.environ.get('PPM', '0'))        # reloj del emisor más rápido (+) que el receptor
ruta, ruta_txt = sys.argv[1], sys.argv[2]
canal = int(sys.argv[3]) if len(sys.argv) > 3 else 0
fs, x = leer(ruta, canal)
spb = fs // BAUD
t = np.arange(spb, dtype=np.float32)
vec = {}
for fr in (MARK, SPACE):
    k = int(0.5 + spb * fr / fs); ph = 2 * np.pi * k / spb
    vec[fr] = (np.cos(ph * t), np.sin(ph * t))

def energias(seg):
    paso = spb / (1 + PPM * 1e-6)          # símbolo del emisor medido en muestras del receptor
    nb = int((len(seg) - spb) // paso) + 1
    ini = np.round(np.arange(nb) * paso).astype(int)
    B = seg[ini[:, None] + np.arange(spb)[None, :]]
    out = {}
    for fr, (c, s) in vec.items():
        out[fr] = ((B @ c) ** 2 + (B @ s) ** 2) / spb ** 2
    return out[MARK], out[SPACE]

textos = [l.strip() for l in open(ruta_txt, encoding='utf-8') if l.strip()]
refs = []
for tx in textos:
    p = tx.encode('utf-8')
    hdr = SYNC + struct.pack('>HH', len(p), crc16(p))
    refs.append((tx, a_bits(hdr + p)))

print(f"archivo={ruta} fs={fs} dur={len(x)/fs:.1f}s pico={np.abs(x).max():.4f} "
      f"(1.0 = fondo de escala)")
# Paquetes: tramos con energía (bloques de 20 ms)
em, es = energias(x)
et = em + es
umbral = 0.00008
act = et > umbral
tramos, i = [], 0
while i < len(act):
    if act[i]:
        j = i
        while j < len(act) and (act[j] or act[j:j + 100].any()): j += 1
        if j - i > 150: tramos.append((i, j))
        i = j
    else: i += 1
print(f"paquetes con energía: {len(tramos)}")

for (a, b) in tramos:
    seg_ini = max(0, a - 5) * spb
    seg = x[seg_ini:(b + 5) * spb]
    pico = np.abs(seg).max()
    print(f"\n=== paquete en t={a*spb/fs:.2f}s–{b*spb/fs:.2f}s  pico={pico:.4f}")
    mejor = None
    for off in range(0, spb, spb // 48):
        m, s = energias(seg[off:])
        if NORM:
            # nivel de cada tono medido en el preámbulo (bloques 5..95: alterna 1200/2200)
            pm, ps = m[5:95], s[5:95]
            m = m / np.percentile(pm, 75); s = s / np.percentile(ps, 75)
        if ALFA:
            # resta la cola del símbolo anterior (fracción ALFA de su energía)
            m = m.copy(); s = s.copy()
            m[1:] = m[1:] - ALFA * m[:-1]; s[1:] = s[1:] - ALFA * s[:-1]
        bits = (m > s).astype(int).tolist()
        sync_b = a_bits(SYNC)
        for idx in range(len(bits) - 16):
            if bits[idx:idx + 16] == sync_b:
                break
        else:
            continue
        for tx, ref in refs:
            n = min(len(ref), len(bits) - idx)
            rx = bits[idx:idx + n]
            err = [k for k in range(n) if rx[k] != ref[k]]
            cand = (len(err) + (len(ref) - n), off, idx, tx, ref, rx, err, m, s)
            if mejor is None or cand[0] < mejor[0]: mejor = cand
    if mejor is None:
        print("  SYNC no encontrado en ningún offset"); continue
    nerr, off, idx, tx, ref, rx, err, m, s = mejor
    print(f"  texto enviado: '{tx}'  ({len(ref)} bits con cabecera)")
    print(f"  mejor offset={off} muestras  bits erróneos={nerr}  BER={100*nerr/len(ref):.2f}%")
    # CRC con los bits recibidos
    rxb = bytes(int(''.join(map(str, rx[k:k+8])), 2) for k in range(0, len(rx) - 7, 8))
    if len(rxb) >= 6:
        L, c = struct.unpack('>HH', rxb[2:6])
        pl = rxb[6:6 + L]
        print(f"  LEN rx={L} (real {len(ref)//8-6})  CRC {'OK' if len(pl)==L and crc16(pl)==c else 'FALLA'}"
              f"  texto rx='{pl.decode('utf-8','replace')}'")
    if err:
        tipos = {'1->0': 0, '0->1': 0}
        for k in err:
            tipos['1->0' if ref[k] == 1 else '0->1'] += 1
        print(f"  tipo de error: enviado 1(1200Hz) leído 0: {tipos['1->0']}   "
              f"enviado 0(2200Hz) leído 1: {tipos['0->1']}")
        # bit anterior distinto (transición) vs igual
        trans = sum(1 for k in err if k > 0 and ref[k - 1] != ref[k])
        print(f"  errores justo después de un cambio de tono: {trans}/{len(err)}")
        print(f"  posiciones (bit): {err[:40]}{' ...' if len(err)>40 else ''}")
    # margen: ratio energía del tono correcto / incorrecto por bit
    r = []
    for k in range(min(len(ref), len(m) - idx)):
        ok_e = m[idx + k] if ref[k] else s[idx + k]
        bad_e = s[idx + k] if ref[k] else m[idx + k]
        r.append(ok_e / max(bad_e, 1e-12))
    r = np.array(r)
    r1 = np.array([r[k] for k in range(len(r)) if ref[k] == 1])
    r0 = np.array([r[k] for k in range(len(r)) if ref[k] == 0])
    print(f"  ratio correcto/incorrecto — bits 1200Hz: mediana {np.median(r1):.1f} min {r1.min():.2f}"
          f" | bits 2200Hz: mediana {np.median(r0):.1f} min {r0.min():.2f}")
    print(f"  bits con ratio < 2: {(r < 2).sum()}   < 1 (error): {(r < 1).sum()}")
    e1 = np.median([m[idx + k] for k in range(len(r)) if ref[k] == 1])
    e0 = np.median([s[idx + k] for k in range(len(r)) if ref[k] == 0])
    print(f"  energía mediana: 1200Hz={e1:.5f}  2200Hz={e0:.5f}  (relación {e1/e0:.2f})")
