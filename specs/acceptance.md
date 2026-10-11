# Criterios de aceptación

Estado: Aprobado v0.4 (2026-10-11)

## 1. Tolerancias comunes
- **Duración**: |duración_stem − duración_audio_de_trabajo| ≤ 50 ms en WAV/FLAC y ≤ 100 ms en MP3 (el códec MP3 añade relleno). La única referencia es la duración del audio de trabajo, sondeado tras normalizar (`SeparationResult.work_duration_s`); nunca la del medio de entrada. El servicio también aplica esta tolerancia en tiempo de ejecución (RF-06).
- **Parámetros de salida**: 44 100 Hz, 2 canales; códec `pcm_s24le` (wav), `flac` (flac), `mp3` a 320 kbps (mp3). Verificado con ffprobe.
- **Reconstrucción**: SNR = 20·log10(RMS(audio de trabajo) / RMS(audio de trabajo − (vocals + instrumental) / `output_gain`)) ≥ 30 dB, sobre los stems exportados. Base medida en T-01: 150 dB (`vocals-max`) y 39–45 dB (`vocals`) sin reescalado por stem; 16–19 dB con el reescalado que se quiere impedir.
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
entonces el audio se normaliza sin intervención, se crean `out/clip_vocals.wav` y `out/clip_instrumental.wav` válidos (duración comparada con `work_duration_s`), y no queda ningún archivo temporal.

**CA-03. Video sin audio** (RF-02) — U, F
Dado un `mudo.mkv` sin pista de audio,
cuando ejecuto `separate`,
entonces el comando termina con código 3, el mensaje indica que el archivo no contiene audio y no se crean archivos. El fallo se detecta en el sondeo: no se llama a `normalize()` ni se comprueba el espacio.

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

**CA-11. Conflicto de salida** (RF-07, ADR-004) — U
Dado que `out/tema_vocals.wav` ya existe,
cuando ejecuto `separate tema.wav -o out` sin `--overwrite`,
entonces termina con código 3 antes de separar, lista el archivo en conflicto y el archivo existente no cambia;
y cuando repito con `--overwrite`, el archivo se reemplaza y el directorio de salida no contiene ninguna copia de seguridad (`.stemseparator-tmp-*`).

**CA-12. Fallo a mitad de proceso** (RF-06, RF-13, ADR-004) — U
Dado un motor que falla durante la separación, un exportador que falla en el segundo stem, o un stem exportado cuya duración se sale de la tolerancia,
cuando ejecuto `separate`,
entonces termina con código 5, el directorio de salida no contiene ningún archivo nuevo y el directorio temporal se ha eliminado;
y si la ejecución usaba `--overwrite` sobre archivos existentes, estos conservan su contenido original y no queda ninguna copia de seguridad.

**CA-13. Progreso y resumen** (RF-08) — U, M
Dada una ejecución correcta,
entonces el `ProgressReporter` recibe `stage_started`/`stage_finished` de VALIDATING, PREPARING, LOADING_MODEL, SEPARATING y EXPORTING, en ese orden. La última llamada es `stage_finished(EXPORTING)` y DONE nunca se notifica. La consola muestra una tabla final con stem, ruta, duración, tamaño, el tiempo total y `output_gain` (factor y dB).
Dada una ejecución que falla en una etapa, `stage_failed` se llama exactamente una vez, con esa etapa, y no hay llamadas posteriores.

**CA-14. Comando `models`** (RF-10) — U
Cuando ejecuto `stemseparator models`,
entonces se lista cada modelo del catálogo con id, descripción, stems, coste en CPU, licencia de los pesos, si está descargado, y se marca el predeterminado (`vocals`).
El coste en CPU se muestra como factor de tiempo real y tiempo estimado para una canción de 3:30 (para `cpu_rtf` = 2,4: "≈2,4×" y "unos 8 min"), junto con la indicación de que se midió en el equipo de referencia.
Un modelo con `weights_license` "Sin licencia declarada" (`vocals-max`) lo muestra tal cual.
Ningún valor se recorta: a 80 columnas, la salida no contiene "…" y contiene completas las dos licencias.

**CA-15. Comando `doctor`** (RF-11) — U, M
Cuando ejecuto `stemseparator doctor` con todas las dependencias presentes,
entonces muestra versiones de Python, StemSeparator, `audio-separator` y FFmpeg, hilos de CPU, ruta de la caché y dispositivo de inferencia (`cpu` o `mps`), y termina con código 0.
Cuando `audio-separator` no es importable, `doctor` lo indica y termina con código 4.

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
entonces los tres terminan sin errores, y `uv run pytest` (que excluye las marcas `ffmpeg` y `slow`) no usa red ni FFmpeg y tarda menos de 30 s.

