# Backlog de tareas — Incremento I1

Estado: Aprobado v0.1 (2026-09-24)

## Reglas
- Cada tarea: `T-xx — título`, descripción breve, RF/CA que cubre, criterio de terminado, dependencias.
- Tareas pequeñas: una sesión de trabajo cada una, verificables con tests.
- Estados: `[ ]` pendiente, `[~]` en curso, `[x]` hecha (con fecha).
- Un CA puede repartirse entre varias tareas (por ejemplo, su parte U en una y su parte F o S en otra). Se da por cubierto cuando todas sus partes están hechas.
- El criterio de terminado de toda tarea incluye la DoD de `constitution.md` §3; aquí solo se listan los criterios específicos.

## Tareas

### [ ] T-01 — Spike de `audio-separator` (sin código de producción)
Fijar versiones compatibles de `audio-separator` y Python; confirmar su API (parámetros de `Separator`, nombres de archivos de salida, etiquetas); listar los modelos candidatos de voz; medir en la CPU del usuario el tiempo por minuto de audio de cada candidato con un fragmento de 30 s. El script y los comandos usados se registran en un anexo de ADR-002; no se añade código al repositorio. El spike se ejecuta en un entorno desechable fuera del repo (un directorio temporal con `uv venv`, o `uv run --with "audio-separator[cpu]"`): no crea `pyproject.toml` ni `uv.lock` en el repo, que son trabajo de T-02.
Preguntas que debe responder, además:
- ¿Expone `audio-separator` la carga de pesos y la inferencia como llamadas separadas? Condiciona el corte LOADING_MODEL/SEPARATING (`interfaces.md` §3).
- ¿Reamuestrea internamente a 44,1 kHz? Si es así, proponer normalizar directamente a 44 100 Hz (RF-02, RF-06).
- ¿Qué parámetros garantizan el modo CPU? Los necesita CA-23. ¿Qué extra instala solo paquetes CPU?
- Licencia de los pesos de cada candidato (RNF-10).

- **Cubre**: RNF-05, RNF-10, RNF-11; prepara RF-03 y CA-23.
- **Terminado cuando**: ADR-002 tiene la tabla completa (`engine_filename`, tiempo CPU/min, licencia, notas) y el equipo de medición; se aplica el criterio de decisión de ~10×; y se proponen, si hacen falta, cambios de spec (`interfaces.md`, `domain.md`, `architecture.md` §4, `CLAUDE.md`) para aprobación; y `git status` está limpio salvo `specs/`.
- **Depende de**: —

### [ ] T-02 — Esqueleto del proyecto
`uv init`, `pyproject.toml` con las dependencias de `architecture.md` §4 y las versiones fijadas en T-01, `requires-python`, `uv.lock`. Estructura de paquetes de `architecture.md` §2 (módulos vacíos). Configuración de pytest (marcas `ffmpeg` y `slow`, `addopts = "-m 'not ffmpeg and not slow'"`), `mypy --strict` y `ruff`. `__version__` y `stemseparator --version`. `.gitignore` con `eval/`.
- **Cubre**: RNF-02, RNF-03, RNF-04, RNF-07; CA-18; CA-23 (parte `uv.lock`: sin paquetes CUDA/GPU).
- **Terminado cuando**: `uv run stemseparator --version` imprime la versión; los tres comandos de CA-18 pasan; el test de CA-23 sobre `uv.lock` pasa.
- **Depende de**: T-01.

### [ ] T-03 — Modelos de dominio, errores y progreso
`domain/models.py` (enums y modelos Pydantic de `interfaces.md` §2, incluido `work_duration_s`), `domain/errors.py` (jerarquía `StemSeparatorError(message, hint)` con el código de salida de cada excepción, incluido `OutputRestoreError` = 6) y `domain/progress.py` (`JobStage`, `ProgressReporter`). Constante de tolerancia de duración (`acceptance.md` §1).
- **Cubre**: RNF-09 (tabla de códigos); base de todos los CA.
- **Terminado cuando**: hay tests de que cada excepción da el código de la tabla de `interfaces.md` §4, de que los modelos son inmutables y de que `domain` no importa nada fuera de `domain`.
- **Depende de**: T-02.

### [ ] T-04 — Catálogo de modelos
`domain/catalog.py` con las entradas de ADR-002 (`vocals-hq`, `vocals-fast`), `list_models()` y `get_model()`.
- **Cubre**: RF-03, RF-10 (datos), RNF-10; CA-07 (parte de dominio).
- **Terminado cuando**: los tests verifican las invariantes de `domain.md` §3 (exactamente un modelo predeterminado, ids únicos, `engine_labels` cubre `stems`, licencia no vacía), `get_model(None)` devuelve el predeterminado y un id desconocido lanza `UnknownModelError`.
- **Depende de**: T-01, T-03.

