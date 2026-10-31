#!/usr/bin/env python3
"""
display_receptor.py — RadioAtlas · RECEPTOR
──────────────────────────────────────────────────────────────────────────────
Display pygame para la Pi receptora.

Estados:
  TERMINAL  → log en tiempo real, cursor verde, estimación de próxima señal
  BERENGUER → bloque [BERENGUER_START]…[BERENGUER_END] revelado typewriter
  ASCII     → imagen ASCII color typewriter (misma lógica que display_emisor)

Transiciones:
  TERMINAL  → BERENGUER   cuando llega un bloque nuevo del log
  BERENGUER → ASCII       cuando el bloque terminó + hold
  ASCII     → TERMINAL    cuando llega actividad / tiempo estimado / ciclo
  *         → TERMINAL    si llega nueva actividad real en el log

Estimación de señal:
  Se registra el timestamp de cada bloque recibido.
  Con ≥ 2 registros se calcula el intervalo promedio.
  ~PREP_SECS antes del estimado se vuelve al terminal con "escucha activa ──".

Requiere en receptor_fsk_v2.py:
  Después de aplicar la transformación Berenguer, escribir al log:
    [BERENGUER_START]
    <líneas de texto de la transformación>
    [BERENGUER_END]

  Usar: sys.stdout.flush() para que el log reciba las líneas en tiempo real.
──────────────────────────────────────────────────────────────────────────────
"""
import os, time, random

# CRÍTICO: debe ir antes de `import pygame` para no bloquear ALSA
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')

import pygame

try:
    from PIL import Image
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

# ── Configuración ──────────────────────────────────────────────────────────────

IMG_DIR = os.path.expanduser("~/banco_imagenes")
LOG_FILE = "/tmp/receptor.log"

POLL_MS          = 120      # intervalo de lectura del log (ms)
LOG_IDLE_SECS    = 20       # segundos sin actividad → entra en ASCII art

ASCII_BUILD_SECS = 120.0    # segundos para revelar imagen ASCII
ASCII_HOLD_SECS  = 60       # hold imagen completa antes de volver al terminal
ASCII_ROWS       = 68
ASCII_COLS       = 75

BERENGUER_BUILD_SECS = 35.0 # segundos para revelar el bloque Berenguer
BERENGUER_HOLD_SECS  = 20   # hold después de revelar

PREP_SECS = 15              # segundos antes del estimado → volver a terminal

# Colores
BG       = (  0,   0,   0)
C_TEXT   = (  0, 220,  75)   # verde estándar
C_DIM    = (  0, 100,  40)   # verde atenuado para líneas antiguas
C_SIGNAL = (  0, 255, 120)   # verde brillante cuando hay señal activa
C_BEREN  = ( 30, 255, 100)   # verde Berenguer
C_ART    = (  0, 160,  55)   # verde ASCII art
C_SEP    = (  0,  70,  30)   # verde separador

ASCII_CHARS = "@%#*+=:,. "

# Líneas cuya aparición NO reinicia el contador de idle
_IDLE_KEYWORDS = (
    'esperando preámbulo',
    'esperando preamb',
    'escuchando',
    'receptor fsk',
    'transmisor fsk',
    'fuera de ventana',
    'fuera del horario',
    'waiting',
    'esperando',
    'retomando',
    'frases:',
    'generacion:',
    'berenguer_start',
    'berenguer_end',
)

# Estados
STATE_TERMINAL  = 'terminal'
STATE_BERENGUER = 'berenguer'
STATE_ASCII     = 'ascii'


# ── Estado global del log ──────────────────────────────────────────────────────

class _State:
    """Estado mutable compartido entre read_log() y el bucle principal."""
    log_lines      = []          # todas las líneas visibles (filtradas)
    raw_count      = 0           # líneas sin filtrar ya leídas (para detectar las nuevas)
    log_key        = None        # (size, mtime) del archivo
    log_changed    = 0.0         # timestamp de la última línea activa

    beren_lines    = []          # líneas del último bloque Berenguer
    beren_pending  = False       # hay un bloque nuevo sin mostrar

    signal_times   = []          # timestamps de recepciones
    MAX_HISTORY    = 6

