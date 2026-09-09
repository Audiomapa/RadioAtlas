# Sistema FSK — RadioAtlas

Transmisión de texto por canal acústico usando modulación por desplazamiento de frecuencia (FSK).  
Parte de la instalación **Poema Visual Berenguer** · Festival de la Imagen · Manizales · 2026.

---

## Por qué FSK

El sistema empezó transmitiendo imágenes con SSTV. La reverberación y el ruido de sala degradaban la imagen hasta hacerla ilegible. El cambio conceptual fue simple: **en vez de transmitir la imagen, transmitir el texto y generar la imagen en el receptor**.

FSK codifica cada bit como uno de dos tonos de audio. A 50 baudios y con 1000 Hz de separación entre tonos, el sistema sobrevive canal acústico de sala con facilidad.

---

## Señal

```
Mark  (bit 1) = 1200 Hz
Space (bit 0) = 2200 Hz
Baudrate      = 50 baud  (20ms por símbolo)
Sample rate   = 44100 Hz
Muestras/bit  = 882
```

### Estructura de cada paquete

```
┌─────────────┬────────┬────────┬────────┬─────────────────┐
│ Preámbulo   │  Sync  │  Len   │  CRC   │    Payload      │
│    2s       │  2B    │  2B    │  2B    │   texto UTF-8   │
│ alternancia │  ABCD  │ BE u16 │CRC-16  │                 │
│ mark/space  │        │        │        │                 │
└─────────────┴────────┴────────┴────────┴─────────────────┘
```

El preámbulo de 2 segundos permite al receptor calibrar el nivel de energía y detectar el inicio de la señal antes de intentar decodificar bits. La detección se hace por energía (no por bits), lo que la hace inmune al problema de desfase de símbolo.

---

## Problema de sincronización y solución

El transmisor y el receptor no comparten reloj. Los bloques de 882 muestras del receptor pueden estar desfasados hasta 441 muestras (medio símbolo) respecto al transmisor.

Con desfase de exactamente medio símbolo, el detector Goertzel ve 50% de energía en 1200 Hz y 50% en 2200 Hz simultáneamente en cada bloque — ambas frecuencias iguales — y la decisión de bit es arbitraria.

**Solución**: el receptor captura las muestras crudas durante toda la transmisión. Al terminar, `decodificar_con_offset()` prueba 8 alineaciones distintas desplazadas 110 muestras (= 1/8 símbolo) entre sí. La primera que produce CRC válido es la correcta.

---

## Goertzel vectorizado

El algoritmo Goertzel clásico (recursivo en Python puro) tardaba ~22 segundos en procesar 8 offsets en la Raspberry Pi. Se reemplazó por producto punto numpy, matemáticamente equivalente:

```python
# Precomputado una sola vez al arrancar
_GCOS[freq] = np.cos(2π · k/N · np.arange(N))
_GSIN[freq] = np.sin(2π · k/N · np.arange(N))

# Por cada bloque
re     = np.dot(muestras, _GCOS[freq])
im     = np.dot(muestras, _GSIN[freq])
energia = (re² + im²) / N²
```

Speedup medido: **97×**. En la Pi: de ~22s a menos de 2s.

---

## Archivos

### `transmisor_fsk.py` — corre en RadioAtlas (`192.168.20.33`)

Lee `frases.txt` línea por línea, codifica cada frase como audio FSK y la reproduce por el parlante. Guarda el índice de frase actual en `estado_fsk.json` para retomar donde quedó si se reinicia.

```bash
python3 transmisor_fsk.py              # bucle continuo
python3 transmisor_fsk.py --prueba    # un envío sin modificar estado
python3 transmisor_fsk.py --texto "x" # texto libre
python3 transmisor_fsk.py --reset     # reinicia desde la frase 0
```

### `receptor_fsk.py` — corre en radioatlasreceptor (`192.168.20.36`)

Captura audio del Roland R-07 (USB), detecta señal por energía, demodula FSK, verifica CRC, pasa el texto a `metodo_berenguer.py` y envía la imagen resultante a la impresora térmica.

```bash
source ~/entorno_arte/bin/activate
python3 receptor_fsk.py
```

---

## Parámetros críticos del receptor

| Parámetro | Valor | Nota |
|---|---|---|
| `DISPOSITIVO_ENTRADA` | `1` | Roland R-07 como USB audio |
| `CANALES_ENTRADA` | `2` | estéreo |
| `UMBRAL_ENERGIA` | `0.00008` | ruido de sala máx 0.000033 → margen 2.4× |
| `UMBRAL_DECISION` | `1.05` | ratio mark/space para decisión de bit |
| `INVERTIR_PARA_PAPEL_CAIDO` | `True` | rota 180° antes de imprimir |

`UMBRAL_ENERGIA` es el parámetro más sensible al entorno físico. Si el receptor no detecta señal, bajarlo. Si detecta ruido como señal, subirlo. El valor actual fue calibrado con el ruido de sala del espacio de instalación.

---

## Parámetros de la impresora

| Parámetro | Valor |
|---|---|
| `IMPRESORA_VENDOR_ID` | `0x0483` |
| `IMPRESORA_PRODUCT_ID` | `0x5720` |
| `IMPRESORA_IN_EP` | `0x81` |
| `IMPRESORA_OUT_EP` | `0x03` |
| `IMPRESORA_ANCHO_DOTS` | `576` (80mm · 203dpi) |
| `CORTAR_ENTRE_SEGMENTOS` | `False` |

```bash
# udev — acceso sin sudo
sudo nano /etc/udev/rules.d/99-escpos.rules
# SUBSYSTEM=="usb", ATTR{idVendor}=="0483", ATTR{idProduct}=="5720", MODE="0666"

# usblp en blacklist (impide que el kernel capture el dispositivo)
sudo nano /etc/modprobe.d/blacklist-usblp.conf
# blacklist usblp
```

El warning `media.width.pixel field not set` que aparece en el log es inofensivo — ignorar.

---

## Instalación de dependencias

```bash
# Fuente con soporte Unicode completo (necesaria para ó, ú, ñ, etc.)
sudo apt install -y fonts-dejavu-core

# Entorno virtual del receptor
python3 -m venv ~/entorno_arte
source ~/entorno_arte/bin/activate
pip install pillow numpy sounddevice python-escpos

# Entorno del transmisor
python3 -m venv ~/rio_sstv/entorno_arte
source ~/rio_sstv/entorno_arte/bin/activate
pip install numpy scipy sounddevice
```

---

## MD5s — versión actual

```
821f504b2635423026aaad9a3ce1eb15  metodo_berenguer.py
5c24991c5798160c4afcd7e40b996190  receptor_fsk.py
a88a43326ab527c5da44bc0763604ed2  transmisor_fsk.py
```

```bash
# Verificar en las Pis
ssh qzzk@192.168.20.33 "md5sum ~/rio_sstv/metodo_berenguer.py ~/rio_sstv/transmisor_fsk.py"
ssh qzzk@192.168.20.36 "md5sum ~/metodo_berenguer.py ~/receptor_fsk.py"
```
