# Checklist de montaje — RadioAtlas FSK Santiago 2026

Todos los comandos se corren desde el Mac. Los que llevan `sudo` piden la contraseña de la Pi.

## Antes de viajar a Santiago
- [x] Tailscale en el receptor: instalado 31 Oct (receptor 100.72.213.62, emisor 100.91.50.45). SSH verificado a ambas desde radio100al.
- [ ] Desactivar el vencimiento de clave de ambos equipos en la consola de Tailscale (por defecto 180 días).
- [ ] Wifi de la galería: pedir nombre y clave y cargarla en las dos Pi antes de viajar. Red WPA2 sin portal de inicio de sesión (o router/4G propio).
- [ ] **CRÍTICO — corte de luz o desenchufar una Pi (es lo mismo):**
    - Hora: las Pi 3 no tienen reloj con pila; sin internet arrancan con la hora del apagado y el horario 9–20 del emisor queda corrido. Con NTP se corrige sola.
    - Tarjeta SD: un corte mientras escribe puede dañar archivos o el sistema. Apagar con `sudo poweroff` antes de desenchufar; llevar copia de la tarjeta de cada Pi.
    - Mitigación: internet estable; módulo RTC DS3231 en cada Pi; o enchufe con timer en lugar del horario por software; avisar al personal de sala.
- [ ] Texto nuevo: mismo `frases.txt` en emisor (`~/rio_sstv/frases.txt`) y receptor (`~/frases.txt`), reiniciar ambos servicios. Cada frase ≤ 80 bytes UTF-8 (tildes y ñ = 2). La captura corta a 17 s (~90 bytes de texto). Las actuales: 20–73 bytes.
- [ ] Prueba de reinicio completo de las dos Pi.
- [ ] Opcional: receptor con `hw:CARD=ZEDi8,DEV=0` en vez de `hw:2,0`.

## En la galería
- [ ] Micrófono del receptor cerca del parlante FSK, apuntado a él y de espaldas a los parlantes de las otras obras (si se puede, cardioide).
- [ ] La señal del micrófono del receptor no debe salir por ningún parlante (evitar acople).
- [ ] Perilla del ZEDi8: pico entre 0.2 y 0.5 con el volumen FSK definitivo (sin saturar).
- [ ] Con todas las obras sonando al volumen de exhibición, grabar 5 min de ambiente:
      `ssh -t qzzk@radioatlasreceptor.local 'sudo bash ~/analisis_fsk/grabar_ambiente.sh'`
- [ ] Analizar esa grabación: energía del ambiente en 1200/2200 Hz frente al FSK, y mezcla con paquetes reales en el banco (`analisis/banco_receptor.py`).
- [ ] Según el resultado: detección de preámbulo por alternancia de tonos y/o corrección de errores (esta última cambia el sonido del emisor: consultar antes).
- [ ] Dejar 1 hora funcionando y revisar el registro:
      `ssh qzzk@radioatlasreceptor.local 'cut -f2 ~/registro_recepciones.tsv | sort | uniq -c'`
- [ ] Emisor: restaurar `HORA_INICIO = dtime(9, 0)` y `HORA_FIN = dtime(20, 0)` (hora de Santiago) y reiniciar `transmisor.service`.
- [ ] Conectar la impresora térmica (USB 0483:5720) y comprobar con `lsusb`.
- [ ] Respaldar al Mac y a GitHub.

## Ya hecho (31 Oct 2026)
- [x] Receptor corregido (niveles, cola, deriva, seguimiento) — 19/19 en vivo.
- [x] Display del receptor: parpadeo corregido.
- [x] Pausa del emisor 180 s.
- [x] Recorte automático de `/tmp/receptor.log`.
- [x] Ambas Pi en hora de Santiago.
- [x] WAV de prueba borrados.

## Volver atrás
Cada cambio tiene su respaldo en la Pi y en `receptor/respaldos_20261031/` y `emisor/respaldos_20261031/`.
Ejemplo receptor: copiar el respaldo encima de `~/receptor_fsk_v2.py` y
`ssh -t qzzk@radioatlasreceptor.local 'sudo bash ~/analisis_fsk/reiniciar_receptor.sh'`.