S = _State()


def _is_active_line(line):
    ll = line.lower()
    return not any(kw in ll for kw in _IDLE_KEYWORDS)


def read_log(visible_rows):
    """
    Lee el log del receptor.
    - Sólo revisa líneas nuevas para decidir si hay actividad.
    - Detecta bloques [BERENGUER_START]…[BERENGUER_END].
    Devuelve lista de las últimas visible_rows líneas (para dibujar).
    """
    try:
        st  = os.stat(LOG_FILE)
        key = (st.st_size, st.st_mtime)
        if key == S.log_key:
            return S.log_lines[-visible_rows:]

        with open(LOG_FILE, errors='replace') as f:
            raw = f.readlines()

        first_read = (S.log_key is None)
        # Contar contra las líneas SIN filtrar: log_lines excluye los marcadores
        # BERENGUER y hacía releer el último bloque en cada línea nueva del log.
        prev_count = S.raw_count
        new_all = [l.rstrip('\n').replace('\x00', '') for l in raw if l.strip() and 'nohup' not in l]

        # Primera lectura: mostrar líneas históricas pero no marcarlas como "activas"
        if first_read:
            added = []
        else:
            added = new_all[prev_count:] if len(new_all) >= prev_count else new_all

        # Líneas filtradas para mostrar (excluimos marcadores internos)
        visible = [l for l in new_all
                   if '[BERENGUER_START]' not in l and '[BERENGUER_END]' not in l]
        S.log_lines = visible
        S.raw_count = len(new_all)
        S.log_key   = key

        # Parsear líneas nuevas
        in_block      = False
        current_block = []
        for line in added:
            if '[BERENGUER_START]' in line:
                in_block      = True
                current_block = []
            elif '[BERENGUER_END]' in line:
                if current_block:
                    S.beren_lines   = current_block[:]
                    S.beren_pending = True
                    now = time.time()
                    S.signal_times.append(now)
                    if len(S.signal_times) > S.MAX_HISTORY:
                        S.signal_times.pop(0)
                in_block = False
            elif in_block:
                if line.strip():
                    current_block.append(line)
            else:
                if _is_active_line(line):
                    S.log_changed = time.time()

    except FileNotFoundError:
        if not S.log_lines:
            S.log_lines = ["▶ esperando receptor..."]

    return S.log_lines[-visible_rows:]


def estimate_next():
    """Estima timestamp de próxima señal. Devuelve None si datos insuficientes."""
    if len(S.signal_times) < 2:
        return None
    intervals = [S.signal_times[i+1] - S.signal_times[i]
                 for i in range(len(S.signal_times) - 1)]
    avg = sum(intervals) / len(intervals)
    return S.signal_times[-1] + avg


# ── AsciiBuilder ────────────────────────────────────────────────────────────────

class AsciiBuilder:
    """
    Revela una imagen ASCII color por columnas L→R.
    Idéntico al de display_emisor.py pero con ASCII_HOLD_SECS del receptor.
    """
    def __init__(self, images):
        self.images    = images
        self.idx       = 0
        self.revealed  = 0
        self.complete  = False
        self.done      = False
        self.hold_until    = None
        self.next_step_t   = None
        self.step_interval = None
        self._start()

    def _total_steps(self):
        _, data, n_rows, modo = self.images[self.idx]
        if modo == 'color':
            return ASCII_COLS * n_rows
        return max(1, sum(len(l) for l in data))

    def _start(self):
        self.revealed      = 0
        self.complete      = False
        self.done          = False
        self.hold_until    = None
        n                  = self._total_steps()
        self.step_interval = ASCII_BUILD_SECS / n
        self.next_step_t   = time.time() + self.step_interval

    def advance(self):
        self.idx = (self.idx + 1) % len(self.images)
        self._start()

    def update(self):
        if self.done:
            return
        now = time.time()
        if self.complete:
            if self.hold_until and now >= self.hold_until:
                self.done = True
        else:
            n = self._total_steps()
            while now >= self.next_step_t and self.revealed < n:
                self.revealed    += 1
                self.next_step_t += self.step_interval
            if self.revealed >= n:
                self.complete   = True
                self.hold_until = now + ASCII_HOLD_SECS

    def current(self):
        return self.images[self.idx]