### [ ] T-05 — Adaptador FFmpeg: sondeo
`media/base.py` (los cuatro Protocols) y `MediaProbe` en `media/ffmpeg.py`: ffprobe con salida JSON → `MediaInfo`, regla `attached_pic`, `has_audio`, `MediaReadError`. Fixtures `lavfi` en `conftest.py`.
- **Cubre**: RF-02; CA-03 (F), CA-21 (F).
- **Terminado cuando**: los tests U de análisis de JSON (salidas de ffprobe guardadas como texto: audio, video, video sin audio, mp3 con carátula, archivo ilegible) pasan, y los tests `ffmpeg` pasan sobre archivos sintéticos.
- **Depende de**: T-03.

### [ ] T-06 — Adaptador FFmpeg: normalización y exportación
`AudioNormalizer` (primera pista → WAV PCM, frecuencia de muestreo original o la que decida T-01, 2 canales) y `StemExporter` (wav `pcm_s24le`, flac 24 bits, mp3 320k CBR; 44 100 Hz, 2 canales). Errores → `NoAudioStreamError` / `ExportError` con stderr en `--verbose` (ADR-003).
- **Cubre**: RF-02, RF-06; CA-02 (F), CA-10 (F).
- **Terminado cuando**: los tests U verifican la lista de argumentos (sin `shell=True`) y los tests `ffmpeg` validan los archivos con ffprobe según `acceptance.md` §1 (entrada mono → estéreo incluida).
- **Depende de**: T-05.

### [ ] T-07 — Comprobaciones del sistema
`media/system.py`: `LocalSystemChecks.require_ffmpeg()` (con instrucciones de instalación por SO), `free_bytes()` y `default_model_dir()` (`STEMSEPARATOR_MODEL_DIR` o `platformdirs`).
- **Cubre**: RF-09, RF-12 (ruta); CA-04 (parte de adaptador).
- **Terminado cuando**: los tests U, con PATH y variables de entorno simulados, pasan.
- **Depende de**: T-03.

### [ ] T-08 — Servicio: VALIDATING y PREPARING
`SeparationService.run()` hasta el final de PREPARING, con dobles de prueba: orden de VALIDATING (directorio de salida creado al final), conflictos, primer sondeo, `NoAudioStreamError` inmediato, espacio (`domain.md` §5), normalización y segundo sondeo. Notificación y fallo de las etapas propias.
- **Cubre**: RF-01, RF-02, RF-03, RF-04, RF-05, RF-07, RF-09; CA-03 (U), CA-05, CA-06, CA-07, CA-08, CA-11 (sin `--overwrite`), CA-17, CA-21 (U), CA-22.
- **Terminado cuando**: los tests U de cada CA listado pasan, incluidos que `normalize()` no se llama sin audio y que `out` no se crea con un `--model` erróneo.
- **Depende de**: T-04, T-05, T-07.

### [ ] T-09 — Servicio: separación, exportación y progreso
Llamada al motor con `reporter`, exportación de los stems pedidos, tercer sondeo y comprobación de duración en tiempo de ejecución (`ExportError`), `SeparationResult` con `work_duration_s`. `FakeEngine` en `conftest.py`. Publicación simple, sin `--overwrite`; la atómica completa llega en T-10.
- **Cubre**: RF-04, RF-06, RF-08, RF-13; CA-01 (U), CA-02 (U), CA-09, CA-10 (U: extensión y formato pasados al exportador), CA-12 (sin `--overwrite`), CA-13 (U).
- **Terminado cuando**: los tests U verifican la secuencia exacta de llamadas al reporter (éxito y fallo en cada etapa, `stage_failed` una sola vez, DONE nunca notificado), la limpieza del temporal y el fallo por duración.
- **Depende de**: T-06, T-08.

### [ ] T-10 — Publicación atómica (ADR-004)
Copias `.bak` con token `<job>`, traslado entre volúmenes con `.stemseparator-tmp-*`, restauración con `os.replace` directo, `OutputRestoreError`, y sección crítica con `SIGINT` diferido. Vive en `application/` porque solo usa la biblioteca estándar; si se saca a un módulo propio, antes se actualiza `architecture.md` §2.
- **Cubre**: RF-07, RF-13; CA-11 (con `--overwrite`), CA-12 (con `--overwrite`), CA-19, CA-24.
- **Terminado cuando**: los tests U con fallos inyectados en cada traslado, `SIGINT` simulado (`signal.raise_signal`) durante la publicación y la restauración, y fallo de restauración pasan, y no queda ningún `.stemseparator-tmp-*` salvo en CA-24.
- **Depende de**: T-09.

