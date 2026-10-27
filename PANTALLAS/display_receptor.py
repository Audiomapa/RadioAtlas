#!/usr/bin/env python3
"""
display_receptor.py  —  RadioAtlas · Receptor
Pantalla 7" portrait 480×800
Alterna entre:
  MODE_IMG   → imagen llena la pantalla (durante idle)
  MODE_TELE  → telemetría llena la pantalla (durante FSK activo)
Lee estado de ~/sstv_recibido/display_estado.json cada 120ms.
"""

import os, json, time, random, math
import pygame

# ── Configuración ────────────────────────────────────────────────────────────
STATE_FILE  = os.path.expanduser("~/sstv_recibido/display_estado.json")
IMG_DIR     = os.path.expanduser("~/banco_imagenes")
W, H        = 480, 800
FADE_MS     = 900       # duración fade entre modos (ms)
IMG_HOLD_MS = (5000, 14000)  # rango tiempo por imagen

# Estado FSK que activa modo telemetría
ESTADOS_ACTIVOS = {"escuchando", "recibiendo", "decodificando", "imprimiendo"}

# ── Colores ──────────────────────────────────────────────────────────────────
BG        = (0,   0,   0)
DARK_OVL  = (0,   0,   0, 220)   # overlay telemetría (alpha)
TEXT      = (196, 196, 196)
MUTED     = (68,  68,  68)
DIM       = (38,  38,  38)
C_RX      = (125, 224, 64)    # verde  · recibiendo
C_DET     = (240, 188, 44)    # amarillo · escuchando
C_IDLE    = (60,  60,  60)    # gris   · idle

# ── Constantes de layout (portrait 480×800) ──────────────────────────────────
PAD_X     = 24
PAD_TOP   = 22
HDR_H     = 52          # altura cabecera (estado + reloj)
SEP_Y1    = HDR_H + PAD_TOP        # separador superior
MAIN_Y    = SEP_Y1 + 24            # texto principal empieza aquí
BOT_SEP   = H - 110                # separador inferior
BAR_Y1    = BOT_SEP + 14
BAR_Y2    = BAR_Y1 + 22
META_Y    = BAR_Y2 + 18
BAR_W     = W - PAD_X * 2 - 48    # ancho barra
BAR_H_PX  = 5

# ── Util ─────────────────────────────────────────────────────────────────────
def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"estado": "idle"}

def cargar_imagenes():
    imgs = []
    if os.path.isdir(IMG_DIR):
        for fn in os.listdir(IMG_DIR):
            if fn.lower().endswith(('.jpg','.jpeg','.png','.bmp')):
                try:
                    s = pygame.image.load(os.path.join(IMG_DIR, fn)).convert()
                    s = pygame.transform.scale(s, (W, H))
                    imgs.append(s)
                except Exception:
                    pass
    # Si no hay imágenes, generar una de placeholder
    if not imgs:
        s = pygame.Surface((W, H))
        s.fill((10, 10, 10))
        imgs.append(s)
    random.shuffle(imgs)
    return imgs

def draw_bar(surf, x, y, w, h, pct, color, bg=(17,17,17), border=(28,28,28)):
    pygame.draw.rect(surf, bg, (x, y, w, h))
    pygame.draw.rect(surf, border, (x, y, w, h), 1)
    fill_w = int(w * pct)
    if fill_w > 0:
        pygame.draw.rect(surf, color, (x+1, y+1, fill_w-1, h-2))

def render_text_wrapped(surf, text, font, color, x, y, max_w, line_h):
    words = text.split()
    line = ""
    lines = []
    for w in words:
        test = (line + " " + w).strip()
        if font.size(test)[0] <= max_w:
            line = test
        else:
            if line:
                lines.append(line)
            line = w
    if line:
        lines.append(line)
    for i, ln in enumerate(lines):
        s = font.render(ln, True, color)
        surf.blit(s, (x, y + i * line_h))
    return len(lines)

