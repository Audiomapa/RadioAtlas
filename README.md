# RadioAtlas

Dispositivo de creación de un poema visual impreso utilizando transmisión de tonos FSK entre dos Raspberry Pi separadas físicamente en la sala. En el receptor el texto se transforma visualmente usando el **Método Berenguer** —inspirado en el libro *Composición de Lugar* de la poeta uruguaya Carmen Berenguer.

---

## Versiones

| Versión | Protocolo | Salida | Contexto | Estado |
|---------|-----------|--------|----------|--------|
| `v1-sstv` | SSTV — imágenes por audio | — | — | Abandonado: canal acústico degrada imagen hasta ilegibilidad |
| `v2-fsk` | FSK 50 baud — texto por audio | Impresora térmica de rollo continuo | **RadioAtlas: la cosmonauta Berenguer · Festival de la Imagen · Manizales · 2026** | Exhibido |
| `v2-fsk + display` | FSK 50 baud — texto por audio | Pantallas pygame (emisor + receptor) | **RadioAtlas: cartografía y supervivencia · CAC Cerrillos** | En desarrollo |

---

## Arquitectura

```
PI-A · radioatlasemisor (transmisor)       PI-B · radioatlasreceptor (receptor)
────────────────────────────────           ────────────────────────────────────
frases.txt  (versos del poema)             Roland R-07  (USB audio input)
transmisor_fsk_v2.py                       receptor_fsk_v2.py
→ modulación FSK 1200/2200 Hz             → Goertzel vectorizado (numpy)
→ 50 baud · preámbulo 2s                  → detección de preámbulo por energía
→ aplay → parlante                        → 8 offsets sincronización + CRC
display_emisor.py                          → metodo_berenguer_v2.py
→ telemetría en verde                      display_receptor.py  ← en desarrollo
→ ASCII art typewriter                     → visualización Método Berenguer

        ──── canal acústico ───►
```

---

## Versión actual — CAC Cerrillos

**RadioAtlas: cartografía y supervivencia**  
Centro de Arte Contemporáneo Cerrillos · 2026

El texto del poema sigue viajando por audio FSK entre las dos Pis. El cambio respecto al Festival de la Imagen es la salida: en vez de imprimirse en papel térmico, el Método Berenguer se muestra en pantallas conectadas a cada Pi.

### Emisor (Pi-A · radioatlasemisor)

- `transmisor_fsk_v2.py` — FSK 50 baud, deriva + corte de frases, SILENCIO=180s entre envíos
- `display_emisor.py` — pantalla pygame: telemetría verde en tiempo real + ASCII art typewriter al entrar en silencio
- `start_display.sh` — lanzador del display (xinit, xrandr portrait, pygame)
- Servicios systemd: `radioatlas-display.service` + `transmisor.service` (auto-start al encender)
- Audio: `SDL_AUDIODRIVER=dummy` libera ALSA para `aplay`

### Timers display

| Parámetro | Valor | Descripción |
|-----------|-------|-------------|
| `LOG_IDLE_SECS` | 20s | Silencio antes de mostrar imagen |
| `ASCII_BUILD_SECS` | 120s | Tiempo en construir imagen (typewriter) |
| `ASCII_HOLD_SECS` | 180s | Imagen completa visible antes de ciclar |

### Receptor (Pi-B · radioatlasreceptor)

- `receptor_fsk_v2.py` — demodulador FSK
- `metodo_berenguer_v2.py` — 10 transformaciones tipográficas
- `display_receptor.py` — **en desarrollo**

---

## Método Berenguer

Diez transformaciones tipográficas aplicadas secuencialmente al texto recibido. El índice **nunca se resetea** — la secuencia no se repite aunque el pool de frases dé una vuelta completa.

| idx | transformación | descripción |
|-----|---------------|-------------|
| 0 | `tension` | palabras en línea, una destacada en bold |
| 1 | `fragmentacion` | frase dividida en 2–3 fragmentos a distintos tamaños |
| 2 | `vertical` | letras en columna vertical |
| 3 | `escalada` | palabras que crecen de pequeño a grande |
| 4 | `colapso` | palabras que menguan de grande a pequeño |
| 5 | `pliegue` | primera mitad normal, segunda girada 180° |
| 6 | `cascada_diagonal` | palabras en diagonal por el plano |
| 7 | `palabras_dispersas` | 4 palabras dispersas en espacio blanco |
| 8 | `mesostico` | mesostico tipo John Cage con palabra espina |
| 9 | `escalera_letras` | letras individuales en escalera diagonal |

---

## Archivos

| Archivo | Descripción | Pi |
|---------|-------------|-----|
| `transmisor_fsk_v2.py` | Modulador FSK + reproductor | emisor |
| `display_emisor.py` | Pantalla emisor (telemetría + ASCII art) | emisor |
| `start_display.sh` | Lanzador pygame (xinit + xrandr) | emisor |
| `receptor_fsk_v2.py` | Demodulador FSK | receptor |
| `metodo_berenguer_v2.py` | Transformaciones tipográficas | receptor |
| `display_receptor.py` | Pantalla receptor *(en desarrollo)* | receptor |
| `frases.txt` | Versos del poema fuente | ambas |

**No versionados:**
- `estado_poema.json` / `estado_fsk.json` — estado de runtime
- `fsk_output_berenguer/*.wav` — audio generado
- `banco_imagenes/` — imágenes ASCII para display emisor

---

## Dependencias

```bash
# emisor (radioatlasemisor)
pip install numpy scipy
sudo apt install python3-pygame

# receptor (radioatlasreceptor · entorno_arte)
pip install pillow numpy sounddevice
```

---

## Pendientes antes de la exposición

- [ ] Restaurar horario: `HORA_INICIO = dtime(9, 0)` / `HORA_FIN = dtime(20, 0)` en `transmisor_fsk_v2.py`
- [ ] Desarrollar `display_receptor.py`
- [ ] Subir imágenes finales a `banco_imagenes/` en Pi emisor
- [ ] Sincronizar archivos receptor desde Pi-B al repo
