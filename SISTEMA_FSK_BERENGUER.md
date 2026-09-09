# Sistema FSK Poema Visual Berenguer — Resumen técnico completo

## Prompt para nuevo chat

```
Estoy trabajando en un sistema de transmisión de texto vía audio FSK para
una instalación artística. El sistema tiene dos Raspberry Pi: una transmisora
(RadioAtlas · 192.168.20.33) y una receptora (radioatlasreceptor · 192.168.20.36).
El texto de un poema viaja por tonos de audio FSK, se decodifica en la Pi
receptora y se transforma en poema visual usando el "Método Berenguer"
(inspirado en la poeta chilena Carmen Berenguer), que se imprime en rollo
continuo en una impresora térmica. El papel cae al suelo desde la impresora.

Llevo varias sesiones de desarrollo y debugging. Te paso el resumen técnico
completo del sistema en su estado actual para que puedas continuar.
```

---

## Arquitectura general

```
PI-A (RadioAtlas · 192.168.20.33)           PI-B (radioatlasreceptor · 192.168.20.36)
──────────────────────────────────          ──────────────────────────────────────────
frases.txt (24 líneas)                      Roland R-07 (USB audio input)
estado_fsk.json                             → Goertzel vectorizado (numpy)
transmisor_fsk.py                           → detección de preámbulo por energía
→ FSK audio 1200/2200 Hz, 50 baud          → captura muestras crudas
→ paplay/aplay → parlante                  → 8 offsets de sincronización → CRC
                                            → texto decodificado
         ─────── canal acústico ──────►    → metodo_berenguer.py
                                            → imagen 576×N px (alto variable)
                                            → rotate(180°) para papel caído
                                            → impresora térmica USB (rollo 80mm)
```

---

## Protocolo FSK

| Parámetro | Valor |
|---|---|
| Mark (bit 1) | 1200 Hz |
| Space (bit 0) | 2200 Hz |
| Baudrate | 50 baud (20ms/símbolo) |
| Sample rate | 44100 Hz |
| Muestras/símbolo | 882 |
| Preámbulo | 2s de alternancia mark/space |
| Sync word | 0xAB 0xCD |
| Estructura paquete | [2s preámbulo][sync 2B][len 2B BE][CRC-16 2B][UTF-8 payload] |
| Silencio entre envíos | configurable — `SILENCIO_ENTRE_ENVIOS_SEG` |

---

## Hardware confirmado

### Receptor (radioatlasreceptor)
- `DISPOSITIVO_ENTRADA = 1` (Roland R-07 grabadora como interfaz USB)
- `CANALES_ENTRADA = 2` (estéreo)
- `UMBRAL_ENERGIA = 0.00008` (ruido sala máx 0.000033 → margen 2.4×)
- `UMBRAL_DECISION = 1.05`

### Impresora térmica
- `IMPRESORA_VENDOR_ID = 0x0483`
- `IMPRESORA_PRODUCT_ID = 0x5720` (lsusb: STMicroelectronics Mass Storage Device)
- `IMPRESORA_IN_EP = 0x81`
- `IMPRESORA_OUT_EP = 0x03`
- `IMPRESORA_ANCHO_DOTS = 576` (papel 80mm a 203dpi)
- `CORTAR_ENTRE_SEGMENTOS = False` (rollo continuo — el papel cae al suelo)
- `IMPRIMIR_AUTOMATICAMENTE = True`
- `INVERTIR_PARA_PAPEL_CAIDO = True` (imagen rotada 180° antes de imprimir)
- Regla udev: `/etc/udev/rules.d/99-escpos.rules`
- usblp en blacklist: `/etc/modprobe.d/blacklist-usblp.conf`
- Warning inofensivo: "media.width.pixel field not set" → ignorar

---

## Archivos en cada Pi

### RadioAtlas (transmisor) — `~/rio_sstv/`
```
transmisor_fsk.py
metodo_berenguer.py
frases.txt
estado_fsk.json          ← estado de transmisión (indice_frase, envio)
fsk_output_berenguer/    ← WAVs generados (no versionados)
```

### radioatlasreceptor (receptor) — `~/`
```
receptor_fsk.py
metodo_berenguer.py
frases.txt
entorno_arte/            ← virtualenv con python-escpos, pillow, sounddevice, numpy
sstv_recibido/           ← PNGs generados + estado_berenguer.json (no versionados)
```

Arranque:
```bash
# Receptor
source ~/entorno_arte/bin/activate
python3 receptor_fsk.py

# Transmisor
cd ~/rio_sstv && source entorno_arte/bin/activate
python3 transmisor_fsk.py
# o para prueba sin modificar estado:
python3 transmisor_fsk.py --prueba
# o para texto específico:
python3 transmisor_fsk.py --texto "frase de prueba"
```