### [ ] T-11 — Motor `AudioSeparatorEngine`
`engines/base.py` y `engines/audio_separator_engine.py`: importación perezosa, configuración CPU, `model_file_dir`, notificación de LOADING_MODEL/SEPARATING (o una sola etapa, según T-01), traducción de etiquetas con `engine_labels`, `EngineError`.
- **Cubre**: RF-12, RNF-05; CA-23 (parte del adaptador), CA-01 (S), CA-02 (S), CA-16 (S).
- **Terminado cuando**: los tests U con un módulo `audio-separator` simulado pasan (modo CPU, etiquetas, salida que falta → `EngineError`, etapas); los tests `slow` pasan sobre un clip de 10 s; importar `stemseparator.cli` no importa `audio_separator`.
- **Depende de**: T-01, T-09.

### [ ] T-12 — CLI `separate`
`cli.py` con Typer: opciones de `interfaces.md` §1, composición de adaptadores, reporter Rich (una etapa con indicador de actividad y tiempo), tabla de resumen, errores → mensaje + sugerencia + código, traceback solo con `--verbose`, Ctrl+C → 130. `__main__.py`.
- **Cubre**: RF-03, RF-04, RF-05, RF-06, RF-07, RF-08, RNF-09; CA-01, CA-04 (`separate`), CA-05, CA-06, CA-07, CA-13 (tabla), CA-19 y CA-24 (código de salida y mensaje).
- **Terminado cuando**: los tests con `CliRunner` y dobles de prueba verifican los códigos de salida y los mensajes en español de cada CA listado.
- **Depende de**: T-10, T-11.

### [ ] T-13 — CLI `models` y `doctor`
`models`: tabla del catálogo con el estado de descarga (archivo presente en el directorio de modelos) y el predeterminado marcado. `doctor`: versiones, hilos de CPU, ruta de la caché; código 4 si falta ffmpeg, ffprobe o `audio-separator`.
- **Cubre**: RF-10, RF-11; CA-04 (`doctor`), CA-14, CA-15 (U).
- **Terminado cuando**: los tests con `CliRunner` pasan, incluido `audio-separator` no importable.
- **Depende de**: T-12.

### [ ] T-14 — Cierre de I1
README (instalación de FFmpeg por SO, uso, limitaciones de `product.md` §7, prefijo `.stemseparator-tmp-*`), texto de `--help` de `--overwrite` según ADR-004, ejecución completa de `-m ffmpeg` y `-m slow`, comprobaciones manuales y protocolo de portabilidad.
- **Cubre**: RNF-06; CA-13 (M), CA-15 (M), CA-20.
- **Terminado cuando**: los resultados de CA-20 están anotados abajo por SO (o marcados "no verificado"), las suites `ffmpeg` y `slow` pasan, y la matriz de trazabilidad de `acceptance.md` §3 no tiene CA sin tarea hecha.
- **Depende de**: T-13.

## Cobertura de CA por tarea

| CA | Tareas |
|----|--------|
| CA-01 | T-09 (U), T-11 (S), T-12 |
| CA-02 | T-06 (F), T-09 (U), T-11 (S) |
| CA-03 | T-05 (F), T-08 (U) |
| CA-04 | T-07, T-12, T-13 |
| CA-05, CA-06 | T-08, T-12 |
| CA-07 | T-04, T-08, T-12 |
| CA-08 | T-08 |
| CA-09 | T-09 |
| CA-10 | T-06 (F), T-09 (U) |
| CA-11 | T-08, T-10 |
| CA-12 | T-09, T-10 |
| CA-13 | T-09 (U), T-12, T-14 (M) |
| CA-14 | T-13 |
| CA-15 | T-13 (U), T-14 (M) |
| CA-16 | T-11 (S) |
| CA-17 | T-08 |
| CA-18 | T-02 (y toda tarea, por la DoD) |
| CA-19 | T-10, T-12 |
| CA-20 | T-14 |
| CA-21 | T-05 (F), T-08 (U) |
| CA-22 | T-08 |
| CA-23 | T-02 (`uv.lock`), T-11 (adaptador) |
| CA-24 | T-10, T-12 |

## Registro de portabilidad (CA-20)
_Se completa en T-14._

## Registro de cambios
- v0.1 (2026-09-24) — Backlog de I1 generado tras aprobar las specs v0.2. T-01 en entorno desechable fuera del repo, con `git status` limpio salvo `specs/`.
- 2026-09-24 — Aprobado v0.1.
