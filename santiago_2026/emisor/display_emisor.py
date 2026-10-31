#!/usr/bin/env python3
"""
display_emisor.py — RadioAtlas · TERMINAL
Telemetría en verde. Cuando el log lleva LOG_IDLE_SECS sin cambiar, construye
el ASCII art línea a línea (~1.5 min). Si hay .ascii.color.png los usa (color
de la foto); si no, .ascii.txt en verde. Luego vuelve al terminal.
"""
import os, time, random

# Forzar driver de audio dummy ANTES de que pygame inicialice
# Esto libera el hardware de audio para el transmisor FSK
os.environ['SDL_AUDIODRIVER'] = 'dummy'

import pygame

try:
    from PIL import Image
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

IMG_DIR       = os.path.expanduser("~/banco_imagenes")
LOG_FILE      = "/tmp/transmisor.log"
POLL_MS       = 100
LOG_IDLE_SECS = 20       # segundos sin transmisión activa → ASCII art

ASCII_BUILD_SECS = 120.0 # tiempo para construir una imagen (2 min)
ASCII_HOLD_SECS  = 180   # max 3 min en imagen completa, luego cicla (o antes si vuelve log)
ASCII_ROWS       = 68    # filas de la grilla ASCII
ASCII_COLS       = 75    # columnas de la grilla ASCII

BG     = (0,   0,   0)
C_TEXT = (0, 230,  80)
C_ART  = (0, 180,  50)

ASCII_CHARS = "@%#*+=:,. "

# ── log ────────────────────────────────────────────────────────────────────────
_log_lines   = []
_log_key     = None
_log_changed = 0.0

# Palabras que indican que el transmisor NO está transmitiendo activamente.
# Estas líneas se muestran en pantalla pero NO reinician el timer de idle.
_IDLE_KEYWORDS = (
    'fuera de ventana',
    'fuera del horario',
    'waiting',
    'esperando',
    'outside',
    'retomando',
    'transmisor fsk',
    'frases:',
    'generacion:',
)

def _is_active_line(line):
    """True si la línea indica transmisión activa (no solo estado de espera)."""
    ll = line.lower()
    return not any(kw in ll for kw in _IDLE_KEYWORDS)

def read_log(n):
    global _log_lines, _log_key, _log_changed
    try:
        st  = os.stat(LOG_FILE)
        key = (st.st_size, st.st_mtime)
        if key != _log_key:
            with open(LOG_FILE) as f:
                raw = f.readlines()
            new_all = [l.rstrip('\n') for l in raw
                       if l.strip() and 'nohup' not in l]
            prev_count = len(_log_lines)
            # Solo las líneas REALMENTE NUEVAS desde la última lectura
            if len(new_all) >= prev_count:
                added = new_all[prev_count:]
            else:
                added = new_all          # log truncado / reinicio del servicio
            _log_lines = new_all
            _log_key   = key
            # Solo reiniciar timer si las líneas nuevas son transmisión activa
            for l in added:
                if _is_active_line(l):
                    print(f"[ACTIVO] {l[:80]}")
                    _log_changed = time.time()
    except FileNotFoundError:
        if not _log_lines:
            _log_lines = ["esperando transmisor..."]
    return _log_lines[-n:]

# ── PIL fallback ───────────────────────────────────────────────────────────────
def image_to_ascii(path, cols, rows):
    try:
        img = Image.open(path).convert('L')
        try:    img = img.resize((cols, rows), Image.LANCZOS)
        except: img = img.resize((cols, rows), Image.ANTIALIAS)
        px, lines = img.load(), []
        for y in range(rows):
            row = ""
            for x in range(cols):
                idx = int((255 - px[x, y]) / 255 * (len(ASCII_CHARS) - 1))
                row += ASCII_CHARS[idx]
            lines.append(row)
        return lines
    except:
        return None