---

## Decisiones técnicas clave

### Por qué FSK y no SSTV
El sistema empezó con SSTV. Falló porque transmitir imágenes por canal acústico
de sala (reverberación, ruido) degrada la imagen hasta ilegibilidad. FSK transmite
texto — el receptor aplica el Método Berenguer localmente. Mucho más robusto.

### Problema de sincronización de símbolo (resuelto)
El transmisor y receptor no comparten reloj. Los bloques de 882 muestras del
receptor pueden estar desfasados hasta 441 muestras (medio símbolo) respecto al
transmisor. Con desfase de exactamente medio símbolo, Goertzel ve 50% mark +
50% space en cada bloque → ambas frecuencias iguales → decisión de bit errónea
→ `alt=0/24` en el log (todos los bits iguales).

**Solución**: capturar muestras crudas durante la recepción. Al final de la señal,
`decodificar_con_offset()` prueba 8 alineaciones distintas (desplazadas 110 muestras
= 1/8 símbolo). La primera que da CRC OK es la válida.

### Detección de preámbulo por energía (no por bits)
El intento de detectar el preámbulo contando alternaciones de bit fracasaba porque
el problema de offset causaba que todos los bits detectados fueran iguales.
Solución: preamble = `energia_count >= 15` bloques consecutivos con energía
`> UMBRAL_ENERGIA`. No depende de decodificar bits correctamente.

### Goertzel vectorizado (numpy)
El Goertzel recursivo en Python puro tomaba ~22 segundos para procesar 8 offsets
en la Pi. Se reemplazó por un producto punto numpy equivalente matemáticamente:

```python
_GCOS[freq] = np.cos(2π * k/N * arange(N))   # precomputado una vez
_GSIN[freq] = np.sin(2π * k/N * arange(N))

re = np.dot(muestras, _GCOS[freq])
im = np.dot(muestras, _GSIN[freq])
energia = (re² + im²) / N²
```
Speedup medido: 97×. En la Pi: de ~22s a < 2s.

### Fuente tipográfica
Requiere DejaVu Sans instalada. Sin ella, PIL usa fuente bitmap que no tiene
glifos para ó, ú, ñ → aparecen como "X" o cajas en la impresión.
```bash
sudo apt install -y fonts-dejavu-core
```

---

## Método Berenguer (metodo_berenguer.py)

### Constantes principales
```python
ANCHO = 576              # ancho nativo impresora (era 320 para SSTV)
ALTO_SEGMENTO = 400      # altura mínima objetivo — ver nota sobre alto variable
ESCALA_FUENTE = 2.0      # rango efectivo: 20px / 36px / 52px
MARGEN = 20              # margen lateral (mantiene texto en área imprimible)
ESPACIO_ENTRE_FRASES = 28
PAD_V = 20               # padding vertical top/bottom en cada segmento
buffer_extra = 3000      # canvas interno — mayor que la transf. más alta posible
```

### Alto variable del segmento
El segmento ya **no se recorta a una altura fija**. Cada transformación se
deja terminar completamente antes de cerrar el segmento; el alto real es
`y_start_última_transf + altura_última_transf + PAD_V`.

Con las 24 frases del poema actual, el rango observado es:

| | px | mm (203 dpi) |
|---|---|---|
| Mínimo | ~395 | ~5 cm |
| Máximo | ~1960 | ~25 cm (transformación vertical, frases largas) |
| Promedio | ~825 | ~10 cm |

La variabilidad de longitud en el rollo es intencional — es parte de la pieza.

### Las 10 transformaciones (TRANSFORMACIONES[idx % 10])
| idx | función | descripción |
|---|---|---|
| 0 | `transformacion_0_tension` | palabras en línea, una destacada en bold |
| 1 | `transformacion_1_fragmentacion` | frase dividida en 2-3 fragmentos a distintos tamaños |
| 2 | `transformacion_2_vertical` | letras en columna vertical |
| 3 | `transformacion_3_escalada` | palabras que crecen de pequeño a grande |
| 4 | `transformacion_4_colapso` | palabras que menguan de grande a pequeño |
| 5 | `transformacion_5_pliegue` | primera mitad normal, segunda girada 180° |
| 6 | `transformacion_7_cascada_diagonal` | palabras en diagonal por el plano |
| 7 | `transformacion_8_palabras_dispersas` | 4 palabras dispersas en espacio blanco |
| 8 | `transformacion_9_mesostico` | mesostico tipo John Cage con palabra espina |
| 9 | `transformacion_10_escalera_letras` | letras individuales en escalera diagonal |

