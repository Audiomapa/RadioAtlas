# RadioAtlas FSK — respaldo Santiago 2026

Copia de los archivos de las dos Pi tal como quedaron instalados el 31 Oct 2026.

| Carpeta | Contenido |
| --- | --- |
| `santiago_2026/receptor/` | `radioatlasreceptor.local`: receptor FSK, display, método Berenguer, servicios systemd, regla cron; `respaldos_20261031/` = versiones anteriores |
| `santiago_2026/emisor/` | `radioatlasemisor.local` (`~/rio_sstv`): transmisor FSK, display, frases, estado, servicios systemd |
| `santiago_2026/banco_imagenes/` | Imágenes ASCII (iguales en emisor y receptor) |
| `santiago_2026/analisis/` | Scripts de medición y banco de prueba usados el 31 Oct; scripts de mantenimiento |
| `santiago_2026/docs/` | `INFORME_TECNICO_20261031.md` (mediciones, causas, cambios) y `CHECKLIST_MONTAJE.md` |
| `santiago_2026/registro/` | Copia del registro de recepciones del 31 Oct |

No incluye: entornos virtuales (`entorno_arte/`), imágenes recibidas (`sstv_recibido/`), WAV generados ni logs.
Dependencias del receptor en `receptor/requirements_receptor.txt`.
