# Criterios de aceptación

Estado: Borrador v0.1 — pendiente de aprobación

## 1. Tolerancias comunes
- **Duración**: |duración_stem − duración_entrada| ≤ 50 ms en WAV/FLAC y ≤ 100 ms en MP3 (el códec MP3 añade relleno).
- **Parámetros de salida**: 44 100 Hz, 2 canales; códec `pcm_s24le` (wav), `flac` (flac), `mp3` a 320 kbps (mp3). Verificado con ffprobe.
- **"Válido"** significa: el archivo existe, pesa más de 0 bytes, ffprobe lo decodifica y cumple las dos tolerancias anteriores.

Verificación: **U** = test unitario (sin dependencias), **F** = test marcado `ffmpeg`, **S** = test marcado `slow` (modelo real), **M** = comprobación manual documentada.

## 2. Incremento I1 — MVP voz/instrumental

**CA-01. Separación de un WAV** (RF-01, RF-03, RF-05, RF-06) — U, S
Dado un archivo `tema.wav` válido,
cuando ejecuto `stemseparator separate tema.wav -o out`,
entonces se crean `out/tema_vocals.wav` y `out/tema_instrumental.wav`, ambos válidos, y el código de salida es 0.

**CA-02. Separación desde video** (RF-02, RF-13) — U, F, S
Dado un archivo `clip.mp4` con pista de audio,
cuando ejecuto `stemseparator separate clip.mp4 -o out`,
entonces se extrae el audio sin intervención, se crean `out/clip_vocals.wav` y `out/clip_instrumental.wav` válidos (duración comparada con la del audio del video), y no queda ningún archivo temporal.

**CA-03. Video sin audio** (RF-02) — U, F
Dado un `mudo.mkv` sin pista de audio,
cuando ejecuto `separate`,
entonces el comando termina con código 3, el mensaje indica que el archivo no contiene audio y no se crean archivos.

**CA-04. FFmpeg ausente** (RF-09, RF-11) — U
Dado un sistema donde `ffmpeg` o `ffprobe` no están en el PATH,
cuando ejecuto `separate` o `doctor`,
entonces el comando termina con código 4 y el mensaje explica cómo instalar FFmpeg en Windows, macOS y Linux, sin mostrar traceback.

**CA-05. Entrada inexistente** (RF-09) — U
Dado un `INPUT` que no existe,
cuando ejecuto `separate`,
entonces termina con código 3, el mensaje muestra la ruta buscada y no se crean archivos.

**CA-06. Formato no admitido** (RF-01) — U
Dado `documento.txt`,
cuando ejecuto `separate`,
entonces termina con código 3 y el mensaje lista las extensiones admitidas.

**CA-07. Modelo desconocido** (RF-03) — U
Dado `--model no-existe`,
cuando ejecuto `separate`,
entonces termina con código 3, el mensaje sugiere `stemseparator models` y no se descarga nada.

**CA-08. Stem no disponible en el modelo** (RF-04) — U
Dado `--stems drums` con un modelo de voz/instrumental,
cuando ejecuto `separate`,
entonces termina con código 3 y el mensaje lista los stems que ofrece ese modelo.

**CA-09. Selección parcial de stems** (RF-04) — U
Dado `--stems vocals`,
cuando ejecuto `separate`,
entonces solo se crea `<nombre>_vocals.<ext>`.

**CA-10. Formatos de salida** (RF-06) — U, F
Dado `--format flac` o `--format mp3`,
cuando ejecuto `separate`,
entonces los archivos tienen la extensión y el códec correspondientes y son válidos.

**CA-11. Conflicto de salida** (RF-07) — U
Dado que `out/tema_vocals.wav` ya existe,
cuando ejecuto `separate tema.wav -o out` sin `--overwrite`,
entonces termina con código 3 antes de separar, lista el archivo en conflicto y el archivo existente no cambia;
y cuando repito con `--overwrite`, el archivo se reemplaza.

