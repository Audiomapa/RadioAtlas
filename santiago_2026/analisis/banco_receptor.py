#!/usr/bin/env python3
"""Corre el bucle de escucha real de un receptor_fsk_v2 sobre una grabación WAV.
Uso: python3 banco_receptor.py modulo.py grabacion.wav [refs.txt]
Reemplaza ArecordStream por un lector de archivo; no toca la impresora ni el estado."""
import sys, os, wave, importlib.util
import numpy as np
sys.path.insert(0, os.path.expanduser('~'))          # metodo_berenguer_v2

ruta_mod, ruta_wav = sys.argv[1], sys.argv[2]
refs = set()
if len(sys.argv) > 3:
    refs = {l.strip() for l in open(sys.argv[3], encoding='utf-8') if l.strip()}
spec = importlib.util.spec_from_file_location('rx', ruta_mod)
rx = importlib.util.module_from_spec(spec); spec.loader.exec_module(rx)

class FinArchivo(Exception): pass
w = wave.open(ruta_wav)
assert w.getframerate() == rx.FS, (w.getframerate(), rx.FS)

class LectorArchivo:
    def __init__(self, device, samplerate, channels, blocksize): self.ch = channels
    def read(self, frames):
        raw = w.readframes(frames)
        if len(raw) < frames * self.ch * 4: raise FinArchivo
        a = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
        return a.reshape(frames, self.ch), False
    def close(self): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass
rx.ArecordStream = LectorArchivo
rx.log = lambda etapa, msg: None if etapa == 'ESCUCHA' and 'energia=' in msg else \
    print(f"  [{w.tell()/rx.FS:6.1f}s] [{etapa}] {msg}")

res = []
try:
    while True:
        texto, ok = rx.esperar_y_decodificar()
        exacto = texto in refs if refs else None
        res.append((ok, exacto))
        print(f"  >>> CRC {'OK ' if ok else 'FALLA'}  {'(coincide con lo enviado) ' if exacto else ''}'{texto}'")
except FinArchivo:
    pass
print(f"RESUMEN {os.path.basename(ruta_mod)} {os.path.basename(ruta_wav)}: paquetes={len(res)} "
      f"CRC_OK={sum(1 for o, _ in res if o)}  coinciden={sum(1 for _, e in res if e)}")