**CA-19. Interrupción con Ctrl+C** (RF-13, ADR-004) — U
Dado un motor que recibe `KeyboardInterrupt` durante SEPARATING, en una ejecución con `--overwrite` sobre un archivo existente,
cuando ejecuto `separate`,
entonces termina con código 130 sin traceback, se llama `stage_failed(SEPARATING)` una vez, el directorio temporal se ha eliminado, el directorio de salida no contiene archivos nuevos y el archivo existente conserva su contenido original.
Dado un `SIGINT` recibido durante la publicación (entre dos traslados), la señal se difiere: la publicación termina, el código de salida es 0, se avisa explícitamente de que la interrupción llegó durante la publicación y el trabajo se completó y no queda ninguna copia `.bak`.
Dado un `SIGINT` recibido durante la restauración, la señal se difiere: la restauración se completa, todos los archivos originales conservan su contenido y el código de salida es 130.

**CA-20. Portabilidad** (RNF-06) — M
Antes de cerrar I1, se ejecutan CA-01, CA-02 y CA-10 (con `-m ffmpeg`) y la suite por defecto en Windows, Linux y macOS. Se anotan en `tasks.md` el SO, la versión de Python, la versión de FFmpeg y el resultado. Un SO no disponible se registra como "no verificado"; no se da por bueno.

**CA-21. Carátula en archivo de audio** (RF-02) — U, F
Dado un `.mp3` con carátula embebida (stream de video con `attached_pic`),
cuando ejecuto `separate`,
entonces se trata como audio (`MediaInfo.kind == AUDIO`) y la separación termina con código 0.

**CA-22. Directorio de salida** (RF-05, RF-09) — U
Dado `-o out` con `out` inexistente y una petición válida, `separate` crea `out` al final de VALIDATING;
dado `-o out` con `out` inexistente y `--model no-existe` (o un stem no disponible, o un conflicto), termina con código 3 y `out` no se ha creado;
dado un `out` existente pero no escribible, termina con código 3 antes de sondear la entrada e indica la ruta.

**CA-23. Solo CPU** (RNF-05) — U
Dado el `uv.lock` versionado,
cuando un test lo analiza (sin red),
entonces no contiene paquetes CUDA/GPU (`nvidia-*`, `cuda-*`, `triton`, `onnxruntime-gpu`, ni `torch`/`torchvision` resueltos desde un índice que no sea el CPU de PyTorch);
y dado un `audio-separator` simulado que informa del dispositivo `cpu` o `mps`, `AudioSeparatorEngine` separa con normalidad y registra el dispositivo en el log;
y dado uno que informa de `cuda`, `separate()` lanza `EngineError` (código 5) antes de cargar el modelo, con un mensaje que indica que la instalación contiene paquetes GPU.

**CA-24. Fallo de la restauración** (RF-13, ADR-004) — U
Dado un fallo durante la publicación con `--overwrite` y un `os.replace(.bak, destino)` simulado que falla,
cuando ejecuto `separate`,
entonces termina con código 6 (no 5 ni 130), sin traceback, y el mensaje indica para cada archivo afectado la ruta del `.bak` y el nombre al que hay que renombrarlo. El `.bak` sigue existiendo con el contenido original.

**CA-25. Reconstrucción por suma y ganancia común** (RF-06, RF-08) — U, S
(U) Dado un `FakeEngine` que devuelve dos stems con picos de 1,2 y 0,5,
cuando el servicio exporta,
entonces calcula `output_gain` = 0,999 / 1,2 con los picos de **todos** los stems del motor (también si `--stems vocals`), pasa esa misma ganancia a `export()` de cada stem y la registra en `SeparationResult.output_gain`;
y con picos ≤ 0,999, `output_gain` = 1,0.
(U) Dado un `audio-separator` simulado que escribe un stem con pico ≥ 0,9999,
cuando `AudioSeparatorEngine` separa,
entonces lanza `EngineError` (el motor reescaló el stem); con pico 0,9998 no la lanza. La entrada que recibe el motor es el audio de trabajo × 0,5, y los stems devueltos están multiplicados por 2.
(S) Dado un clip con música y voz (el de `STEMSEPARATOR_SAMPLE_AUDIO` o uno sintético) separado con cada modelo del catálogo y exportado en `wav`,
cuando se suman las muestras de los stems exportados `vocals` e `instrumental` y se divide la suma por `output_gain`,
entonces el resultado reconstruye el audio de trabajo con un SNR ≥ 30 dB (§1).

