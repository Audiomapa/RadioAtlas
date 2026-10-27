# RadioAtlas — Poema Visual Berenguer
## Documentación técnica · Festival de la Imagen, Manizales 2026

---

## 1. Descripción del proyecto

RadioAtlas es una instalación de arte sonoro que transmite el Poema Visual Berenguer vía FSK (Frequency Shift Keying) sobre audio analógico.

**Arquitectura de producción:**
- **Pi 3B Emisor**: genera frases poéticas por Método Berenguer, las codifica en FSK 50 baud (mark 1200 Hz / space 2200 Hz) y las reproduce por parlante.
- **Pi 3B Receptor**: captura audio del ambiente, decodifica FSK y muestra las frases recibidas en su pantalla.
- Ambas pantallas (7 pulgadas, portrait) muestran el banco de imágenes en idle y telemetría en negro completo durante transmisión/recepción.

**Desarrollo actual:** el transmisor corre en Pi 4 (192.168.0.23). Próximo paso: migrar al Pi 3B.

---

## 2. Arquitectura de hardware

| Componente | Modelo | Rol | IP (actual) |
|---|---|---|---|
| Pi 4 | Raspberry Pi 4 | Emisor FSK + Display (desarrollo) | 192.168.0.23 |
| Pi 3B #1 | Raspberry Pi 3B | Emisor FSK + Display (producción) | 192.168.0.20 (a asignar) |
| Pi 3B #2 | Raspberry Pi 3B | Receptor FSK + Display | 192.168.0.20 (a asignar) |
| Pantalla | 7 pulgadas HDMI | Display portrait 600×1024 | — |
| Audio out | USB o 3.5mm | Salida FSK (emisor) | — |
| Audio in | Micrófono USB | Entrada FSK (receptor) | — |

**Resolución de pantalla:** el Pi sale en 1024×600 landscape por defecto. Con `xrandr --rotate right` se rota a 600×1024 portrait. pygame detecta automáticamente con `set_mode((0,0), FULLSCREEN)`.

El banco de imágenes vive en `~/banco_imagenes/` (JPG/PNG). Debe copiarse a ambos Pi 3B.

---

## 3. Software y archivos principales

| Archivo | Ubicación | Descripción |
|---|---|---|
| `display_emisor.py` | `~/rio_sstv/` | Display pygame: imágenes + telemetría |
| `transmisor_fsk_v2.py` | `~/rio_sstv/` | Generador FSK Berenguer |
| `display_estado.json` | `~/rio_sstv/` | Estado compartido entre transmisor y display |
| `banco_imagenes/` | `~/` | Carpeta con imágenes JPG/PNG |
| `start_display.sh` | `/tmp/` | Script arranque X11 + xrandr + pygame |
| `estado_fsk.json` | `~/rio_sstv/` | Progreso de frases transmitidas |

### display_estado.json

Canal de comunicación entre transmisor y display. El transmisor escribe, el display lee cada 120ms:

```json
{
  "estado": "transmitiendo",
  "frase_actual": "también yo no puedes ser, no solo va",
  "tipo_frase": "deriva",
  "indice": 12,
  "total_frases": 48,
  "porcentaje_enviado": 0.6
}
```

**Estados posibles:**
- `idle` → display muestra imágenes
- `preparando` → transmisor genera frase (1s), display muestra telemetría
- `transmitiendo` → audio FSK activo, display muestra telemetría

---

## 4. Configuración del display

### start_display.sh

```bash
#!/bin/bash
export DISPLAY=:0
xrandr --output HDMI-1 --rotate right   # portrait 600×1024
sleep 1
python3 /home/qzzk/rio_sstv/display_emisor.py
```

### Lanzar desde SSH (sin sesión gráfica activa)

