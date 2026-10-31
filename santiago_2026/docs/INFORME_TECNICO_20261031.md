# RadioAtlas FSK — Santiago 2026 — Informe técnico del 31 Oct 2026

Resultado: el receptor pasó de **0 de 7** paquetes con CRC OK a **19 de 19** en una hora de
funcionamiento en vivo (31 Oct, 14:06–15:06, hora de Santiago), cada texto idéntico al enviado.
Todo lo de este informe son mediciones hechas ese día; lo que no se midió se dice explícitamente.

Equipos: emisor `radioatlasemisor.local` (Pi, salida 3.5 mm bcm2835, parlante) y receptor
`radioatlasreceptor.local` (Pi, micrófono → Allen & Heath ZEDi8, card 2, S32_LE, 48 kHz, 2 canales).
Protocolo: FSK 50 baud, 1200 Hz = bit 1, 2200 Hz = bit 0; paquete =
preámbulo 2 s (100 símbolos alternando 1200/2200) + SYNC 0xABCD + LEN (2 B) + CRC-16/CCITT (2 B) + texto UTF-8.

---

## 1. Diagnóstico previo que se descartó

El informe anterior atribuía los fallos a "sidebands de ±25 Hz del ZEDi8" (2175/2225 Hz al 19 %).

**Medición:** FFT del WAV digital que genera el emisor (`fsk_sonoro.wav`, antes de cualquier parlante
o interfaz), script `analisis/fft_bandas.py`:

| Frecuencia | Amplitud relativa al tono |
| --- | --- |
| 1175 Hz | 15.2 % |
| 1225 Hz | 15.9 % |
| 2175 Hz | 11.8 % |
| 2225 Hz | 11.3 % |

Las bandas ya están en la señal digital. Las produce la modulación: el preámbulo cambia de tono
cada 20 ms, es decir, cada tono se prende y apaga a 25 Hz, lo que genera bandas laterales a ±25 Hz.
No es un artefacto del ZEDi8. Del mismo modo, la "alternancia de RMS entre bloques pares e impares"
que se había medido corresponde a la alternancia del preámbulo combinada con niveles distintos de
cada tono (sección 3).

---

## 2. Cómo se midió

1. **Referencia exacta de lo enviado.** Mientras el receptor grababa, en el emisor se copiaba cada
   `fsk_sonoro.wav` nuevo en cuanto se escribía, y se decodificaba digitalmente
   (`analisis/texto_de_wav.py`, CRC OK en todos). Así cada paquete grabado se compara bit a bit
   con lo que realmente sonó.
2. **Analizador de errores** `analisis/ber_paquetes.py`: el mismo detector que el receptor (Goertzel
   en el bin exacto, bloque de 960 muestras), prueba 48 desfases, busca el SYNC y cuenta bits
   erróneos, su tipo (1→0 o 0→1) y su posición. Validado sobre el WAV limpio: 0 errores, CRC OK.
3. **Tonos de prueba** (`analisis/tonos_prueba.py`, 7.5 s, tocado en la pausa de 20 s del emisor
   sin detener su servicio): 1200 Hz solo, 2200 Hz solo y cortes secos. Analizados con
   `analisis/analizar_tonos.py` y `analisis/frecuencia_exacta.py`, ambos validados sobre el WAV limpio.
4. **Banco de prueba del receptor** `analisis/banco_receptor.py`: corre el bucle de escucha real de
   `receptor_fsk_v2.py` (misma detección de preámbulo, captura y decodificación) leyendo una grabación
   en lugar de `arecord`. Con esto se comparó cada versión del receptor sobre las mismas grabaciones.

---

## 3. Mediciones

### 3.1 Saturación de entrada
Con la ganancia original del ZEDi8, pico = **1.0** (fondo de escala). Se bajó la perilla de micrófono:
pico **0.29–0.38**. Con la saturación corregida los errores siguieron (23–36 %), así que no era la causa
principal.

### 3.2 Nivel distinto de cada tono
Tonos puros grabados en la sala (energía Goertzel, bloques de 20 ms):

| Tramo | E1200 | E2200 |
| --- | --- | --- |
| Tono 1200 Hz solo | −31.2 dB | −82.5 dB |
| Tono 2200 Hz solo | −76.6 dB | −21.5 dB |

- 2200 Hz llega **~10 dB más fuerte** que 1200 Hz (en paquetes FSK reales la relación medida fue de ~14 dB).
- Con 1200 Hz solo no aparece 2200 Hz (−51 dB): la cadena no genera 2200 Hz por sí misma.