# ── BerenguerBuilder ────────────────────────────────────────────────────────────

class BerenguerBuilder:
    """
    Revela el bloque de texto Berenguer carácter a carácter (typewriter).
    """
    def __init__(self, lines):
        self.lines    = lines
        self.revealed = 0
        total         = max(1, sum(len(l) for l in lines))
        self.step_int = BERENGUER_BUILD_SECS / total
        self.next_t   = time.time() + self.step_int
        self.complete = False
        self.done     = False
        self.hold_until = None

    def update(self):
        if self.done:
            return
        now = time.time()
        if self.complete:
            if self.hold_until and now >= self.hold_until:
                self.done = True
        else:
            total = sum(len(l) for l in self.lines)
            while now >= self.next_t and self.revealed < total:
                self.revealed += 1
                self.next_t   += self.step_int
            if self.revealed >= total:
                self.complete   = True
                self.hold_until = now + BERENGUER_HOLD_SECS


# ── Render helpers ──────────────────────────────────────────────────────────────

def wrap_line(line, cols):
    """Parte una línea larga en segmentos de 'cols' caracteres."""
    out = []
    while len(line) > cols:
        out.append(line[:cols])
        line = line[cols:]
    out.append(line)
    return out


def render_terminal(screen, font, S, cols, rows, ch, cw, cursor_on, now):
    """Dibuja el estado TERMINAL: log + estimación de señal."""
    # Envolver líneas largas
    wrapped = []
    for line in S.log_lines:
        wrapped.extend(wrap_line(line, cols))

    next_est = estimate_next()
    if next_est:
        secs = int(next_est - now)
        if secs > 0:
            wrapped.append(f"── próxima señal ~{secs}s ──────────────────")
        else:
            wrapped.append("── escucha activa ──────────────────────────")

    visible = wrapped[-rows:]
    y = 4
    for i, line in enumerate(visible):
        is_last  = (i == len(visible) - 1)
        is_bright = ('▶' in line or 'crc ok' in line.lower()
                     or 'preámbulo' in line.lower() or 'texto:' in line.lower()
                     or 'transformación' in line.lower() or 'imprimiendo' in line.lower()
                     or 'impresión' in line.lower() or 'enviando' in line.lower())
        color = C_SIGNAL if is_bright else C_TEXT
        text  = line + ("█" if is_last and cursor_on else "")
        screen.blit(font.render(text, True, color), (4, y))
        y += ch


