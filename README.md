# RadioAtlas — Poema Visual Berenguer

Instalación artística de transmisión de poesía por canal acústico.

El texto de un poema viaja como tonos de audio FSK entre dos Raspberry Pi
separadas físicamente en la sala. En la Pi receptora el texto se transforma
en imagen tipográfica usando el **Método Berenguer** —inspirado en el libro Composición de Lugar de la poeta
uruguaya Carmen Berenguer— y se imprime en tiempo real en una impresora
térmica de rollo continuo. El papel cae al suelo a medida que se imprime.

---

## Arquitectura

```
PI-A · RadioAtlas (transmisor)          PI-B · radioatlasreceptor (receptor)
──────────────────────────────          ─────────────────────────────────────
frases.txt  (24 versos del poema)       Roland R-07  (USB audio input)
transmisor_fsk.py                       receptor_fsk.py
→ modulación FSK 1200/2200 Hz           → Goertzel vectorizado (numpy)
→ 50 baud · preámbulo 2s               → detección de preámbulo por energía
→ paplay/aplay → parlante              → 8 offsets de sincronización + CRC
                                        → metodo_berenguer.py
        ──── canal acústico ───►        → imagen 576×N px (alto variable)
                                        → rotación 180° para papel caído
                                        → impresora térmica USB · rollo 80mm
```

---

## Versiones del sistema

| Tag | Protocolo | Estado |
|-----|-----------|--------|
| `v1-sstv` | SSTV — imágenes por audio | Abandonado: canal acústico de sala degrada la imagen hasta ilegibilidad |
| `v2-fsk`  | FSK — texto por audio, imagen generada en el receptor | **En uso** |

La decisión de pasar de SSTV a FSK fue central: en vez de transmitir la imagen
(frágil ante ruido y reverberación), se transmite el texto crudo y el Método
Berenguer se aplica localmente en el receptor. El canal acústico solo tiene que
sobrevivir texto ASCII — mucho más robusto.

---

## Archivos

| Archivo | Descripción | Pi |
|---------|-------------|-----|
| `transmisor_fsk.py` | Modulador FSK + reproductor de audio | RadioAtlas |
| `receptor_fsk.py` | Demodulador FSK + control de impresora | radioatlasreceptor |
| `metodo_berenguer.py` | Generador de imagen tipográfica | ambas |
| `frases.txt` | 24 versos del poema fuente | ambas |

**No versionados** (datos de runtime):
- `estado_fsk.json` — índice de frase y número de envío (transmisor)
- `sstv_recibido/estado_berenguer.json` — índice de transformación (receptor)
- `fsk_output_berenguer/*.wav` — audio generado
- `sstv_recibido/*.png` — imágenes impresas

---

## Arranque

```bash
# Receptor (192.168.20.36)
source ~/entorno_arte/bin/activate
python3 receptor_fsk.py

# Transmisor (192.168.20.33)
cd ~/rio_sstv && source entorno_arte/bin/activate
python3 transmisor_fsk.py

# Prueba sin modificar estado
python3 transmisor_fsk.py --prueba

# Texto específico
python3 transmisor_fsk.py --texto "frase de prueba"
```

---

## Método Berenguer

Diez transformaciones tipográficas que se aplican secuencialmente al texto
recibido. El índice de transformación **nunca se resetea** — la secuencia
no se repite aunque el pool de frases dé una vuelta completa.

| idx | transformación | descripción |
|-----|---------------|-------------|
| 0 | `tension` | palabras en línea, una destacada en bold |
| 1 | `fragmentacion` | frase dividida en 2–3 fragmentos a distintos tamaños |
| 2 | `vertical` | letras en columna vertical (puede ocupar hasta ~25cm de papel) |
| 3 | `escalada` | palabras que crecen de pequeño a grande |
| 4 | `colapso` | palabras que menguan de grande a pequeño |
| 5 | `pliegue` | primera mitad normal, segunda girada 180° |
| 6 | `cascada_diagonal` | palabras en diagonal por el plano |
| 7 | `palabras_dispersas` | 4 palabras dispersas en espacio blanco |
| 8 | `mesostico` | mesostico tipo John Cage con palabra espina |
| 9 | `escalera_letras` | letras individuales en escalera diagonal |

El alto del segmento impreso es **variable**: cada transformación termina
completa antes de cortar (nunca se trunca texto a mitad). El rango real con
las frases del poema actual va de ~5cm a ~25cm por segmento.

---

## Dependencias

```bash
# En ambas Pis
sudo apt install -y fonts-dejavu-core

# En radioatlasreceptor (entorno_arte)
pip install pillow numpy sounddevice python-escpos
```

```bash
# En RadioAtlas (rio_sstv)
pip install numpy scipy sounddevice
```

---

## Hardware confirmado

- **Audio entrada**: Roland R-07 como interfaz USB (`DISPOSITIVO_ENTRADA = 1`)
- **Impresora**: `VID 0x0483 / PID 0x5720` (STMicroelectronics) · papel 80mm · 203dpi · 576 dots
- **udev**: `/etc/udev/rules.d/99-escpos.rules`
- **usblp**: en blacklist (`/etc/modprobe.d/blacklist-usblp.conf`)

---

## Festival de la Imagen · Manizales · 2026