### 3.3 Cola tras cada tono
Energía de la misma frecuencia después de un corte seco, cada 5 ms (dB respecto del tono):

| Corte | 0 ms | 10 ms | 20 ms | 30 ms | 40 ms |
| --- | --- | --- | --- | --- | --- |
| 1200 Hz (sala) | −4.8 | −8.0 | −7.4 | −9.5 | −11.4 |
| 2200 Hz (sala) | −9.6 | −9.9 | −7.1 | −6.6 | −6.2 |
| WAV limpio (control) | 0.0 | −18.3 | −142 | −142 | −142 |

El sonido de cada tono sigue presente, solo 6–10 dB abajo, durante al menos 40 ms (dos bits).
Entre 100 y 400 ms después ya está ~31 dB abajo. No se separó si viene del parlante, de reflexiones
de la sala o del micrófono.

### 3.4 Errores de bit (con referencia exacta)
Paquete "esta ciudad yo no la podía habitar" (328 bits), detector original:
**77 bits erróneos (23 %); 76 de ellos eran bits de 1200 Hz leídos como 2200 Hz**; 43 justo después de
un cambio de tono. En los tres paquetes medidos así, entre 93 % y 99 % de los errores fueron 1→0.

Explicación con los datos de 3.2 y 3.3: tras un bit de 2200 Hz, su cola (~8 dB abajo del 2200)
queda **por encima** del 1200 Hz directo (~10 dB abajo del 2200). El detector `e_mark > e_space`
elige 2200 Hz.

### 3.5 Reloj del emisor
- Frecuencia exacta de los tonos grabados (pendiente de fase sobre 1.6 s): **1200.543 Hz y 2200.802 Hz**
  (+453 y +365 ppm). WAV limpio: 0 ppm. El reloj de reproducción del emisor corre más rápido que el
  de captura del receptor.
- Sincronía a lo largo de cada paquete (`analisis/deriva_con_ref.py`, por tramos de 50 bits, con
  referencia): **no es constante**.

| Paquete | Corrimiento a lo largo del paquete |
| --- | --- |
| "el manizaleño es nosotros para cambiar este sistema" (CRC OK) | 0 hasta el bit 400; luego −90 a −200 muestras |
| "Entonces a mí esto me enseña, esto es la vida" (fallaba) | 0 hasta el bit 250; luego −70, −170, −230, −320, −370 muestras |

−370 muestras = 7.7 ms, casi medio bit. Por eso los errores restantes se juntaban al final de los
paquetes largos (en el caso de "Entonces…", 20 de 25 errores en los últimos 1.5 s).

---

## 4. Por qué Manizales funcionaba

El método de decisión era el mismo: la versión anterior del receptor (`respaldos_20261031/receptor_fsk_v2.py.bak`,
44100 Hz, 1 canal, `sounddevice`) decidía cada bit con `e_mark > e_space`, sin emparejar niveles,
sin compensar la cola y con desfase fijo. Ese detector solo funciona si los dos tonos llegan con nivel
parecido, se cortan limpio y la deriva entre relojes es pequeña.

**No hay mediciones de Manizales.** Ninguno de los tres efectos medidos se puede afirmar ni descartar allí:
- el desequilibrio de nivel y la cola dependen del parlante, la sala y la posición del micrófono;
- la deriva es *relativa* entre el reloj del emisor y el de la interfaz de captura (en Manizales otra:
  un comentario del código nombra una Roland R-07);
- la saturación depende de la ganancia usada.

Lo medido es que el detector original no tiene margen para ninguno de ellos. El nuevo los corrige
sin depender de que el canal sea favorable.

Prueba pendiente (no imprescindible) para separar causas: conectar la salida del emisor por cable a
la entrada del ZEDi8 y repetir 3.2 y 3.3. Si desaparecen, vienen del parlante o la sala.

---

## 5. Cambios en el receptor (`receptor/receptor_fsk_v2.py`)

