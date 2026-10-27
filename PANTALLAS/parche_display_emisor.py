#!/usr/bin/env python3
"""
parche_display_emisor.py
Aplica en la Pi emisora:  python3 parche_display_emisor.py

Modifica transmisor_fsk_v2.py para que escriba display_estado.json
con el estado actual de la transmisión, para que display_emisor.py lo lea.
"""

import sys, os, re, shutil

TARGET = os.path.expanduser("~/rio_sstv/transmisor_fsk_v2.py")
BACKUP = TARGET + ".bak_display"

INJECT_IMPORTS = """\
# ── Display telemetría ────────────────────────────────────────────────────────
import json as _json
from datetime import datetime as _dt

_DISPLAY_PATH = os.path.expanduser("~/rio_sstv/display_estado.json")
_display_estado = {
    "estado": "idle",
    "frase_actual": "",
    "tipo_frase": "literal",
    "indice": 0,
    "total_frases": 48,
    "porcentaje_enviado": 0.0,
    "proxima_en": 0.0,
    "hora_inicio": "",
    "hora_fin": "",
    "timestamp": "",
}

def _upd_display(**kw):
    global _display_estado
    _display_estado.update(kw)
    _display_estado["timestamp"] = _dt.now().isoformat()
    try:
        with open(_DISPLAY_PATH, "w") as _f:
            _json.dump(_display_estado, _f)
    except Exception:
        pass
# ── fin display ───────────────────────────────────────────────────────────────
"""

print(f"[parche] leyendo {TARGET}")
with open(TARGET) as f:
    src = f.read()

if "_upd_display" in src:
    print("[parche] ya parcheado, nada que hacer.")
    sys.exit(0)

shutil.copy2(TARGET, BACKUP)
print(f"[parche] backup en {BACKUP}")

# Inyectar después del bloque de imports
lines = src.splitlines(keepends=True)
last_import = 0
for i, line in enumerate(lines):
    stripped = line.lstrip()
    if stripped.startswith("import ") or stripped.startswith("from "):
        last_import = i

lines.insert(last_import + 1, INJECT_IMPORTS)
src = "".join(lines)
print(f"[parche] imports en línea {last_import + 2}")

# ── Parche 1: cuando elige una frase (antes de transmitir) ────────────────────
# Buscamos elegir_frase() y la asignación de tipo_tx
pat_elegir = re.compile(
    r'(tipo_tx\s*,\s*frase_tx\s*=\s*elegir_frase\([^)]*\))',
    re.MULTILINE
)
repl_elegir = (
    r'\1\n'
    r'    _upd_display(estado="preparando", frase_actual=frase_tx, '
    r'tipo_frase=tipo_tx, indice=contador if "contador" in dir() else 0, '
    r'total_frases=len(FRASES) if "FRASES" in dir() else 48)'
)
src, n = pat_elegir.subn(repl_elegir, src)
print(f"[parche] elegir_frase: {n} reemplazos")

# ── Parche 2: al iniciar la transmisión FSK ───────────────────────────────────
# Buscamos el inicio del loop de transmisión de bits
pat_tx_start = re.compile(
    r'(for\s+\w+\s+in\s+bits\s*:)',
    re.MULTILINE
)
repl_tx_start = (
    r'_upd_display(estado="transmitiendo", porcentaje_enviado=0.0)\n    \1'
)
src, n = pat_tx_start.subn(repl_tx_start, src, count=1)
print(f"[parche] inicio transmisión: {n} reemplazos")

# ── Parche 3: progreso durante transmisión ────────────────────────────────────
# Buscamos dentro del loop de bits dónde se reproduce audio
# típicamente: sd.play(... o sounddevice.play(
pat_sdplay = re.compile(
    r'(sd\.play\([^)]+\)\s*\n\s*sd\.wait\(\))',
    re.MULTILINE
)
def repl_sdplay(m):
    return (m.group(0) + "\n"
            "        _upd_display(porcentaje_enviado=min("
            "(bits.index(b) + 1) / len(bits) if 'bits' in dir() and bits else 0.0, 1.0))")
src, n = pat_sdplay.subn(repl_sdplay, src, count=3)
print(f"[parche] progreso sd.play: {n} reemplazos")

# ── Parche 4: al terminar transmisión / entrar en sleep ───────────────────────
pat_sleep = re.compile(
    r'(time\.sleep\(INTERVALO[^)]*\))',
    re.MULTILINE
)
repl_sleep = (
    r'_upd_display(estado="idle", porcentaje_enviado=1.0, '
    r'proxima_en=INTERVALO_MIN if "INTERVALO_MIN" in dir() else INTERVALO if "INTERVALO" in dir() else 0)\n    \1'
)
src, n = pat_sleep.subn(repl_sleep, src, count=1)
print(f"[parche] sleep→idle: {n} reemplazos")

# ── Parche 5: horario de operación ───────────────────────────────────────────
# Intentamos leer HORA_INICIO y HORA_FIN para el display
pat_horario = re.compile(
    r'HORA_FIN\s*=\s*dtime\((\d+)\s*,\s*(\d+)\)',
    re.MULTILINE
)
m_hora = pat_horario.search(src)
if m_hora:
    h, mn = m_hora.group(1), m_hora.group(2)
    pat_ini = re.compile(r'HORA_INICIO\s*=\s*dtime\((\d+)\s*,\s*(\d+)\)')
    m_ini = pat_ini.search(src)
    if m_ini:
        hi, mi = m_ini.group(1), m_ini.group(2)
        hora_ini_str = f"{int(hi):02d}:{int(mi):02d}"
        hora_fin_str = f"{int(h):02d}:{int(mn):02d}"
        # Poner en el primer _upd_display
        src = src.replace(
            '_upd_display(estado="preparando"',
            f'_upd_display(hora_inicio="{hora_ini_str}", hora_fin="{hora_fin_str}", estado="preparando"',
            1
        )
        print(f"[parche] horario {hora_ini_str}–{hora_fin_str} añadido")

with open(TARGET, "w") as f:
    f.write(src)

print(f"\n[parche] ✓ {TARGET} modificado.")
print(f"[parche]   Backup en {BACKUP}")
print("[parche]   Reinicia el servicio: sudo systemctl restart transmisor")