def render_berenguer(screen, font, S, builder, cols, rows, ch, cursor_on):
    """Dibuja el estado BERENGUER: cabecera del log + bloque typewriter."""
    header_n = min(5, rows // 5)

    # Cabecera: últimas líneas del log antes del bloque
    log_wrap = []
    for line in S.log_lines:
        log_wrap.extend(wrap_line(line, cols))
    header_lines = log_wrap[-header_n:]

    y = 4
    for line in header_lines:
        screen.blit(font.render(line, True, C_DIM), (4, y))
        y += ch

    # Separador
    sep = "─" * min(cols, 60)
    screen.blit(font.render(sep, True, C_SEP), (4, y))
    y += ch + 4

    # Bloque Berenguer: typewriter
    remaining = builder.revealed
    for line in builder.lines:
        if y + ch > screen.get_height():
            break
        if remaining <= 0:
            break
        display = line[:cols]
        if remaining >= len(line):
            screen.blit(font.render(display, True, C_BEREN), (4, y))
            remaining -= len(line)
            y += ch
        else:
            partial = line[:remaining]
            if not builder.complete and cursor_on:
                partial += "█"
            screen.blit(font.render(partial[:cols + 1], True, C_BEREN), (4, y))
            remaining = 0
            y += ch


def render_ascii(screen, small_font, builder, W, H, art_ch, cursor_on):
    """Dibuja el estado ASCII ART (igual que display_emisor)."""
    _, data, n_rows, modo = builder.current()
    revealed = builder.revealed

    if modo == 'color':
        char_w = W / ASCII_COLS
        char_h = H / n_rows
        cur_row = min(revealed // ASCII_COLS, n_rows - 1)
        cur_col = revealed % ASCII_COLS
        # Filas completas
        if cur_row > 0:
            full_h = int(cur_row * char_h)
            screen.blit(data, (0, 0), (0, 0, W, full_h))
        # Columnas de la fila actual
        if cur_col > 0:
            row_y  = int(cur_row * char_h)
            row_h  = int(char_h) + 2
            part_w = int(cur_col * char_w)
            screen.blit(data, (0, row_y), (0, row_y, part_w, row_h))
        # Cursor
        if not builder.complete and cursor_on:
            cx = int(cur_col * char_w)
            cy = int(cur_row * char_h)
            screen.blit(small_font.render("█", True, C_TEXT), (cx, cy))
    else:
        # Texto ASCII
        remaining = revealed
        y = 4
        art_cols  = max(1, W // small_font.size("A")[0])
        for line in data:
            if remaining <= 0 or y > H:
                break
            if remaining >= len(line):
                screen.blit(small_font.render(line[:art_cols], True, C_ART), (4, y))
                remaining -= len(line)
                y += art_ch
            else:
                partial = line[:remaining]
                if not builder.complete and cursor_on:
                    partial += "█"
                screen.blit(small_font.render(partial[:art_cols + 1], True, C_ART), (4, y))
                break


# ── Main ────────────────────────────────────────────────────────────────────────

def main():
    pygame.init()
    screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    W, H   = screen.get_size()
    pygame.mouse.set_visible(False)
    clock  = pygame.time.Clock()
    print(f"[display_receptor] resolución: {W}×{H}", flush=True)

    # Fuente terminal
    FONT_SIZE = max(12, H // 42)
    F   = pygame.font.SysFont("monospace", FONT_SIZE)
    cw  = F.size("A")[0]
    ch  = F.get_height() + 2
    cols = max(1, (W - 8) // cw)
    rows = max(1, (H - 8) // ch)

    # Fuente ASCII art (más pequeña)
    ART_FONT_SIZE = max(7, H // 78)
    FA    = pygame.font.SysFont("monospace", ART_FONT_SIZE)
    art_ch = FA.get_height()

    # ── Cargar imágenes ASCII color ──────────────────────────────────────────
    images = []
    if os.path.isdir(IMG_DIR):
        files = sorted(f for f in os.listdir(IMG_DIR)
                       if f.endswith('.ascii.color.png'))
        for fn in files:
            fp = os.path.join(IMG_DIR, fn)
            try:
                surf = pygame.image.load(fp).convert()
                if surf.get_size() != (W, H):
                    surf = pygame.transform.scale(surf, (W, H))
                images.append((fn, surf, ASCII_ROWS, 'color'))
                print(f"[ascii-color] {fn} ✓", flush=True)
            except Exception as e:
                print(f"[ascii-color] {fn}: {e}", flush=True)
        if not images:
            # Fallback: .ascii.txt
            for fn in sorted(f for f in os.listdir(IMG_DIR) if f.endswith('.ascii.txt')):
                try:
                    with open(os.path.join(IMG_DIR, fn)) as f:
                        lines = [l.rstrip('\n') for l in f if l.strip()]
                    if lines:
                        images.append((fn, lines, len(lines), 'txt'))
                        print(f"[ascii-txt] {fn} ✓", flush=True)
                except Exception as e:
                    print(f"[ascii-txt] {fn}: {e}", flush=True)
        random.shuffle(images)

    if not images:
        # Sin imágenes: crear placeholder de texto
        placeholder = ["▶ radioatlas · receptor ◀",
                       "banco_imagenes/ vacío",
                       "añade .png a ~/banco_imagenes/"]
        images = [("placeholder", placeholder, len(placeholder), 'txt')]
        print("[ascii] sin imágenes — usando placeholder", flush=True)
    else:
        print(f"[ascii] {len(images)} imagen(es) cargadas", flush=True)

    # ── Estado inicial ───────────────────────────────────────────────────────
    ascii_builder  = AsciiBuilder(images)
    beren_builder  = None
    state          = STATE_TERMINAL
    last_poll_ms   = 0
    cursor_on      = True
    cursor_t       = time.time()
    S.log_changed  = time.time()   # mostrar terminal al arranque

    print(f"[display_receptor] iniciado. terminal {cols}×{rows}", flush=True)

    running = True
    while running:
        now_ms = pygame.time.get_ticks()
        now    = time.time()

        # Eventos
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            if ev.type == pygame.KEYDOWN and ev.key in (pygame.K_ESCAPE, pygame.K_q):
                running = False

        # Leer log
        if now_ms - last_poll_ms > POLL_MS:
            last_poll_ms = now_ms
            read_log(rows)

        # Cursor parpadeante
        if now - cursor_t > 0.5:
            cursor_on = not cursor_on
            cursor_t  = now

        # Condiciones
        log_active = (S.log_changed > 0 and (now - S.log_changed) < LOG_IDLE_SECS)
        next_est   = estimate_next()
        # prep_soon solo dentro de la ventana PREVIA a next_est (no después)
        prep_soon  = (next_est is not None) and (0 < (next_est - now) < PREP_SECS)

        # ── Máquina de estados ───────────────────────────────────────────────

        if state == STATE_TERMINAL:
            if S.beren_pending:
                beren_builder   = BerenguerBuilder(S.beren_lines[:])
                S.beren_pending = False
                state           = STATE_BERENGUER

            elif not log_active and not prep_soon:
                if ascii_builder.done:
                    ascii_builder.advance()
                else:
                    # Resetear timer para evitar deuda de tiempo acumulada en TERMINAL
                    ascii_builder.next_step_t = time.time() + ascii_builder.step_interval
                state = STATE_ASCII

        elif state == STATE_BERENGUER:
            if log_active and not S.beren_pending:
                # Nueva señal interrumpe (raro, pero posible)
                state = STATE_TERMINAL
                ascii_builder.advance()
            elif beren_builder is not None and beren_builder.done:
                state = STATE_ASCII
                ascii_builder.advance()
            elif S.beren_pending:
                # Llegó otro bloque mientras mostrábamos → reemplazar
                beren_builder   = BerenguerBuilder(S.beren_lines[:])
                S.beren_pending = False

        elif state == STATE_ASCII:
            if log_active or S.beren_pending:
                state = STATE_TERMINAL
            elif prep_soon:
                # Volver al terminal antes de la señal estimada
                state = STATE_TERMINAL
            elif ascii_builder.done:
                ascii_builder.advance()
                state = STATE_TERMINAL

        # ── Render ───────────────────────────────────────────────────────────
        screen.fill(BG)

        if state == STATE_TERMINAL:
            render_terminal(screen, F, S, cols, rows, ch, cw, cursor_on, now)

        elif state == STATE_BERENGUER:
            if beren_builder is not None:
                beren_builder.update()
                render_berenguer(screen, F, S, beren_builder, cols, rows, ch, cursor_on)

        elif state == STATE_ASCII:
            ascii_builder.update()
            render_ascii(screen, FA, ascii_builder, W, H, art_ch, cursor_on)

        pygame.display.flip()
        clock.tick(30)

    pygame.quit()


if __name__ == "__main__":
    main()