# ── Constructor línea a línea ──────────────────────────────────────────────────
class AsciiBuilder:
    """
    Cada imagen es (nombre, datos, n_rows, modo).
    modo='color' → datos es pygame.Surface; revealed = píxeles revelados (0..screen_h)
    modo='txt'   → datos es lista de strings; revealed = caracteres revelados (typewriter)
    """
    def __init__(self, images, screen_h):
        self.images   = images
        self.screen_h = screen_h
        self.idx      = 0
        self.revealed = 0
        self.complete = False
        self.done     = False   # True cuando termina hold → volver al terminal
        self.hold_until   = None
        self.next_step_t  = None
        self.step_interval = None
        self._start()

    def _n_steps(self, idx):
        """Total de pasos para la imagen idx (celdas ASCII o caracteres)."""
        _, data, n_rows, modo = self.images[idx]
        if modo == 'color':
            return ASCII_COLS * n_rows    # un paso = 1 celda de la grilla ASCII
        else:
            return max(1, sum(len(l) for l in data))  # un paso = 1 carácter

    def _start(self):
        self.revealed    = 0
        self.complete    = False
        self.done        = False
        self.hold_until  = None
        n = self._n_steps(self.idx)
        self.step_interval = ASCII_BUILD_SECS / n
        self.next_step_t   = time.time() + self.step_interval

    def advance(self):
        """Avanzar a la siguiente imagen (llamar al volver al ASCII tras pausa)."""
        self.idx = (self.idx + 1) % len(self.images)
        self._start()

    def reset_for_next(self):
        """Log interrumpió el ASCII: avanzar y preparar."""
        self.advance()

    def update(self):
        now = time.time()
        if self.done:
            return
        if self.complete:
            if self.hold_until and now >= self.hold_until:
                self.done = True   # señal: volver al terminal
        else:
            n = self._n_steps(self.idx)
            if now >= self.next_step_t and self.revealed < n:
                self.revealed   += 1
                self.next_step_t = now + self.step_interval
                if self.revealed >= n:
                    self.complete   = True
                    self.hold_until = now + ASCII_HOLD_SECS

    def current(self):
        return self.images[self.idx]   # (nombre, datos, n_rows, modo)

    def is_building(self):
        return not self.complete

