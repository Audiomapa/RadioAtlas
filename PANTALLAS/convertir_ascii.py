#!/usr/bin/env python3
"""
convertir_ascii.py — RadioAtlas · pre-conversión de imágenes
Por cada imagen genera:
  .pantalla.jpg      → foto recortada 600×1024
  .ascii.txt         → ASCII clásico (un color)
  .ascii.color.png   → ASCII en color (PNG, carga directa en pygame)
"""
import os, sys, argparse

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Instalar Pillow: pip3 install Pillow")
    sys.exit(1)

SCREEN_W     = 600
SCREEN_H     = 1024
DEFAULT_COLS = 75
DEFAULT_ROWS = 68
CHARS        = "@%#*+=:,. "   # denso → vacío

# Fuentes monospace para el PNG color (orden de preferencia)
FONT_PATHS = [
    "/System/Library/Fonts/Courier.ttc",          # macOS
    "/Library/Fonts/Courier New.ttf",
    "/System/Library/Fonts/Monaco.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",  # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
]

def crop_to_portrait(img, target_w, target_h):
    """Recorta al centro manteniendo proporción target_w:target_h."""
    w, h   = img.size
    target = target_w / target_h
    actual = w / h
    if actual > target:
        new_w = int(h * target)
        left  = (w - new_w) // 2
        img   = img.crop((left, 0, left + new_w, h))
    elif actual < target:
        new_h = int(w / target)
        top   = (h - new_h) // 2
        img   = img.crop((0, top, w, top + new_h))
    return img

def resize(img, w, h):
    try:    return img.resize((w, h), Image.LANCZOS)
    except: return img.resize((w, h), Image.ANTIALIAS)

def make_ascii_txt(img_gray_small, cols, rows):
    px = img_gray_small.load()
    lines = []
    for y in range(rows):
        row = ""
        for x in range(cols):
            v   = px[x, y]
            idx = int((255 - v) / 255 * (len(CHARS) - 1))
            row += CHARS[idx]
        lines.append(row)
    return lines

def make_ascii_color_png(img_color_small, img_gray_small, cols, rows, cell_w, cell_h, font):
    """Renderiza ASCII art en color como imagen PIL."""
    canvas   = Image.new('RGB', (cols * cell_w, rows * cell_h), (0, 0, 0))
    draw     = ImageDraw.Draw(canvas)
    px_gray  = img_gray_small.load()
    px_color = img_color_small.load()

    for y in range(rows):
        for x in range(cols):
            v    = px_gray[x, y]
            idx  = int((255 - v) / 255 * (len(CHARS) - 1))
            char = CHARS[idx]
            r, g, b = px_color[x, y]
            # Aumentar brillo para verse bien en pantalla oscura
            r = min(255, int(r * 1.6))
            g = min(255, int(g * 1.6))
            b = min(255, int(b * 1.6))
            draw.text((x * cell_w, y * cell_h), char, fill=(r, g, b), font=font)

    # Redimensionar al tamaño exacto de pantalla
    if canvas.size != (SCREEN_W, SCREEN_H):
        canvas = resize(canvas, SCREEN_W, SCREEN_H)
    return canvas

def load_font(cell_h):
    for path in FONT_PATHS:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, max(8, cell_h - 1))
            except Exception:
                pass
    print("  ⚠ Sin fuente TTF — usando fuente por defecto (calidad reducida)")
    try:
        return ImageFont.load_default(size=max(8, cell_h - 1))
    except TypeError:
        return ImageFont.load_default()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("carpeta",    nargs="?", default="banco_imagenes")
    ap.add_argument("--cols",     type=int,  default=DEFAULT_COLS)
    ap.add_argument("--rows",     type=int,  default=DEFAULT_ROWS)
    ap.add_argument("--no-color", action="store_true", help="omitir versión color PNG")
    ap.add_argument("--preview",  action="store_true", help="imprimir primera imagen y salir")
    args = ap.parse_args()

    exts = ('.jpg', '.jpeg', '.png', '.bmp', '.gif')
    fns  = sorted(f for f in os.listdir(args.carpeta)
                  if f.lower().endswith(exts)
                  and not any(f.endswith(s) for s in
                              ('.ascii.txt', '.pantalla.jpg', '.ascii.color.png')))

    if not fns:
        print(f"Sin imágenes en '{args.carpeta}'")
        sys.exit(1)

    cell_w = SCREEN_W // args.cols
    cell_h = SCREEN_H // args.rows
    font   = None if args.no_color else load_font(cell_h)

    print(f"Convirtiendo {len(fns)} imagen(es)  [celda {cell_w}×{cell_h}px]")

    for fn in fns:
        src = os.path.join(args.carpeta, fn)
        try:
            img_orig  = Image.open(src).convert('RGB')
            img_crop  = crop_to_portrait(img_orig, SCREEN_W, SCREEN_H)

            # 1) Foto a la medida de la pantalla
            resize(img_crop, SCREEN_W, SCREEN_H).save(
                src + ".pantalla.jpg", "JPEG", quality=90)

            # Versión pequeña para ASCII
            img_small_color = resize(img_crop,           args.cols, args.rows)
            img_small_gray  = resize(img_crop.convert('L'), args.cols, args.rows)

            # 2) ASCII clásico (.txt)
            lines = make_ascii_txt(img_small_gray, args.cols, args.rows)
            with open(src + ".ascii.txt", 'w') as f:
                f.write('\n'.join(lines))

            # 3) ASCII color (.png)
            color_label = ""
            if not args.no_color and font:
                png = make_ascii_color_png(
                    img_small_color, img_small_gray,
                    args.cols, args.rows, cell_w, cell_h, font)
                png.save(src + ".ascii.color.png", "PNG", optimize=True)
                color_label = " + .ascii.color.png"

            print(f"  ✓ {fn}  →  .pantalla.jpg + .ascii.txt{color_label}")

            if args.preview:
                print("\n--- ASCII preview ---")
                for ln in lines: print(ln)
                print("--- fin ---\n")
                break

        except Exception as e:
            print(f"  ✗ {fn}: {e}")

    print("\nListo.")
    print("Sube .ascii.txt y .ascii.color.png a ~/banco_imagenes/ en la Pi.")

if __name__ == "__main__":
    main()