| Cambio | Detalle | Por qué (sección) |
| --- | --- | --- |
| Niveles emparejados | Mide la energía de cada tono en los bloques 2–50 de la captura (preámbulo), percentil 75, y divide cada energía por su nivel antes de decidir | 3.2 |
| Resta de cola | Antes de decidir, resta ALFA × energía del bit anterior (ALFA probados: 0.1, 0.2, 0) | 3.3 |
| Búsqueda de deriva | Duración de símbolo = 960/(1+ppm), ppm probados: 400, 500, 300, 600, 200, 0; 16 desfases | 3.5 |
| Seguimiento de sincronía | Si nada de lo anterior da CRC OK: recorre el paquete en tramos de 25 bits y en cada tramo reajusta el punto de muestreo ±120 muestras, eligiendo el de mayor contraste medio \|E1200−E2200\|/(E1200+E2200) | 3.5 |
| Tabla de energías única | Energías de cada ventana cada 20 muestras, calculadas una vez por captura; todos los intentos leen de ahí. Peor caso (ruido sin paquete) 85.6 s → 4.5 s; paquete difícil 10.1 s → 2.1 s | el receptor no escucha mientras decodifica |
| Validación de paquete | Descarta LEN = 0 o LEN ≥ 200 | falsos SYNC |
| Preámbulo mínimo | `PREAMBULO_MIN_BLOQUES` 15 → 25 | falsos inicios |
| Registro permanente | `~/registro_recepciones.tsv`: fecha ISO, CRC_OK / CRC_FALLA / SIN_PAQUETE, texto (~2 MB en 3 meses) | seguimiento de la exhibición |
| Audio de fallos | Cada captura sin CRC OK se guarda en `~/fallos_fsk/` (WAV 16 bit, máx. 30) | diagnóstico futuro |

El CRC elige la combinación correcta: se aceptan solo paquetes con CRC-16 válido y LEN < 200.

### Resultados en el banco (mismas grabaciones)

| Versión | Paquetes completos con CRC OK |
| --- | --- |
| Original | 0 de 7 |
| Niveles + cola + deriva | 5 de 7 |
| + seguimiento de sincronía (instalada) | **7 de 7** |

### Resultado en vivo (versión instalada)
31 Oct 14:06–15:06: 19 envíos, **19 CRC OK**, textos idénticos. 2 detecciones `SIN_PAQUETE` sin
transmisión del emisor: sonidos de la sala (uno con energía en ~1162 Hz que saturó, pico 1.0; otro en
~600 Hz), descartados en segundos, sin texto mostrado.

---

## 6. Otros cambios del día

| Equipo | Qué | Detalle |
| --- | --- | --- |
| Receptor | `display_receptor.py` | Parpadeo cada 1 s y nunca pasaba al ASCII: `read_log()` comparaba el número de líneas contra la lista *filtrada* (sin marcadores `[BERENGUER_START/END]`), así que en cada línea de telemetría releía las últimas líneas y volvía a disparar el último bloque Berenguer. Corregido contando líneas sin filtrar (`S.raw_count`). Verificado con simulación sobre copia del log real |
| Receptor | `/etc/cron.d/radioatlas-recortar-log` | `/tmp/receptor.log` (en RAM) crecía ~170 KB/h (~4 MB/día; ~370 MB en 3 meses, con `/tmp` de 453 MB y 905 MB de RAM), y el display lo relee completo. Cada 15 min, si pasa de 1 MB, deja las últimas 500 líneas sin marcadores |
| Receptor | Zona horaria | America/Bogota → America/Santiago |
| Receptor | Ganancia ZEDi8 | Perilla de micrófono bajada (pico 1.0 → ~0.3) |
| Emisor | `transmisor_fsk_v2.py` | `SILENCIO_ENTRE_ENVIOS_SEG` 20 → 180 |

No se cambió: el WAV del emisor sigue a 44100 Hz (los datos no lo justifican; la deriva es del reloj
del hardware), y `HORA_INICIO`/`HORA_FIN` siguen 0:00–23:59 (restaurar 9:00–20:00 antes de abrir).

---

## 7. Mensajes del log que no son errores de recepción
- `[IMPRESIÓN] ❌ Error: Device not found (... 1155, 22304 ...)`: impresora térmica no conectada
  (`lsusb` solo muestra el ZEDi8). 1155:22304 = 0483:5720, la impresora configurada.
- `The media.width.pixel field of the printer profile is not set...`: aviso de python-escpos, inofensivo.

---

## 8. Riesgos para los 3 meses
- **Sonido de otras obras** (paisajes de Valparaíso, aves, lluvia, retroalimentación). El receptor
  solo mira ~50 Hz alrededor de 1200 y 2200 Hz. Riesgo: sonido en esas bandas (cantos de aves,
  acoples) y falsos inicios. Sin medir en la galería real. Ver `CHECKLIST_MONTAJE.md`.
- **Deriva de reloj fuera de rango**: el seguimiento tolera hasta ±120 muestras cada 25 bits
  (~240 muestras/s); lo medido llega a ~80 muestras/s.
- **Disco del receptor**: 2.4 GB libres; imágenes recibidas ~28 KB c/u (~500 MB en 3 meses a 9–20 h).
