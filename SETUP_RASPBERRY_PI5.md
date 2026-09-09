# Puesta en marcha del transmisor en la Raspberry Pi 5

## 0. Antes de empezar

- Con la HiFiBerry DAC+ puesta en el header GPIO, no necesitas jack de audio
  integrado ni dongle USB — la salida de audio sale por los terminales/RCA
  de la propia HAT.
- Usa una **fuente de poder oficial de 27W USB-C** (o una de buena calidad
  que entregue 5V/5A). Con una fuente débil, la Pi 5 puede hacer *brownout*
  a mitad de una transmisión de varias horas y corromper la sesión — y ojo,
  con una HAT puesta encima muchas carcasas oficiales de la Pi 5 no calzan,
  revisa que la que uses tenga espacio para el header GPIO ocupado.
- Se recomienda buena disipación/ventilación — vas a tener la Pi generando
  mapas y codificando audio varias horas seguidas, todos los días, por 3-4
  días.

## 1. Sistema operativo

1. Con el **Raspberry Pi Imager**, graba **Raspberry Pi OS (64-bit)**, basado
   en Debian Bookworm (no uses la versión de 32 bits: contextily/rasterio
   funcionan mucho mejor en 64-bit).
2. En las opciones avanzadas del Imager (ícono de engranaje), configura de
   una vez: hostname, usuario/contraseña, WiFi, y **habilita SSH** — así no
   necesitas monitor/teclado para lo que sigue.
3. Primer arranque, conéctate por SSH y actualiza:
   ```bash
   sudo apt update && sudo apt full-upgrade -y
   sudo reboot
   ```

## 2. Audio — HiFiBerry DAC+ (hw 2.6)

Tu placa es la variante **"DAC+" estándar** (no la "Pro"), que usa el chip
PCM5102A y opera como esclavo I2S — esta es justo la que funciona sin
problemas conocidos en la Pi 5 (la variante "DAC+ Pro"/"DAC2 Pro" sí tiene
bugs reportados de I2S en Pi5 por el cambio de arquitectura del RP1; la
estándar no los tiene).

1. Con la Pi apagada, coloca la HAT en el header GPIO de 40 pines.
2. Edita el config de arranque (en Bookworm está en `/boot/firmware/`, no en
   `/boot/`):
   ```bash
   sudo nano /boot/firmware/config.txt
   ```
3. Busca la línea `dtoverlay=vc4-kms-v3d` y agrégale `,noaudio` para que el
   HDMI no compita por la tarjeta de sonido:
   ```
   dtoverlay=vc4-kms-v3d,noaudio
   ```
4. Si existe una línea `dtparam=audio=on`, coméntala o bórrala.
5. Agrega al final del archivo:
   ```
   dtoverlay=hifiberry-dac
   ```
6. Guarda, reinicia:
   ```bash
   sudo reboot
   ```
7. Verifica que Linux vea la tarjeta:
   ```bash
   aplay -l
   ```
   Deberías ver una línea como:
   ```
   card X: sndrpihifiberry [snd_rpi_hifiberry_dac], device 0: HifiBerry DAC HiFi pcm5102a-hifi-0
   ```
8. Prueba directo por ALSA (sin PipeWire de por medio, para aislar problemas):
   ```bash
   aplay -D hw:X /usr/share/sounds/alsa/Front_Center.wav   # usa el número de card visto arriba
   ```
9. Si eso suena, confirma que PipeWire también la tome como sink por
   defecto (que es lo que usa nuestro script vía `paplay`):
   ```bash
   pactl list sinks short
   pactl set-default-sink alsa_output.platform-soc_sound.stereo-fallback   # el nombre puede variar, usa el que veas arriba
   paplay /usr/share/sounds/alsa/Front_Center.wav
   ```

**Si `aplay -l` no la lista después de reiniciar:** lo más común es un
conflicto con otro overlay que también use el bus I2S/SPI/I2C (por ejemplo
un HAT de CAN bus o similar) — revisa que no tengas otros `dtoverlay=` que
choquen, y mira `dmesg | grep -i hifiberry` para ver el error específico.

*(Alternativa si en algún punto usas otra Pi sin HAT: un dongle de audio USB
también sirve y no requiere tocar `config.txt` — Linux lo reconoce solo.)*

No sigas al paso 3 hasta que este pitido suene bien — ahorra mucho tiempo
depurar el audio del sistema antes de meter Python en la ecuación.

## 3. Dependencias del proyecto

```bash
sudo apt install -y python3-pip python3-venv python3-dev libatlas-base-dev \
    libjpeg-dev libopenjp2-7 build-essential

cd ~/rio_sstv   # o donde hayas copiado los archivos
python3 -m venv entorno_arte
source entorno_arte/bin/activate

pip install --upgrade pip
pip install -r requirements_transmisor.txt
```

**Si `rasterio` (dependencia de `contextily`) falla al instalar:** Raspberry
Pi OS ya trae `pip` configurado para usar
[piwheels.org](https://www.piwheels.org/) (repositorio de wheels
precompilados para ARM), así que normalmente instala sin compilar nada. Si
aun así falla:
```bash
pip install rasterio --extra-index-url https://www.piwheels.org/simple
```
Si sigue sin funcionar, como alternativa se puede instalar `python3-rasterio`
del sistema con `apt` en vez de `pip` (requiere ajustar el venv con
`--system-site-packages`), pero normalmente no hace falta llegar a eso.

## 4. Prueba rápida (antes de dejarlo corriendo días)

Con el entorno activado:
```bash
python3 transmisor_rio.py --prueba
```

Esto descarga el trazado del río (o usa el caché si ya corriste el
transmisor antes), genera **un solo mapa**, lo codifica y lo reproduce —
todo de inmediato, sin importar la ventana horaria configurada, y **sin
tocar** el progreso guardado (`estado_transmision.json`). Es el modo pensado
justo para validar la instalación completa (mapa, audio, permisos) antes del
montaje real.

Si escuchas el chirrido SSTV con claridad por el parlante, ya está lista
para el recorrido completo:
```bash
python3 transmisor_rio.py
```
(sin `--prueba`, este sí respeta la ventana horaria y va avanzando punto a
punto guardando progreso).

## 5. Dejarlo corriendo solo (systemd)

Con el venv creado en `~/rio_sstv/entorno_arte`:

```ini
# /etc/systemd/system/transmisor-rio.service
[Unit]
Description=Transmisor SSTV recorrido del río
After=network-online.target sound.target
Wants=network-online.target

[Service]
ExecStart=/home/pi/rio_sstv/entorno_arte/bin/python3 /home/pi/rio_sstv/transmisor_rio.py
WorkingDirectory=/home/pi/rio_sstv
Restart=always
RestartSec=15
User=pi
Environment=XDG_RUNTIME_DIR=/run/user/1000

[Install]
WantedBy=multi-user.target
```

`XDG_RUNTIME_DIR` es importante: sin esa variable, `paplay` a veces no
encuentra el servidor de PipeWire/Pulse cuando corre como servicio systemd
en vez de una sesión interactiva.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now transmisor-rio.service

# ver qué está haciendo en vivo
journalctl -u transmisor-rio.service -f
```

Como el progreso se guarda en disco y respeta la ventana horaria, es seguro
dejarlo con `Restart=always`: un reinicio a media transmisión simplemente
retoma en el siguiente punto disponible dentro de la ventana del día.
