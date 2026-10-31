# Checklist de montaje — RadioAtlas FSK Santiago 2026

Todos los comandos se corren desde el Mac. Los que llevan `sudo` piden la contraseña de la Pi.

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
