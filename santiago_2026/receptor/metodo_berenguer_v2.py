#!/usr/bin/env python3
"""
metodo_berenguer.py — poema visual generativo, con memoria/continuidad.

Reescritura del método original con tres cambios de fondo:

1. ESPACIADO DINÁMICO: cada transformación calcula y devuelve cuánto alto
   ocupó de verdad, y la frase siguiente arranca justo después — nunca se
   superponen (a diferencia del original, que repartía el alto en partes
   iguales sin importar qué tan alta saliera cada transformación).
2. TRANSFORMACIONES DIAGONALES nuevas (cascada diagonal + rayas cruzadas).
3. Las frases viven en frases.txt, no en el código.

Dos formas de generar imagen:
  - generar_rollo_por_bloques(): metros de 576x8000px para imprimir
    directo en rollo continuo.
  - generar_segmento_secuencial(): genera UN segmento de 576x400 en
    resolución nativa de la impresora (203dpi, ~50mm de papel), continuando
    exactamente donde quedó el anterior. Lo llama receptor_fsk.py tras
    cada frase decodificada por FSK.

Uso standalone (rollo continuo):
    python3 metodo_berenguer.py --metros 30
    python3 metodo_berenguer.py --metros 5 --frases-archivo mis_frases.txt

Uso como módulo (desde receptor_fsk.py):
    from metodo_berenguer import generar_segmento_secuencial, cargar_frases
    frases = cargar_frases("frases.txt")
    imagen, nuevo_indice, nuevo_offset = generar_segmento_secuencial(
        frases, indice_frase=estado["indice_frase"], offset=estado["offset"]
    )
"""

import argparse
import json
import os
import random
from PIL import Image, ImageDraw, ImageFont
import unicodedata

def _normalizar_char(c):
    """
    Convierte caracteres acentuados a su equivalente sin acento para
    garantizar que se rendericen correctamente incluso con la fuente
    bitmap de fallback de PIL (que no soporta Unicode completo).
    Solo se usa para caracteres individuales en el mesostico y pliegue
    — el texto de las frases completas sí mantiene los acentos.
    """
    return unicodedata.normalize("NFD", c).encode("ascii", "ignore").decode("ascii") or c

# ============================================================
# CONFIGURACIÓN
# ============================================================
# Resolución nativa de la impresora térmica (80mm a 203dpi, margen incluido).
# Generar directamente a este ancho elimina el upscale 320→576 que causaba
# compounding con ESCALA_FUENTE y truncaba frases largas.
ANCHO = 576
# Alto de cada segmento del rollo. 400 dots ≈ 50mm de papel por frase recibida.
ALTO_SEGMENTO = 400
MARGEN = 35          # margen lateral — mantiene texto dentro del área imprimible
COLOR_FONDO = (255, 255, 255)
COLOR_TEXTO = (10, 10, 10)
COLOR_NEGRITA = (0, 0, 0)

# Con ANCHO nativo (576) ya no se necesita amplificar fuentes para compensar
# un canvas pequeño. ESCALA_FUENTE=3.0 da texto claramente legible en papel
# de 80mm a 203dpi. IMPORTANTE: con escala ≠ 1.0 los dobles _e(_e(x)) que
# aparecen en algunas transformaciones componen la escala dos veces; todos
# deben ser un solo _e(). Ver transformaciones 3, 4, 5, 8, 10.
ESCALA_FUENTE = 2.0

ESPACIO_ENTRE_FRASES = 45  # espacio entre transformaciones consecutivas en el rollo

def _e(tamano):
    """Aplica el factor de escala global al tamaño de fuente."""
    return max(12, int(tamano * ESCALA_FUENTE))

PIXELS_POR_METRO = 8000     # para el modo rollo standalone

FRASES_POR_DEFECTO = [
    "El atávico sueño humano de las totalidades",
    "formateando la superficie del mundo",
    "Redes de distribución en movimiento continuo",
]


# ============================================================
# CARGA DE FUENTE Y FRASES
# ============================================================
def cargar_fuente(tamano):
    rutas_posibles = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSerif.ttf",
    ]
    script_dir = os.path.dirname(os.path.abspath(__file__))
    ruta_local = os.path.join(script_dir, "fonts", "DejaVuSans.ttf")
    if os.path.exists(ruta_local):
        rutas_posibles.insert(0, ruta_local)

    for ruta in rutas_posibles:
        if os.path.exists(ruta):
            try:
                return ImageFont.truetype(ruta, max(1, int(tamano)))
            except Exception:
                pass

    # Fallback: fuente bitmap de PIL — NO soporta caracteres acentuados.
    # Los acentos (ó, ú, á, ñ...) aparecen como cajas en la impresión.
    # Solución: sudo apt install -y fonts-dejavu-core
    print("⚠️  FUENTE TTF NO ENCONTRADA — instalar: sudo apt install fonts-dejavu-core")
    try:
        return ImageFont.load_default(size=max(1, int(tamano)))
    except TypeError:
        return ImageFont.load_default()