# ── Clase para manejar fade entre imágenes ───────────────────────────────────
IMG_SHOW_MS  = (2000, 8000)   # tiempo imagen visible
IMG_BLACK_MS = (1500, 5000)   # tiempo negro entre imágenes

class ImageCycler:
    """Negro → imagen → negro → imagen. Corte directo, nunca dos imágenes seguidas."""
    def __init__(self, imgs):
        self.imgs   = list(imgs)
        self.cur    = None          # None = negro
        self.switch = time.time() + random.uniform(*[x/1000 for x in IMG_BLACK_MS])

    def update(self, now_ms):
        t = time.time()
        if t < self.switch:
            return
        if self.cur is None:
            self.cur    = random.choice(self.imgs)
            self.switch = t + random.uniform(*[x/1000 for x in IMG_SHOW_MS])
        else:
            self.cur    = None
            self.switch = t + random.uniform(*[x/1000 for x in IMG_BLACK_MS])

    def draw(self, surf):
        if self.cur is not None:
            surf.blit(self.cur, (0, 0))


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    pygame.init()
    screen = pygame.display.set_mode((W, H), pygame.FULLSCREEN)
    pygame.display.set_caption("RadioAtlas · Receptor")
    pygame.mouse.set_visible(False)
    clock = pygame.time.Clock()

    # Fuentes
    FONT_SM   = pygame.font.SysFont("monospace", 11)
    FONT_MED  = pygame.font.SysFont("monospace", 13)
    FONT_LG   = pygame.font.SysFont("monospace", 26, bold=False)
    FONT_LG_I = pygame.font.SysFont("monospace", 26, italic=True)
    FONT_ST   = pygame.font.SysFont("monospace", 12, bold=True)  # estado

    # Cargar imágenes
    imgs   = cargar_imagenes()
    cycler = ImageCycler(imgs)

    # Superficie del overlay de telemetría (para fade)
    tele_surf   = pygame.Surface((W, H), pygame.SRCALPHA)
    tele_alpha  = 0    # 0=oculto 255=visible
    tele_target = 0    # destino

    # Estado
    state       = {"estado": "idle"}
    last_poll   = 0
    POLL_MS     = 120

    # Dot pulsante
    dot_t0      = time.time()

    def dot_alpha():
        t = (time.time() - dot_t0) % 1.1
        # seno suave 0..1
        return int(255 * (0.5 + 0.5 * math.cos(t / 1.1 * math.pi * 2)))

    def draw_tele(surf, st):
        surf.fill((0, 0, 0, 220))

        estado    = st.get("estado", "idle")
        frase     = st.get("frase_actual", "")
        modo      = st.get("modo_actual", "pool")
        idx_f     = st.get("indice", 0)
        energia   = st.get("energia_norm", 0.0)
        bits_pct  = st.get("bits_pct", 0.0)

        # Color según estado
        if estado == "escuchando":
            col = C_DET
        elif estado in ("recibiendo", "decodificando", "imprimiendo"):
            col = C_RX
        else:
            col = C_IDLE

        lbl_map = {
            "idle":         "IDLE",
            "escuchando":   "ESCUCHANDO",
            "recibiendo":   "RECIBIENDO",
            "decodificando":"DECODIFICANDO",
            "imprimiendo":  "IMPRIMIENDO",
        }
        lbl = lbl_map.get(estado, estado.upper())

        # ── Cabecera ──
        # Dot
        da = dot_alpha() if estado in ESTADOS_ACTIVOS else 255
        dot_color = tuple(int(c * da/255) for c in col)
        pygame.draw.circle(surf, dot_color, (PAD_X + 5, PAD_TOP + 8), 5)

        # Estado
        ts = FONT_ST.render(lbl, True, col)
        surf.blit(ts, (PAD_X + 18, PAD_TOP))

        # Reloj
        hhmm = time.strftime("%H:%M:%S")
        tc = FONT_SM.render(hhmm, True, MUTED)
        surf.blit(tc, (W - PAD_X - tc.get_width(), PAD_TOP + 2))

        # Separador
        pygame.draw.line(surf, (40, 40, 40), (PAD_X, SEP_Y1), (W - PAD_X, SEP_Y1), 1)

        # ── Sub-label ──
        sub_map = {
            "escuchando":   "señal detectada · FSK 1200/2200 Hz",
            "recibiendo":   "decodificando · 50 baudios",
            "decodificando":"✓ CRC-16 OK",
            "imprimiendo":  "método berenguer",
            "idle":         "",
        }
        sub = sub_map.get(estado, "")
        if sub:
            ts2 = FONT_SM.render(sub, True, MUTED if estado != "escuchando" else C_DET)
            surf.blit(ts2, (PAD_X, SEP_Y1 + 12))

        # ── Frase (texto principal grande) ──
        if frase:
            ph_y = MAIN_Y + 20
            render_text_wrapped(surf, frase, FONT_LG_I, col,
                                PAD_X, ph_y, W - PAD_X*2, 38)

            # modo/index debajo de la frase
        if modo or idx_f:
            modo_str = f"modo: {modo}  ·  frase: {idx_f:02d}"
            tm = FONT_SM.render(modo_str, True, DIM)
            surf.blit(tm, (PAD_X, BOT_SEP - 26))

        # ── Separador inferior ──
        pygame.draw.line(surf, (40, 40, 40), (PAD_X, BOT_SEP), (W - PAD_X, BOT_SEP), 1)

        # ── Barras ──
        bx = PAD_X + 48
        bw = W - PAD_X - 48 - PAD_X

        # señal
        tl = FONT_SM.render("señal", True, MUTED)
        surf.blit(tl, (PAD_X, BAR_Y1 + 1))
        draw_bar(surf, bx, BAR_Y1, bw, BAR_H_PX, energia, col)

        # bits (sólo durante recibiendo)
        if estado == "recibiendo" and bits_pct > 0:
            tl2 = FONT_SM.render("bits", True, MUTED)
            surf.blit(tl2, (PAD_X, BAR_Y2 + 1))
            draw_bar(surf, bx, BAR_Y2, bw, BAR_H_PX, bits_pct, C_RX)

        # meta
        meta = f"modo:{modo} · frase:{idx_f:02d}"
        tm2 = FONT_SM.render(meta, True, MUTED)
        surf.blit(tm2, (PAD_X, META_Y))

    # ── Loop principal ────────────────────────────────────────────────────────
    running = True
    while running:
        now_ms = pygame.time.get_ticks()

        # Eventos
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            if ev.type == pygame.KEYDOWN:
                if ev.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False

        # Leer estado
        if now_ms - last_poll > POLL_MS:
            last_poll = now_ms
            state = load_state()

        estado_actual = state.get("estado", "idle")
        tele_target   = 255 if estado_actual in ESTADOS_ACTIVOS else 0

        # Fade telemetría
        step = 255 / (FADE_MS / (1000 / 60))  # pasos por frame a 60fps
        if tele_alpha < tele_target:
            tele_alpha = min(tele_alpha + step, tele_target)
        elif tele_alpha > tele_target:
            tele_alpha = max(tele_alpha - step, tele_target)

        # ── Dibujar ──
        screen.fill(BG)

        # Capa imagen (siempre presente)
        cycler.update(now_ms)
        cycler.draw(screen)

        # Capa telemetría (fade in/out)
        if tele_alpha > 1:
            tele_surf.fill((0, 0, 0, 0))
            draw_tele(tele_surf, state)
            tele_surf.set_alpha(int(tele_alpha))
            screen.blit(tele_surf, (0, 0))

        pygame.display.flip()
        clock.tick(60)

    pygame.quit()

if __name__ == "__main__":
    main()