**CA-26. Sin telemetría** (RNF-12) — U, M
Dados un `onnxruntime` y un `audio-separator` simulados,
cuando `AudioSeparatorEngine` separa,
entonces `ORT_DISABLE_TELEMETRY` vale `1` en el entorno y `onnxruntime.disable_telemetry_events()` se ha llamado, ambas cosas antes de la primera importación de `audio_separator`.
(M) Una separación real ejecutada en un sandbox que solo permite los hosts de descarga de modelos no registra ningún intento de conexión a otros hosts.

## 3. Matriz de trazabilidad (I1)

| RF | CA |
|----|----|
| RF-01 | CA-01, CA-06 |
| RF-02 | CA-02, CA-03, CA-21 |
| RF-03 | CA-01, CA-07 |
| RF-04 | CA-08, CA-09 |
| RF-05 | CA-01, CA-22 |
| RF-06 | CA-01, CA-10, CA-12, CA-25 |
| RF-07 | CA-11 |
| RF-08 | CA-13, CA-25 |
| RF-09 | CA-04, CA-05, CA-17, CA-22 |
| RF-10 | CA-14 |
| RF-11 | CA-04, CA-15 |
| RF-12 | CA-16 |
| RF-13 | CA-02, CA-12, CA-19, CA-24 |

| RNF | Verificación | Tipo |
|-----|--------------|------|
| RNF-01 SDD estricto | DoD de `constitution.md` §3 en cada tarea | Proceso (no verificable por test) |
| RNF-02 Idiomas | Revisión de cada tarea antes de marcarla hecha | Proceso (no verificable por test) |
| RNF-03 Tipado | CA-18 | Test |
| RNF-04 Entorno `uv` | `uv.lock` versionado; revisión en la tarea de esqueleto | Revisión manual |
| RNF-05 Solo CPU | CA-23, CA-15 (dispositivo en `doctor`) | Test |
| RNF-06 Portabilidad | CA-20 | Revisión manual |
| RNF-07 Tests sin red | CA-18 | Test |
| RNF-08 Validación por metadatos | Revisión de los tests de cada tarea | Revisión manual |
| RNF-09 Errores y códigos | CA-03 a CA-08, CA-11, CA-12, CA-15, CA-17, CA-19, CA-22, CA-24 | Test |
| RNF-10 Licencias | Licencias en el ADR de cada modelo; visibles en CA-14 | ADR + revisión manual |
| RNF-11 Rendimiento medido | Tiempos registrados en ADR-002 (spike T-01) | ADR (no verificable por test) |
| RNF-12 Sin telemetría | CA-26 | Test + revisión manual |

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
- v0.2 (2026-09-24) — Duración contra `work_duration_s`; CA-02, CA-03, CA-11, CA-12, CA-13, CA-15 y CA-18 ajustados; nuevos CA-19 (Ctrl+C), CA-20 (portabilidad, manual), CA-21 (carátula), CA-22 (directorio de salida), CA-23 (solo CPU) y CA-24 (fallo de restauración); CA-19 cubre la sección crítica; matriz RNF completa con tipo de verificación.
- 2026-09-24 — Aprobado v0.2.
- v0.3 (2026-09-25) — T-01: CA-13 muestra `output_gain`; CA-14 muestra el factor de tiempo real y la licencia no declarada; CA-15 muestra el dispositivo; CA-23 reescrito (barrera de dependencias, `cpu`/`mps` aceptados, `cuda` → `EngineError`); nuevos CA-25 (reconstrucción por suma y `output_gain`, U + S) y CA-26 (sin telemetría, U + M); tolerancia de reconstrucción en §1; matriz con RF-06 → CA-25 y RNF-12.
- 2026-09-25 — Aprobado v0.3.
- v0.4 (2026-10-11) — T-13: CA-14 exige la descripción (`description`) y que ningún valor se recorte a 80 columnas (sin "…", con las dos licencias completas). Motivo: un campo visible sin CA es el hueco que señaló T-04, y la maqueta de T-13 mostró que Rich recortaba la licencia a 80 columnas. Aprobado por el usuario.