def cargar_frases(ruta="frases.txt"):
    """Una frase por línea. Si el archivo no existe, usa un set mínimo de respaldo."""
    if os.path.exists(ruta):
        with open(ruta, encoding="utf-8") as f:
            frases = [linea.strip() for linea in f if linea.strip()]
        if frases:
            return frases
    print(f"⚠️  No se encontró (o está vacío) '{ruta}' — usando frases de respaldo.")
    return FRASES_POR_DEFECTO


# ============================================================
# DIBUJO DE TEXTO
# ============================================================
def dibujar_texto_con_negrita(draw, texto, x, y, tamano, es_negrita=False, color=COLOR_TEXTO):
    fuente = cargar_fuente(tamano)
    if es_negrita:
        draw.text((x + 1, y), texto, fill=COLOR_NEGRITA, font=fuente)
        draw.text((x, y + 1), texto, fill=COLOR_NEGRITA, font=fuente)
        draw.text((x, y), texto, fill=COLOR_NEGRITA, font=fuente)
    else:
        draw.text((x, y), texto, fill=color, font=fuente)





# ============================================================
# TRANSFORMACIONES — todas top-anchored: reciben y_top, devuelven altura usada
# ============================================================
def transformacion_0_tension(draw, frase, x_centro, y_top, ancho, paso, img=None):
    palabras = frase.split()
    if not palabras:
        return 20

    random.seed(paso * 7)
    idx_destacada = random.randint(0, len(palabras) - 1)

    x_actual = MARGEN
    tamano_max = 12
    for i, palabra in enumerate(palabras):
        if i == idx_destacada:
            tamano = _e(22 + random.randint(0, 4))
            es_negrita = True
        else:
            tamano = _e(12 + random.randint(0, 4))
            es_negrita = False
        tamano_max = max(tamano_max, tamano)

        fuente = cargar_fuente(tamano)
        bbox = draw.textbbox((0, 0), palabra, font=fuente)
        ancho_palabra = bbox[2] - bbox[0]

        if x_actual + ancho_palabra > ancho - MARGEN:
            break  # no partir palabras a la fuerza fuera del lienzo

        dibujar_texto_con_negrita(draw, palabra, x_actual, y_top, tamano, es_negrita)
        x_actual += ancho_palabra + max(16, tamano // 2)

    return tamano_max + 10


def transformacion_1_fragmentacion(draw, frase, x_centro, y_top, ancho, paso, img=None):
    palabras = frase.split()
    if not palabras:
        return 20

    random.seed(paso * 11)
    num_partes = random.randint(2, 3)
    parte_size = max(1, len(palabras) // num_partes)

    partes = []
    for i in range(num_partes):
        inicio = i * parte_size
        fin = (i + 1) * parte_size if i < num_partes - 1 else len(palabras)
        partes.append(" ".join(palabras[inicio:fin]))

    y_actual = y_top
    for i, parte in enumerate(partes):
        if i % 2 == 0:
            tamano = _e(18 + random.randint(0, 6))
            es_negrita = True
        else:
            tamano = _e(10 + random.randint(0, 4))
            es_negrita = False

        x_actual = MARGEN + random.randint(0, 30)
        espacio_pw = max(14, tamano // 3)

        # Dibujar palabra a palabra para respetar el borde derecho del canvas
        for pw in parte.split():
            fuente = cargar_fuente(tamano)
            bbox = draw.textbbox((0, 0), pw, font=fuente)
            ancho_pw = bbox[2] - bbox[0]
            if x_actual + ancho_pw > ancho - MARGEN:
                break
            dibujar_texto_con_negrita(draw, pw, x_actual, y_actual, tamano, es_negrita)
            x_actual += ancho_pw + espacio_pw

        y_actual += tamano + random.randint(4, 8)

    return y_actual - y_top


def transformacion_2_vertical(draw, frase, x_centro, y_top, ancho, paso, img=None):
    # Posición horizontal de la columna: distribuida en tres zonas
    # (izquierda, centro, derecha) para que la columna no aparezca
    # siempre en el mismo borde. Seeded por paso → reproducible.
    random.seed(paso * 13 + 3)
    zona = random.randint(0, 2)
    margen_texto = MARGEN + 10
    if zona == 0:
        columna = random.randint(margen_texto, ancho // 3)
    elif zona == 1:
        columna = random.randint(ancho // 3, 2 * ancho // 3)
    else:
        columna = random.randint(2 * ancho // 3, ancho - margen_texto - 30)
    y_actual = y_top

    for i, char in enumerate(frase):
        if char == ' ':
            y_actual += 12
            continue
        if i % 3 == 0:
            tamano = _e(18 + (i % 5))
            es_negrita = True
        elif i % 3 == 1:
            tamano = _e(16 + (i % 3))   # era _e(10) = 22px — demasiado pequeño para térmico
            es_negrita = False
        else:
            tamano = _e(16 + (i % 4))   # era _e(14) — sube el piso a 32px
            es_negrita = (i % 2 == 0)

        dibujar_texto_con_negrita(draw, _normalizar_char(char) or char, columna, y_actual, tamano, es_negrita)
        y_actual += tamano + 2

    return y_actual - y_top


def transformacion_3_escalada(draw, frase, x_centro, y_top, ancho, paso, img=None):
    palabras = frase.split()
    if not palabras:
        return 20

    x_actual = MARGEN
    tamano_max = 10
    for i, palabra in enumerate(palabras):
        tamano = _e(13 + (i / max(1, len(palabras) - 1)) * 15)  # era _e(10..26): mínimo 20px → ahora 26px
        tamano_max = max(tamano_max, tamano)
        es_negrita = i > len(palabras) // 2

        fuente = cargar_fuente(int(tamano))
        bbox = draw.textbbox((0, 0), palabra, font=fuente)
        ancho_palabra = bbox[2] - bbox[0]
        if x_actual + ancho_palabra > ancho - MARGEN:
            break

        dibujar_texto_con_negrita(draw, palabra, x_actual, y_top, int(tamano), es_negrita)
        x_actual += ancho_palabra + max(14, int(tamano) // 3)

    return int(tamano_max) + 10


def transformacion_4_colapso(draw, frase, x_centro, y_top, ancho, paso, img=None):
    palabras = frase.split()
    if not palabras:
        return 20

    x_actual = MARGEN
    tamano_max = 6
    for i, palabra in enumerate(palabras):
        tamano = _e(26 - (i / max(1, len(palabras) - 1)) * 14)  # era _e(26..6): final 12px → ahora mínimo 24px
        tamano_max = max(tamano_max, tamano)
        es_negrita = i < len(palabras) // 3

        fuente = cargar_fuente(int(tamano))
        bbox = draw.textbbox((0, 0), palabra, font=fuente)
        ancho_palabra = bbox[2] - bbox[0]
        if x_actual + ancho_palabra > ancho - MARGEN:
            break

        dibujar_texto_con_negrita(draw, palabra, x_actual, y_top, int(tamano), es_negrita)
        x_actual += ancho_palabra + max(14, int(tamano) // 3)

    return int(tamano_max) + 10


def transformacion_5_pliegue(draw, frase, x_centro, y_top, ancho, paso, img=None):
    """
    No es un espejo (invertir los caracteres es un gesto plano). Es un
    pliegue: la frase sigue leyéndose derecho, pero a la mitad el soporte
    "se da vuelta" — las palabras siguientes se dibujan rotadas 180°, como
    si el papel se doblara sobre sí mismo sin cortarse. Más cerca de la
    cinta de Moebius/botella de Klein de la que habla Berenguer que una
    simple reversión de texto.
    """
    palabras = frase.split()
    if not palabras:
        return 20

    random.seed(paso * 17)
    punto_pliegue = max(1, len(palabras) // 2)

    x_actual = MARGEN
    y_actual = y_top
    y_fin = y_top + 20

    for i, palabra in enumerate(palabras):
        tamano = _e(14 + random.randint(0, 8))
        es_negrita = i < punto_pliegue

        fuente = cargar_fuente(tamano)
        bbox = draw.textbbox((0, 0), palabra, font=fuente)
        ancho_palabra = bbox[2] - bbox[0]
        alto_palabra = bbox[3] - bbox[1]

        if x_actual + ancho_palabra > ancho - MARGEN:
            x_actual = MARGEN
            y_actual += tamano + 6

        if i < punto_pliegue or img is None:
            # antes del pliegue: derecho, normal
            dibujar_texto_con_negrita(draw, palabra, x_actual, y_actual, tamano, es_negrita)
        else:
            # después del pliegue: rotado 180°.
            # El canvas temporal lleva padding extra para que PIL no clipe
            # letras con ascendentes/descendentes al rotar. La posición de
            # pegado se ajusta para compensar el padding y mantener la
            # palabra en la misma posición visual.
            pad = 12
            tmp_w = ancho_palabra + pad * 2
            tmp_h = tamano + pad * 2
            tmp = Image.new("RGBA", (tmp_w, tmp_h), (0, 0, 0, 0))
            ImageDraw.Draw(tmp).text((pad, pad), palabra, fill=COLOR_NEGRITA, font=fuente)
            rotated = tmp.rotate(180)
            paste_x = max(0, x_actual - pad)
            paste_y = max(0, y_actual - pad)
            img.paste(rotated, (paste_x, paste_y), rotated)

        x_actual += ancho_palabra + random.randint(4, 12)
        y_fin = y_actual + tamano + 6

    return y_fin - y_top


def transformacion_7_cascada_diagonal(draw, frase, x_centro, y_top, ancho, paso, img=None):
    """
    Las palabras siguen una línea diagonal real que cruza el plano —
    no una escalera con indentación, sino un ángulo sostenido de arriba
    a abajo, de izquierda a derecha o al revés. El ojo tiene que seguir
    la diagonal para leer. Como Berenguer disponía el texto sobre la
    superficie del papel, siguiendo líneas invisibles que cruzan el espacio.
    """
    palabras = frase.split()
    if not palabras:
        return 20

    random.seed(paso * 23)
    n = len(palabras)
    sentido = random.choice([-1, 1])  # -1: de derecha a izquierda; 1: de izquierda a derecha

    # Definir los dos extremos de la diagonal en el espacio del lienzo
    if sentido == 1:
        x_inicio = MARGEN
        x_fin = ancho - MARGEN - 60
    else:
        x_inicio = ancho - MARGEN - 60
        x_fin = MARGEN

    tamano_base = _e(12 + random.randint(0, 6))
    alto_palabra = tamano_base + 6
    alto_total = n * alto_palabra

    for i, palabra in enumerate(palabras):
        t = i / max(1, n - 1)
        x = int(x_inicio + t * (x_fin - x_inicio))
        y = y_top + i * alto_palabra

        tamano = tamano_base + random.randint(-2, 4)
        es_negrita = random.random() < 0.35

        fuente = cargar_fuente(tamano)
        bbox = draw.textbbox((0, 0), palabra, font=fuente)
        ancho_palabra = bbox[2] - bbox[0]
        x_dibujo = max(MARGEN, min(ancho - MARGEN - ancho_palabra, x))
        dibujar_texto_con_negrita(draw, palabra, x_dibujo, y, tamano, es_negrita)

    return alto_total


def transformacion_8_palabras_dispersas(draw, frase, x_centro, y_top, ancho, paso, img=None):
    """
    Palabras sueltas distribuidas en el espacio blanco sin línea horizontal
    que las organice — como si hubieran caído o flotado, cada una en su
    posición propia. Solo texto, solo blanco alrededor. Más silencio que
    las otras transformaciones — una pausa en el poema.
    """
    palabras = frase.split()
    if not palabras:
        return 30

    random.seed(paso * 29)

    # altura_zona debe ser proporcional al tamaño de fuente máximo para que
    # las 4 palabras no se pisen. Con _e(14..24) el máximo es ~48px por línea.
    tamano_estimado = _e(20)
    altura_zona = max(tamano_estimado * 4 + 30, 90) + random.randint(0, 20)

    n = min(4, len(palabras))
    seleccion = random.sample(palabras, n)
    zonas_x = [MARGEN, ancho // 3, ancho * 2 // 3, ancho - 100]
    # Offsets Y estrictamente crecientes — antes [0, 1/3, 2/3, 1/2] causaba
    # que la 4ª palabra cayera ENTRE la 2ª y la 3ª, produciendo superposición.
    zonas_y_offset = [
        0,
        altura_zona // 4,
        altura_zona // 2,
        3 * altura_zona // 4,
    ]

    for i, palabra in enumerate(seleccion):
        tamano = _e(14 + random.randint(0, 10))
        es_negrita = random.random() < 0.4
        x = zonas_x[i % len(zonas_x)] + random.randint(-10, 10)
        y = y_top + zonas_y_offset[i % len(zonas_y_offset)] + random.randint(0, 12)
        fuente = cargar_fuente(tamano)
        bbox = draw.textbbox((0, 0), palabra, font=fuente)
        x = max(MARGEN, min(ancho - MARGEN - (bbox[2] - bbox[0]), x))
        dibujar_texto_con_negrita(draw, palabra, x, y, tamano, es_negrita)

    return altura_zona


def transformacion_9_mesostico(draw, frase, x_centro, y_top, ancho, paso, img=None):
    """
    El mesostico de John Cage — técnica que Berenguer conocía y que encaja
    con su interés en la topología del texto y el espacio:

    Una palabra "espina" corre verticalmente por una columna fija. Cada
    línea horizontal contiene esa letra de la espina en esa columna exacta,
    con el resto de la palabra desplegándose a los lados. La letra de la
    espina se enfatiza (negrita, tamaño mayor).

    Ejemplo con espina "RÍO":
        fo  R  mateando
        d   Í  stribución
      región O  cidental

    La espina se elige como la palabra más larga de la frase fuente, y las
    líneas horizontales son palabras del mismo texto buscando esa letra.
    """
    palabras = frase.split()
    if len(palabras) < 2:
        return 20

    random.seed(paso * 37)
    # Elegir la espina: la palabra más larga de la frase
    espina = max(palabras, key=len).upper()
    if len(espina) < 2:
        espina = frase[:4].upper().replace(" ", "")

    # Normalizar caracteres acentuados — la espina usa letras individuales
    # que deben renderizarse correctamente con cualquier fuente, incluyendo
    # el fallback bitmap de PIL que no soporta Unicode completo.
    espina = "".join(_normalizar_char(c) for c in espina) or espina

    # Usar solo una porción aleatoria de la espina — no todas las letras,
    # como hacía Berenguer: a veces 2 letras, a veces 4, rara vez más.
    n_letras_disponibles = len(espina)
    n_letras_usar = random.randint(2, min(5, n_letras_disponibles))
    inicio_espina = random.randint(0, max(0, n_letras_disponibles - n_letras_usar))
    espina = espina[inicio_espina:inicio_espina + n_letras_usar]

    # Columna fija donde cae la letra de la espina
    col_espina = ancho // 3 + random.randint(-20, 20)

    tamano_normal = _e(16 + random.randint(0, 4))
    tamano_espina = tamano_normal + 8
    alto_linea = tamano_espina + 6

    y_actual = y_top
    palabras_pool = palabras * 3  # repetir para tener suficiente material

    for i_letra, letra in enumerate(espina):
        # Buscar una palabra del pool que contenga esta letra
        palabra_linea = None
        for pw in palabras_pool:
            if letra.lower() in pw.lower() or letra in pw.upper():
                palabra_linea = pw
                palabras_pool.remove(pw)
                break
        if palabra_linea is None:
            palabra_linea = letra

        # Encontrar la posición de la letra dentro de la palabra
        idx_en_palabra = next(
            (j for j, c in enumerate(palabra_linea.upper()) if c == letra),
            0
        )

        # Dibujar la parte de la palabra ANTES de la letra de la espina
        parte_antes = palabra_linea[:idx_en_palabra]
        parte_despues = palabra_linea[idx_en_palabra + 1:]

        fuente_normal = cargar_fuente(tamano_normal)
        fuente_espina = cargar_fuente(tamano_espina)

        if parte_antes:
            bbox = draw.textbbox((0, 0), parte_antes, font=fuente_normal)
            ancho_antes = bbox[2] - bbox[0]
            x_antes = col_espina - ancho_antes - 2
            if x_antes >= MARGEN:
                draw.text((x_antes, y_actual + (tamano_espina - tamano_normal) // 2),
                          parte_antes, fill=COLOR_TEXTO, font=fuente_normal)

        # Dibujar la letra de la espina, enfatizada
        draw.text((col_espina, y_actual), letra, fill=COLOR_NEGRITA, font=fuente_espina)
        bbox_espina = draw.textbbox((0, 0), letra, font=fuente_espina)
        x_despues = col_espina + (bbox_espina[2] - bbox_espina[0]) + 2

        if parte_despues:
            bbox_despues = draw.textbbox((0, 0), parte_despues, font=fuente_normal)
            ancho_despues = bbox_despues[2] - bbox_despues[0]
            if x_despues + ancho_despues <= ancho - MARGEN:
                draw.text((x_despues, y_actual + (tamano_espina - tamano_normal) // 2),
                          parte_despues, fill=COLOR_TEXTO, font=fuente_normal)

        y_actual += alto_linea

    return y_actual - y_top


def transformacion_10_escalera_letras(draw, frase, x_centro, y_top, ancho, paso, img=None):
    """
    Las letras de una o dos palabras bajan o suben en diagonal letra por letra
    — no palabras enteras en ángulo, sino cada letra individual desplazada un
    paso respecto a la anterior, formando una escalera de signos. El tamaño
    puede variar levemente entre letras para que la escalera respire.
    Como los poemas visuales de Berenguer donde los caracteres se distribuyen
    en el espacio siguiendo una trayectoria, no una línea horizontal.
    """
    palabras = frase.split()
    if not palabras:
        return 20

    random.seed(paso * 41)

    # Elegir 1 o 2 palabras para la escalera
    n_palabras = random.randint(1, min(2, len(palabras)))
    seleccion = random.sample(palabras, n_palabras)
    texto = " ".join(seleccion)

    sentido = random.choice([-1, 1])   # -1 sube, 1 baja
    paso_x = random.randint(8, 16)     # cuánto avanza horizontalmente cada letra
    paso_y = random.randint(6, 13) * sentido  # cuánto sube/baja cada letra

    n_chars = max(1, len(texto.replace(' ', '')))

    # Punto de arranque: si baja empezamos arriba-izquierda; si sube,
    # calculamos el y de inicio para que el extremo superior no supere y_top
    # (antes con y_start = y_top+60 fijo, las letras podían subir por ENCIMA
    # de y_top solapándose con la transformación anterior).
    if sentido == 1:
        x = MARGEN + random.randint(0, 30)
        y = y_top + random.randint(5, 20)
    else:
        # sentido==-1: y decrece, necesitamos y_start suficientemente abajo
        desplazamiento_total = abs(paso_y) * n_chars
        y_start_minimo = y_top + desplazamiento_total + 20
        x = MARGEN + random.randint(0, 30)
        y = y_start_minimo + random.randint(0, 20)

    y_min, y_max = y, y
    for char in texto:
        tamano = _e(14 + random.randint(-2, 6))
        es_negrita = random.random() < 0.3
        if x > ancho - MARGEN - 10:
            break
        if char != ' ':
            char_safe = _normalizar_char(char) or char
            dibujar_texto_con_negrita(draw, char_safe, int(x), int(y), tamano, es_negrita)
        x += paso_x
        y += paso_y
        y_min = min(y_min, y)
        y_max = max(y_max, y)

    return int(y_max - y_top) + 30


TRANSFORMACIONES = [
    transformacion_0_tension,
    transformacion_1_fragmentacion,
    transformacion_2_vertical,
    transformacion_3_escalada,
    transformacion_4_colapso,
    transformacion_5_pliegue,
    transformacion_7_cascada_diagonal,
    transformacion_8_palabras_dispersas,
    transformacion_9_mesostico,
    transformacion_10_escalera_letras,
]
N_TRANSFORMACIONES = len(TRANSFORMACIONES)



def dibujar_frase(draw, frase, y_top, ancho, paso, img=None):
    """Dibuja una frase con la transformación que le toca según `paso`,
    top-anchored en y_top. Devuelve la altura real que ocupó."""
    transformacion = TRANSFORMACIONES[paso % N_TRANSFORMACIONES]
    x_centro = ancho // 2
    altura = transformacion(draw, frase, x_centro, y_top, ancho, paso, img=img)
    return max(15, int(altura))


# ============================================================
# GENERADOR DE SEGMENTOS SECUENCIALES (320x256) — para el transmisor
# ============================================================
def generar_segmento_secuencial(frases, indice_frase=0, offset=0,
                                 alto_objetivo=ALTO_SEGMENTO, ancho=ANCHO,
                                 separador=True):
    """
    Genera UN segmento de (ancho × alto_objetivo) en resolución nativa de
    la impresora (ancho=576 dots por defecto), continuando exactamente donde
    quedó el segmento anterior.  Devuelve (imagen, nuevo_indice_frase,
    nuevo_offset) para persistir y pasar a la siguiente llamada.

    separador=True dibuja una línea gris tenue al pie del segmento para
    delimitar visualmente cada frase recibida en el rollo continuo.

    IMPORTANTE: `indice_frase` cumple doble función — decide qué frase tocar
    (`frases[idx % len(frases)]`) Y alimenta la semilla aleatoria de cada
    transformación (idx crudo, sin módulo).  El valor que se devuelve y
    persiste NUNCA se le aplica % len(frases) para que la aleatoriedad no
    se repita al completar una vuelta del pool.
    """
    # Mayor que la altura máxima posible de una sola frase. La transformación
    # 2 (letras en columna vertical) es la más alta: apila una letra por
    # línea, así que con las frases más largas del pool (~55 caracteres)
    # puede llegar a ~1900px. 900px no alcanzaba para ese caso y volvía a
    # activar el resguardo de recorte (el mismo bug que se está corrigiendo).
    buffer_extra = 3000
    canvas_alto = alto_objetivo + buffer_extra

    # Padding vertical: 20px en blanco arriba y abajo de cada segmento impreso.
    # Garantiza separación visible entre prints consecutivos sin depender del
    # avance de papel del receptor, y aleja el contenido del borde superior.
    PAD_V = 20

    img = Image.new("RGB", (ancho, canvas_alto), COLOR_FONDO)
    draw = ImageDraw.Draw(img)

    y_cursor = PAD_V - offset   # el contenido arranca 20px desde arriba
    idx = indice_frase
    dibujadas = []  # (idx, y_start, altura)

    # ── Ruptura de regularidad visual ───────────────────────────────────
    # Sin jitter, la transformación 2 (letras en columna vertical) siempre
    # cae en las posiciones globales 2, 12, 22, 32… del índice — completamente
    # predecible. El rollo empieza a leerse como: bloque diverso → columna →
    # bloque diverso → columna, con una regularidad casi tipográfica.
    #
    # Dos mecanismos para romperla:
    #
    # 1. JITTER DE INICIO: al comenzar cada segmento se elige un desplazamiento
    #    aleatorio 0..N-1 que se suma al índice de transformación (pero NO al
    #    índice de frase ni al estado guardado). Cada segmento empieza en un
    #    punto distinto del ciclo de 10 transformaciones.
    #
    # 2. PASO VARIABLE: dentro del segmento, el índice de transformación no
    #    siempre avanza de 1 en 1 — a veces salta 2 o 3 posiciones. El ciclo
    #    de 10 se recorre a velocidad irregular: la columna vertical aparece
    #    más o menos según el ritmo del salto, nunca en un compás fijo.
    #    PROB_SALTO controla con qué frecuencia se produce un salto.
    PROB_SALTO = 0.35   # 35% de las transformaciones dan un salto extra de 1-2
    jitter = random.randint(0, N_TRANSFORMACIONES - 1)
    paso_transf = idx + jitter   # índice para seleccionar transformación
    # ────────────────────────────────────────────────────────────────────

    while y_cursor < (alto_objetivo - PAD_V):
        frase = frases[idx % len(frases)]
        y_start = y_cursor
        altura = dibujar_frase(draw, frase, y_cursor, ancho, paso_transf, img=img)
        dibujadas.append((idx, y_start, altura))
        y_cursor = y_start + altura + ESPACIO_ENTRE_FRASES
        idx += 1
        # Paso variable: avanzar 1 posición extra en el ciclo de transformaciones
        # con probabilidad PROB_SALTO, para que la columna vertical no aparezca
        # siempre en el mismo compás del bloque.
        paso_transf += 1 + (random.randint(1, 2) if random.random() < PROB_SALTO else 0)

    # ------------------------------------------------------------------
    # ALTO REAL DEL SEGMENTO: nunca se recorta a mitad de una transformación.
    #
    # Antes: se recortaba siempre a `alto_objetivo` fijo (400px). Si la
    # última transformación dibujada quedaba a medio camino en ese punto,
    # se cortaba en seco (palabras/letras cortadas al FINAL de la
    # impresión) y se guardaba un `offset` para "continuar" esa misma
    # transformación en la próxima llamada.
    #
    # Ese `offset` solo tiene sentido si la próxima llamada dibuja la
    # MISMA lista de frases en el mismo punto de continuidad (como en el
    # modo rollo standalone). Pero receptor_fsk.py arma una lista nueva
    # de una sola frase (el texto recién decodificado) en cada llamada,
    # así que el offset heredado terminaba aplicándose a un texto
    # distinto: el resultado era que las primeras palabras del texto
    # NUEVO se dibujaban desplazadas fuera del área visible — cortadas
    # al INICIO de la impresión siguiente.
    #
    # Ahora: se deja que la última transformación termine completa
    # (el alto del segmento pasa a ser "~alto_objetivo o un poco más",
    # nunca menos) y nunca se devuelve un offset fraccional. Como el
    # rollo se imprime sin cortes entre segmentos, un segmento un poco
    # más alto no afecta nada — solo evita cortar texto a la mitad.
    # ------------------------------------------------------------------
    if dibujadas:
        _, ultimo_y_start, ultima_altura = dibujadas[-1]
        alto_real = ultimo_y_start + ultima_altura + PAD_V
    else:
        alto_real = alto_objetivo
    # Resguardo: no exceder el canvas interno (buffer_extra ya cubre con
    # margen la altura máxima esperada de una transformación).
    alto_real = min(alto_real, canvas_alto)

    img_final = img.crop((0, 0, ancho, alto_real))

    # Línea separadora al pie del segmento (delimita frases en el rollo)
    if separador:
        d_sep = ImageDraw.Draw(img_final)
        d_sep.line([(0, alto_real - 2), (ancho - 1, alto_real - 2)],
                   fill=(200, 200, 200), width=1)

    # Como ninguna transformación queda a medias, siempre se continúa
    # limpio desde la próxima frase/índice, sin offset fraccional.
    nuevo_indice = idx
    nuevo_offset = 0

    return img_final, nuevo_indice, nuevo_offset


def cargar_o_crear_estado_poema(ruta_estado="estado_poema.json"):
    if os.path.exists(ruta_estado):
        with open(ruta_estado) as f:
            return json.load(f)
    return {"indice_frase": 0, "offset": 0}


def guardar_estado_poema(estado, ruta_estado="estado_poema.json"):
    with open(ruta_estado, "w") as f:
        json.dump(estado, f)


# ============================================================
# MODO ROLLO STANDALONE (320 x N metros) — para imprimir directo
# ============================================================
def generar_rollo_por_bloques(frases, metros=30, carpeta_salida="rio_30m"):
    os.makedirs(carpeta_salida, exist_ok=True)

    estado = {"indice_frase": 0, "offset": 0}
    ruta_estado_local = os.path.join(carpeta_salida, "_estado_generacion.json")
    if os.path.exists(ruta_estado_local):
        with open(ruta_estado_local) as f:
            estado = json.load(f)

    metro_inicial = estado.get("metro_actual", 0)

    for m in range(metro_inicial, metros):
        print(f"📦 Generando metro {m + 1}/{metros}...", end=" ", flush=True)

        img_metro = Image.new("RGB", (ANCHO, PIXELS_POR_METRO), COLOR_FONDO)
        draw = ImageDraw.Draw(img_metro)
        y_cursor = 0
        idx = estado["indice_frase"]
        y_cursor = -estado["offset"]

        dibujadas = []
        while y_cursor < PIXELS_POR_METRO:
            frase = frases[idx % len(frases)]
            y_start = y_cursor
            altura = dibujar_frase(draw, frase, y_cursor, ANCHO, idx, img=img_metro)
            dibujadas.append((idx, y_start, altura))
            y_cursor = y_start + altura + ESPACIO_ENTRE_FRASES
            idx += 1

        # borde de metro
        draw.line([(0, 0), (ANCHO, 0)], fill=(180, 180, 180), width=3)
        draw.line([(0, PIXELS_POR_METRO - 1), (ANCHO, PIXELS_POR_METRO - 1)], fill=(180, 180, 180), width=3)
        fuente_metro = cargar_fuente(14)
        draw.text((5, 5), f"METRO {m + 1}", fill=(150, 150, 150), font=fuente_metro)

        nombre = f"metro_{m + 1:03d}_de_{metros:03d}.png"
        img_metro.save(os.path.join(carpeta_salida, nombre))
        print(f"✅ {nombre}")

        # continuar exactamente donde quedó, para el próximo metro
        nuevo_indice, nuevo_offset = None, 0
        for idx_d, y_start, altura in dibujadas:
            if y_start <= PIXELS_POR_METRO < y_start + altura:
                nuevo_indice, nuevo_offset = idx_d, PIXELS_POR_METRO - y_start
                break
        if nuevo_indice is None:
            nuevo_indice = dibujadas[-1][0] + 1 if dibujadas else idx
            nuevo_offset = 0

        estado = {"indice_frase": nuevo_indice, "offset": nuevo_offset, "metro_actual": m + 1}
        with open(ruta_estado_local, "w") as f:
            json.dump(estado, f)

    print(f"\n✅ {metros} metros generados en: {carpeta_salida}/")


# ============================================================
# MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="Método Berenguer — poema visual generativo")
    parser.add_argument("--metros", type=int, default=30)
    parser.add_argument("--frases-archivo", default="frases.txt")
    parser.add_argument("--out", default="rio_30m")
    parser.add_argument("--prueba-segmento", action="store_true",
                         help="Genera un solo segmento de 320x256 de prueba (segmento_prueba.png)")
    args = parser.parse_args()

    frases = cargar_frases(args.frases_archivo)
    print(f"🎨 {len(frases)} frases cargadas desde '{args.frases_archivo}'")
    print(f"🔄 {N_TRANSFORMACIONES} transformaciones (incluye 2 diagonales nuevas)")

    if args.prueba_segmento:
        estado = cargar_o_crear_estado_poema()
        img, nuevo_idx, nuevo_off = generar_segmento_secuencial(
            frases, estado["indice_frase"], estado["offset"]
        )
        img.save("segmento_prueba.png")
        guardar_estado_poema({"indice_frase": nuevo_idx, "offset": nuevo_off})
        print(f"✅ segmento_prueba.png generado ({ANCHO}×{ALTO_SEGMENTO})")
        print(f"   próximo estado: frase {nuevo_idx}, offset {nuevo_off}px")
        return

    generar_rollo_por_bloques(frases, metros=args.metros, carpeta_salida=args.out)


if __name__ == "__main__":
    main()