```bash
# Permitir a cualquier usuario iniciar X (una sola vez)
echo 'allowed_users=anybody' | sudo tee /etc/X11/Xwrapper.config

# Instalar xrandr si no está
sudo apt install -y x11-xserver-utils

# Matar sesiones anteriores y relanzar
sudo pkill -f display_emisor; sudo pkill Xorg; sleep 2
chmod +x /tmp/start_display.sh
nohup xinit /tmp/start_display.sh -- :0 &>/tmp/display.log &
```

### config.txt — NO incluir estas líneas

Asegurarse de **no tener** en `/boot/firmware/config.txt` (interfieren con la detección HDMI):

```
display_rotate=1
hdmi_group=2
hdmi_mode=87
hdmi_cvt=480 800 60 6 0 0 0
```

### Forzar modo telemetría para pruebas

```bash
echo '{"estado":"transmitiendo","frase_actual":"prueba visual berenguer","tipo_frase":"literal","indice":1,"total_frases":48,"porcentaje_enviado":0.5}' > ~/rio_sstv/display_estado.json

# Volver a imágenes:
echo '{"estado":"idle"}' > ~/rio_sstv/display_estado.json
```

---

## 5. Hallazgos clave y fixes

| Problema | Causa | Solución |
|---|---|---|
| Pantalla con bordes azules / imagen cortada | `config.txt` tenía `display_rotate` y `hdmi_cvt` hardcodeados | Eliminar esas líneas; usar `set_mode((0,0), FULLSCREEN)` |
| Resolución incorrecta (1280×1024 en vez de 1024×600) | Modo HDMI forzado en config.txt | Eliminar `hdmi_group`, `hdmi_mode`, `hdmi_cvt` |
| Display horizontal en pantalla portrait | Pi 4 sale landscape por defecto | `xrandr --output HDMI-1 --rotate right` en start_display.sh |
| Telemetría como overlay encima de imágenes | Superficie SRCALPHA semi-transparente (diseño original incorrecto) | Cambio de modo: estado activo = `screen.fill(BG)` + texto directo, sin overlay |
| `estado` siempre `idle` durante transmisión | `_upd_display(estado='transmitiendo')` se llamaba **después** de `enviar_frase()` | Mover la llamada **antes** de `enviar_frase()` + `sleep(1)` de aviso |
| Transmisor no transmitía de noche | Ventana horaria `HORA_INICIO=9:00` / `HORA_FIN=20:00` activa | Cambiar a `dtime(0,0)` / `dtime(23,59)` para pruebas; restaurar en producción |
| `xinit` falla: "Resource temporarily unavailable" | Dos instancias de Xorg corriendo | `sudo pkill Xorg; sleep 3` antes de relanzar |
| pygame no encontrado en virtualenv | `entorno_arte` no tiene pygame; usa el sistema | Usar `/usr/bin/python3` o `pip install pygame --break-system-packages` |
| `xrandr` no instalado | Imagen mínima de Bookworm | `sudo apt install -y x11-xserver-utils` |
| Texto telemetría muy pequeño | Fuentes hardcodeadas para 480×800 | Fuentes escaladas: `H // 16` para frase principal |

### Flujo de imágenes correcto

```
negro (1–5s random) → imagen (5–15s random) → negro → imagen …
```

Corte directo, nunca crossfade, nunca dos imágenes consecutivas sin negro intermedio.

---

## 6. Estado actual (Pi 4 / desarrollo)

**Funcionando:**
- `display_emisor.py` corriendo vía xinit + xrandr portrait
- Imágenes del banco ciclando con cortes directos (5–15s / 1–5s negro)
- `transmisor_fsk_v2.py` transmitiendo cada ~80s
- Cambio automático imagen ↔ telemetría según `display_estado.json`
- Telemetría en negro completo con texto grande escalado

**Ventana horaria actual (pruebas):**
```python
HORA_INICIO = dtime(0, 0)
HORA_FIN    = dtime(23, 59)
```
⚠️ Restaurar antes del festival: `dtime(9, 0)` / `dtime(20, 0)`

---

## 7. Migración a Pi 3B

### Pi 3B Emisor (reemplaza al Pi 4)