**CA-12. Fallo a mitad de proceso** (RF-13, ADR-004) — U
Dado un motor que falla durante la separación o un exportador que falla en el segundo stem,
cuando ejecuto `separate`,
entonces termina con código 5, el directorio de salida no contiene ningún archivo nuevo y el directorio temporal se ha eliminado.

**CA-13. Progreso y resumen** (RF-08) — U, M
Dada una ejecución correcta,
entonces se notifican las etapas en orden (`VALIDATING` … `DONE`) al `ProgressReporter`, y la consola muestra una tabla final con stem, ruta, duración, tamaño y el tiempo total.

**CA-14. Comando `models`** (RF-10) — U
Cuando ejecuto `stemseparator models`,
entonces se lista cada modelo del catálogo con id, stems, coste CPU, licencia de los pesos, si está descargado, y se marca el predeterminado.

**CA-15. Comando `doctor`** (RF-11) — U, M
Cuando ejecuto `stemseparator doctor` con todas las dependencias presentes,
entonces muestra versiones de Python, StemSeparator, `audio-separator` y FFmpeg, hilos de CPU y ruta de la caché, y termina con código 0.

**CA-16. Caché de modelos** (RF-12) — S
Dado un modelo ya descargado en `STEMSEPARATOR_MODEL_DIR`,
cuando ejecuto `separate` sin red,
entonces la separación funciona sin intentar descargarlo de nuevo.

**CA-17. Espacio en disco** (RF-09) — U
Dado que el espacio libre simulado es menor que la estimación de `domain.md` §5,
cuando ejecuto `separate`,
entonces termina con código 4 antes de separar e indica el espacio necesario y el disponible.

**CA-18. Calidad del código** (RNF-03, RNF-07) — automático
Dado el repositorio,
cuando ejecuto `uv run pytest`, `uv run mypy src` y `uv run ruff check .`,
entonces los tres terminan sin errores, y `pytest` sin marcas no usa red y tarda menos de 30 s.

## 3. Matriz de trazabilidad (I1)

| RF | CA |
|----|----|
| RF-01 | CA-01, CA-06 |
| RF-02 | CA-02, CA-03 |
| RF-03 | CA-01, CA-07 |
| RF-04 | CA-08, CA-09 |
| RF-05 | CA-01 |
| RF-06 | CA-01, CA-10 |
| RF-07 | CA-11 |
| RF-08 | CA-13 |
| RF-09 | CA-04, CA-05, CA-17 |
| RF-10 | CA-14 |
| RF-11 | CA-04, CA-15 |
| RF-12 | CA-16 |
| RF-13 | CA-02, CA-12 |

## 4. Protocolo de evaluación de calidad

Los tests garantizan que el sistema funciona; este protocolo mide **cómo suena**. Es obligatorio para elegir el modelo por defecto (ADR-002) y para aceptar cualquier modelo nuevo (P4 de la constitución). No forma parte de la suite de `pytest`.

**Material**: entre 3 y 5 fragmentos de 30 s aportados por el usuario, de estilos distintos (p. ej., pop con voz clara, rock con guitarras densas, acústico, voz con mucha reverb). Si se dispone de las pistas aisladas originales, se usan como referencia. El material se guarda fuera del repositorio (carpeta `eval/` en `.gitignore`).

**Métrica objetiva** (solo si hay referencia): SDR por stem, calculado por `scripts/evaluate.py` (I2).

**Checklist de escucha** (siempre), puntuación 1–5 por fragmento y stem:
1. Bleed: ¿se oyen instrumentos en la voz, o voz en el instrumental?
2. Artefactos: sonido "acuoso", metálico o entrecortado.
3. Integridad: ¿se pierden sibilantes, respiraciones, ataques o colas de reverb?
4. Coros: ¿los coros van a `vocals` de forma consistente?

**Registro por modelo**: puntuaciones, SDR si aplica, y tiempo en CPU por minuto de audio (con modelo y número de hilos del equipo de prueba). Se anota en el ADR correspondiente.

## Registro de cambios
- v0.1 — Borrador inicial.