### Flujo por frase recibida
```
texto_rx → frases_ciclo = [texto_rx]
→ generar_segmento_secuencial(frases_ciclo, indice_frase, offset=0)
   → aplica transformaciones secuenciales sobre la frase
   → cada transformación termina completa (no se recorta)
   → segmento 576×N px con PAD_V=20 arriba/abajo
   → separador gris al pie
   → devuelve siempre nuevo_offset=0 (no hay cortes fraccionales)
→ binarizar (normalización por fila → percentil 90 como blanco)
→ MedianFilter(3)
→ rotate(180°) si INVERTIR_PARA_PAPEL_CAIDO=True
→ impresora.image() + impresora.ln(4)
→ guardar estado {indice_frase, offset=0, segmento}
```

### Estado del poema
Estado inicial: `{"indice_frase": 155, "offset": 60}`
Guardado en: `sstv_recibido/estado_berenguer.json`
El índice nunca se resetea con `% len(frases)` — así la aleatoriedad
no se repite cuando el pool da una vuelta completa.
El campo `offset` siempre se guarda como `0` desde la versión actual.

### Orientación de impresión
La impresora alimenta el papel hacia abajo. El papel cae y se acumula en el
suelo. El primer elemento impreso (parte superior de la imagen) queda más
lejos de la impresora — al leer el rollo caído queda invertido. Se corrige
con `img_final.rotate(180)` en `receptor_fsk.py` justo antes de
`impresora.image()`. El PNG guardado en `sstv_recibido/` **no** se rota
(queda en orientación normal para revisar en pantalla).

El flag `INVERTIR_PARA_PAPEL_CAIDO = True` controla este comportamiento;
cambiarlo a `False` si se modifica el montaje físico de la impresora.

---

## Bugs corregidos (historial completo)

| Bug | Causa | Fix |
|---|---|---|
| Texto demasiado pequeño | ANCHO=320, ESCALA_FUENTE=1.8 → upscale 1.8× | ANCHO=576, ESCALA_FUENTE=2.0 |
| Fuente compound | ESCALA_FUENTE≠1.0 + dobles `_e(_e(x))` en t3/t4/t5/t8/t10 | Eliminar doble `_e()` |
| X en acentos | DejaVu no instalada → PIL bitmap sin Unicode | `apt install fonts-dejavu-core` |
| alt=0/24 | Desfase de símbolo entre TX y RX | `decodificar_con_offset()` 8 offsets |
| 22s de procesamiento | Goertzel en Python puro | Vectorizar con numpy |
| Texto pegado (t0,t1,t3,t4) | Espaciado 2-6px imperceptible a 2× | `max(14-16, tamano//2-3)` |
| Palabras fuera del borde | MARGEN=8, sin check en t1 y t9 | MARGEN=20, check por palabra |
| Sin espacio entre segmentos | No había avance de papel | `ln(4)` + PAD_V=20 |
| `bits_rx` congelado en 60 | UMBRAL_ENERGIA=0.0005 bloqueaba señal | 0.0005 → 0.00008 |
| **Palabras cortadas al inicio/final** | Crop fijo a 400px partía transformaciones a mitad; el `offset` heredado se aplicaba a la frase siguiente (texto distinto) | `generar_segmento_secuencial()` deja terminar la última transformación completa; `buffer_extra` 900→3000; siempre devuelve `offset=0` |
| **Impresión al revés en rollo caído** | El papel cae al suelo; el inicio del segmento queda más lejos y se lee invertido | `INVERTIR_PARA_PAPEL_CAIDO = True` + `rotate(180°)` en `imprimir()` antes de `impresora.image()` |

---

## MD5s — versión actual

```
821f504b2635423026aaad9a3ce1eb15  metodo_berenguer.py
5c24991c5798160c4afcd7e40b996190  receptor_fsk.py
a88a43326ab527c5da44bc0763604ed2  transmisor_fsk.py
```

Para verificar en las Pis:
```bash
ssh qzzk@192.168.20.33 "md5sum ~/rio_sstv/metodo_berenguer.py ~/rio_sstv/transmisor_fsk.py"
ssh qzzk@192.168.20.36 "md5sum ~/metodo_berenguer.py ~/receptor_fsk.py"
```

---

## Estado al cierre de sesión — 09/09/2026

Sistema funcionando en Festival de la Imagen · Manizales.

Bugs resueltos en esta sesión:
- Palabras cortadas al inicio y final de cada impresión (crop fijo + offset heredado)
- Impresión invertida en el rollo que cae al suelo

Rollo físico: la longitud de cada segmento es variable y eso es intencional.
La transformación más larga es `transformacion_2_vertical` (~25cm en frases largas).