```bash
# Desde el Pi 4, copiar archivos al Pi 3B emisor
rsync -av ~/banco_imagenes/ qzzk@<IP_PI3B_EMISOR>:~/banco_imagenes/
scp ~/rio_sstv/display_emisor.py qzzk@<IP_PI3B_EMISOR>:~/rio_sstv/
scp ~/rio_sstv/transmisor_fsk_v2.py qzzk@<IP_PI3B_EMISOR>:~/rio_sstv/
scp ~/rio_sstv/frases_berenguer.txt qzzk@<IP_PI3B_EMISOR>:~/rio_sstv/   # si existe
```

**Dependencias en Pi 3B emisor:**
```bash
sudo apt update
sudo apt install -y python3-pygame x11-xserver-utils python3-numpy
pip3 install sounddevice --break-system-packages
echo 'allowed_users=anybody' | sudo tee /etc/X11/Xwrapper.config
```

**Crear `/tmp/start_display.sh`** (idéntico al Pi 4).

### Pi 3B Receptor

```bash
rsync -av ~/banco_imagenes/ qzzk@<IP_PI3B_RECEPTOR>:~/banco_imagenes/
scp ~/rio_sstv/display_emisor.py qzzk@<IP_PI3B_RECEPTOR>:~/rio_sstv/
```

El receptor necesita además `receptor_fsk.py` (por implementar): captura audio por micrófono USB, detecta tonos 1200/2200 Hz, decodifica bits, reconstruye texto, escribe en `display_estado.json`.

### Autostart (ambos Pi 3B)

```bash
crontab -e
# Agregar:
@reboot sleep 10 && chmod +x /tmp/start_display.sh && nohup xinit /tmp/start_display.sh -- :0 &>/tmp/display.log &
@reboot sleep 15 && nohup python3 ~/rio_sstv/transmisor_fsk_v2.py &>/tmp/transmisor.log &  # Solo emisor
```

### Verificación post-migración

```bash
# En el Pi 3B emisor — verificar que todo corre
ps aux | grep -E "display|transmisor|Xorg" | grep -v grep
cat ~/rio_sstv/display_estado.json

# Forzar telemetría para test visual
echo '{"estado":"transmitiendo","frase_actual":"prueba","tipo_frase":"literal","indice":1,"total_frases":48,"porcentaje_enviado":0.5}' > ~/rio_sstv/display_estado.json
sleep 5
echo '{"estado":"idle"}' > ~/rio_sstv/display_estado.json
```

---

## 8. Comandos de referencia rápida

```bash
# Ver procesos corriendo
ps aux | grep python3 | grep -v grep

# Reiniciar display
sudo pkill -f display_emisor; sudo pkill Xorg; sleep 2
nohup xinit /tmp/start_display.sh -- :0 &>/tmp/display.log &

# Lanzar transmisor en fondo
nohup python3 ~/rio_sstv/transmisor_fsk_v2.py &>/tmp/transmisor.log &

# Ver log del display
tail -f /tmp/display.log

# Ver log del transmisor
tail -f /tmp/transmisor.log

# Ver estado actual
cat ~/rio_sstv/display_estado.json

# Cambiar ventana horaria temporalmente (para pruebas)
sed -i 's/HORA_INICIO = dtime(9, 0)/HORA_INICIO = dtime(0, 0)/' ~/rio_sstv/transmisor_fsk_v2.py
sed -i 's/HORA_FIN    = dtime(20, 0)/HORA_FIN    = dtime(23, 59)/' ~/rio_sstv/transmisor_fsk_v2.py

# Restaurar ventana horaria (producción)
sed -i 's/HORA_INICIO = dtime(0, 0)/HORA_INICIO = dtime(9, 0)/' ~/rio_sstv/transmisor_fsk_v2.py
sed -i 's/HORA_FIN    = dtime(23, 59)/HORA_FIN    = dtime(20, 0)/' ~/rio_sstv/transmisor_fsk_v2.py
```