# ── main ───────────────────────────────────────────────────────────────────────
def main():
    pygame.init()
    screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    W, H   = screen.get_size()
    pygame.mouse.set_visible(False)
    clock  = pygame.time.Clock()
    print(f"[display] {W}×{H}")

    # Fuente terminal
    FONT_SIZE = max(12, H // 40)
    F  = pygame.font.SysFont("monospace", FONT_SIZE)
    cw = F.size("A")[0]
    ch = F.get_height() + 2
    cols = max(1, (W - 8) // cw)
    rows = max(1, (H - 8) // ch)
    print(f"[terminal] {cols}×{rows} celdas, font {FONT_SIZE}px")

    # Fuente ASCII txt
    ART_FONT_SIZE = max(8, H // 72)
    FA     = pygame.font.SysFont("monospace", ART_FONT_SIZE)
    art_cw = FA.size("A")[0]
    art_ch = FA.get_height()
    art_cols = max(1, W // art_cw)
    art_rows = max(1, H // art_ch)
    print(f"[ascii-txt] grid {art_cols}×{art_rows}, font {ART_FONT_SIZE}px")

    # ── Cargar imágenes ────────────────────────────────────────────────────────
    images = []

    if os.path.isdir(IMG_DIR):
        files = os.listdir(IMG_DIR)

        # Prioridad 1: color PNG → revela cortando de arriba
        color_pngs = sorted(f for f in files if f.endswith('.ascii.color.png'))
        for fn in color_pngs:
            try:
                surf = pygame.image.load(os.path.join(IMG_DIR, fn)).convert()
                if surf.get_size() != (W, H):
                    surf = pygame.transform.scale(surf, (W, H))
                images.append((fn, surf, ASCII_ROWS, 'color'))
                print(f"[ascii-color] {fn} ✓")
            except Exception as e:
                print(f"[ascii-color] {fn}: {e}")

        # Prioridad 2: .ascii.txt verde
        if not images:
            txts = sorted(f for f in files if f.endswith('.ascii.txt'))
            for fn in txts:
                try:
                    with open(os.path.join(IMG_DIR, fn)) as f:
                        lines = [l.rstrip('\n') for l in f if l.strip()]
                    if lines:
                        images.append((fn, lines, len(lines), 'txt'))
                        print(f"[ascii-txt] {fn} ✓  ({len(lines)} líneas)")
                except Exception as e:
                    print(f"[ascii-txt] {fn}: {e}")

        # Prioridad 3: PIL en vivo
        if not images and HAVE_PIL:
            fns = sorted(f for f in files
                         if f.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp'))
                         and '.ascii' not in f and '.pantalla' not in f)
            for fn in fns:
                al = image_to_ascii(os.path.join(IMG_DIR, fn), art_cols, art_rows)
                if al:
                    images.append((fn, al, len(al), 'txt'))
                    print(f"[ascii-PIL] {fn} ✓")

        random.shuffle(images)
        modo = images[0][3] if images else '—'
        print(f"[ascii] {len(images)} imagen(es)  modo={modo}")
        if images:
            _, d0, n0, m0 = images[0]
            n_steps = H if m0 == 'color' else max(1, sum(len(l) for l in d0))
            unit = "px" if m0 == 'color' else "chars"
            print(f"[ascii] {ASCII_BUILD_SECS}s por imagen  ({ASCII_BUILD_SECS/n_steps*1000:.1f}ms/{unit})")
    else:
        print("[ascii] carpeta banco_imagenes no encontrada")

    builder  = AsciiBuilder(images, H) if images else None
    in_ascii = False
    last_poll = 0
    cursor_on = True
    cursor_t  = time.time()

    running = True
    while running:
        now_ms = pygame.time.get_ticks()
        now    = time.time()

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT: running = False
            if ev.type == pygame.KEYDOWN and ev.key in (pygame.K_ESCAPE, pygame.K_q):
                running = False

        if now_ms - last_poll > POLL_MS:
            last_poll = now_ms
            read_log(rows)

        if now - cursor_t > 0.5:
            cursor_on = not cursor_on
            cursor_t  = now

        log_active = (_log_changed > 0 and (now - _log_changed) < LOG_IDLE_SECS)

        if log_active:
            # Limpiar estado del builder siempre que haya actividad
            # (in_ascii O done → preparar siguiente imagen)
            if builder and (in_ascii or builder.done):
                builder.advance()
            in_ascii = False
        elif builder:
            if builder.done:
                in_ascii = False      # imagen completa, esperando siguiente ciclo
            else:
                in_ascii = True

        screen.fill(BG)

        if not in_ascii:
            # ── TERMINAL ──────────────────────────────────────────────────────
            # Wrap líneas largas en vez de cortarlas
            wrapped = []
            for line in _log_lines:
                if not line:
                    continue
                while len(line) > cols:
                    wrapped.append(line[:cols])
                    line = line[cols:]
                wrapped.append(line)
            visible = wrapped[-rows:]
            y = 4
            for i, line in enumerate(visible):
                text = line
                if i == len(visible) - 1 and cursor_on:
                    text += "█"
                screen.blit(F.render(text, True, C_TEXT), (4, y))
                y += ch

        else:
            # ── ASCII ART (construyendo línea a línea) ─────────────────────────
            builder.update()
            _, data, n_rows, modo = builder.current()
            revealed = builder.revealed

            if modo == 'color':
                # Typewriter: celda a celda, izquierda→derecha, línea a línea
                char_w  = W / ASCII_COLS
                char_h2 = H / n_rows
                cur_row = min(revealed // ASCII_COLS, n_rows - 1)
                cur_col = revealed % ASCII_COLS

                # Filas completas encima de la fila actual
                if cur_row > 0:
                    full_h = int(cur_row * char_h2)
                    screen.blit(data, (0, 0), (0, 0, W, full_h))

                # Fila actual: solo hasta cur_col
                if cur_col > 0:
                    row_y   = int(cur_row * char_h2)
                    row_h   = int(char_h2) + 2
                    part_w  = int(cur_col * char_w)
                    screen.blit(data, (0, row_y), (0, row_y, part_w, row_h))

                # Cursor █ al final de lo revelado en la fila actual
                if builder.is_building() and cursor_on:
                    cx = int(cur_col * char_w)
                    cy = int(cur_row * char_h2)
                    screen.blit(FA.render("█", True, C_TEXT), (cx, cy))

            else:
                # Typewriter: carácter a carácter a través de todas las líneas
                remaining = revealed
                y = 4
                for line in data:
                    if remaining <= 0:
                        break
                    if remaining >= len(line):
                        # línea completa
                        screen.blit(FA.render(line[:art_cols], True, C_ART), (4, y))
                        remaining -= len(line)
                        y += art_ch
                    else:
                        # línea parcial = posición actual del cursor
                        text = line[:remaining]
                        if builder.is_building() and cursor_on:
                            text += "█"
                        screen.blit(FA.render(text[:art_cols + 1], True, C_ART), (4, y))
                        break

        pygame.display.flip()
        clock.tick(30)

    pygame.quit()


if __name__ == "__main__":
    main()
