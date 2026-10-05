# Arquitectura

Estado: Aprobado v0.8 (2026-10-04)

## 1. Vista de componentes

```
┌────────────────────────────────────────────────────────────┐
│ CLI  (Typer + Rich)                     stemseparator.cli   │
│  separate · models · doctor                                  │
│  Traduce argumentos → SeparationRequest, errores → mensajes  │
└───────────────────────────┬────────────────────────────────┘
                            │ usa
┌───────────────────────────▼────────────────────────────────┐
│ Aplicación                          stemseparator.application│
│  SeparationService: orquesta el ciclo de vida del trabajo    │
└──────┬──────────────────────┬──────────────────────┬────────┘
       │ depende de puertos   │                      │
┌──────▼───────────┐  ┌───────▼──────────┐  ┌────────▼────────┐
│ Dominio          │  │ Puerto Engine    │  │ Puerto Media     │
│ models, errors,  │  │ SeparationEngine │  │ MediaProbe,      │
│ catalog, progress│  │ (Protocol)       │  │ AudioNormalizer, │
│ (sin dependencias│  └───────▲──────────┘  │ StemExporter,    │
│  externas salvo  │          │ implementa  │ SystemChecks     │
│  Pydantic)       │  ┌───────┴──────────┐  └────────▲────────┘
└──────────────────┘  │ AudioSeparator-  │           │ implementa
                      │ Engine           │  ┌────────┴────────┐
                      │ (audio-separator)│  │ FFmpeg adapter   │
                      └──────────────────┘  │ (subprocess),    │
                                            │ LocalSystemChecks│
                                            └─────────────────┘
```

### Regla de dependencias
- `domain` no importa nada del proyecto salvo `domain`.
- `application` importa `domain` y los protocolos; nunca adaptadores concretos.
- `engines` y `media` importan `domain`.
- `ProgressReporter` y `JobStage` viven en `domain/progress.py` para que el motor pueda notificar etapas sin importar `application`.
- `cli` es el único lugar que construye los adaptadores concretos y los inyecta en `SeparationService` (composición manual, sin framework de inyección).
- `audio-separator` solo se importa en `engines/audio_separator_engine.py`, de forma perezosa.

## 2. Estructura del repositorio

```
stemseparator/
├── .gitattributes         # finales de línea: * text=auto, *.py eol=lf
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── uv.lock
├── specs/
│   ├── constitution.md  product.md  domain.md  architecture.md
│   ├── interfaces.md    acceptance.md  tasks.md  i8-api.md
│   └── adr/
├── src/stemseparator/
│   ├── __init__.py            # __version__
│   ├── __main__.py            # python -m stemseparator
│   ├── cli.py
│   ├── application/
│   │   └── service.py         # SeparationService
│   ├── domain/
│   │   ├── models.py          # Pydantic models, enums
│   │   ├── errors.py          # jerarquía de excepciones
│   │   ├── catalog.py         # catálogo de modelos
│   │   └── progress.py        # ProgressReporter, JobStage
│   ├── engines/
│   │   ├── base.py            # SeparationEngine (Protocol)
│   │   └── audio_separator_engine.py
│   └── media/
│       ├── base.py            # MediaProbe, AudioNormalizer, StemExporter, SystemChecks (Protocols)
│       ├── ffmpeg.py          # implementación con subprocess
│       └── system.py          # LocalSystemChecks (binarios, espacio en disco), default_model_dir()
├── tests/
│   ├── conftest.py            # fixtures: audio/video sintético, FakeEngine
│   ├── unit/
│   └── integration/           # marcados ffmpeg / slow
├── scripts/                   # (I2) evaluate.py
└── web/                       # (I8) front de la interfaz web local; contrato en specs/i8-api.md
```

## 3. Flujo del comando `separate`

1. `cli` valida la sintaxis (Typer) y construye un `SeparationRequest`.
2. `SeparationService.run(request, reporter)`:
   1. **Validar** (VALIDATING, `SeparationService.validate()`; `run()` empieza llamándola): existencia y lectura de la entrada, extensión, `SystemChecks.require_ffmpeg()`, modelo en catálogo, stems ⊆ stems del modelo, conflictos de salida (si el directorio no existe, no hay conflictos). Al final de VALIDATING, y solo si todo lo anterior pasó, se crea el directorio de salida si no existe (RF-05) y se comprueba que es escribible sobre el directorio ya creado (`OutputNotWritableError`, RF-09). Así, un `--model` mal escrito no deja un directorio vacío. Después, todavía en VALIDATING:
      - `MediaProbe.probe(entrada)`. `has_audio == False` → `NoAudioStreamError` inmediato. Un stream de video con `disposition.attached_pic == 1` (carátula) no cuenta como video.
      - Espacio: estimación de `domain.md` §5 con la duración de entrada contra `SystemChecks.free_bytes()` del volumen temporal y del de salida.
      `validate()` devuelve el `MediaInfo` del sondeo.
   2. **Preparar** (PREPARING):
      1. `AudioNormalizer.normalize(entrada, work_dir/source.wav)`, sin excepción: toda entrada, sea audio o video, se normaliza a WAV.
      2. `MediaProbe.probe(work_dir/source.wav)` → duración de referencia para los stems.
   3. **Separar**: `SeparationEngine.separate(source.wav, model, work_dir/engine, model_dir, reporter)` → `dict[StemName, Path]`. El motor notifica LOADING_MODEL y SEPARATING.
   4. **Exportar** (EXPORTING): `output_gain` = min(1, `OUTPUT_PEAK_CEILING` / máx. de `StemExporter.peak()` sobre todos los stems del motor); para cada stem pedido, `StemExporter.export(..., gain=output_gain)` a `work_dir/export/<nombre>_<stem>.<ext>`; después `MediaProbe.probe()` de cada archivo para rellenar `StemFile`. Si la duración de un stem se sale de la tolerancia de `acceptance.md` §1 respecto a la de referencia, se lanza `ExportError` (código 5). Es una comprobación en tiempo de ejecución, no solo una aserción de los tests: un stem truncado nunca llega al usuario.
   5. **Publicar** (sigue en EXPORTING, como en `domain.md` §4): solo cuando todos los stems pedidos se han exportado y han pasado la comprobación de duración, mover los archivos exportados a `output_dir`, con copia de seguridad de los que se reemplazan (ver ADR-004). Si falla un traslado intermedio, se retiran del directorio de salida los stems ya trasladados en esta ejecución.
   6. **Limpiar**: `work_dir` es un `tempfile.TemporaryDirectory`; se borra siempre. Si hubo fallo, antes se restauran los archivos reemplazados. DONE no se notifica: el progreso termina con `stage_finished(EXPORTING)` y `run()` devuelve el `SeparationResult`.
3. `cli` muestra la tabla de resumen o, ante `StemSeparatorError`, el mensaje y la sugerencia, y sale con el código correspondiente.

**Excepción aceptada a P8**: si el trabajo falla después de VALIDATING, el directorio de salida creado en esa etapa puede quedar vacío. Un directorio vacío no es un archivo corrupto, y P8 se refiere a archivos.

## 4. Dependencias

| Paquete | Uso | Tipo |
|---------|-----|------|
| `audio-separator[cpu]==0.47.0` | Motor de separación | runtime |
| `torch`, `torchvision` (índice `https://download.pytorch.org/whl/cpu` vía `[tool.uv.sources]`) | Dependencias de `audio-separator`, fijadas a su build CPU | runtime |
| `librosa<1` | Dependencia de `audio-separator`, acotada | runtime |
| `typer` | CLI | runtime |
| `rich` | Salida por consola | runtime |
| `pydantic` (v2) | Modelos de datos | runtime |
| `platformdirs` | Directorio de caché de modelos | runtime |
| `pytest`, `pytest-cov` | Tests | dev |
| `mypy`, `ruff` | Calidad | dev |
| FFmpeg + FFprobe (binarios del sistema) | Sondeo, normalización, exportación | sistema |

Versiones fijadas en `uv.lock`. `requires-python = ">=3.11,<3.13"` (T-01: `audio-separator` 0.47.0 exige `>=3.10`; medido con 3.12).

- **Por qué `torch` desde el índice CPU**: el extra `[cpu]` de `audio-separator` no evita CUDA; solo elige `onnxruntime` en vez de `onnxruntime-gpu`. `torch` es una dependencia incondicional y PyPI sirve en Linux la build CUDA (20 paquetes `nvidia-*`/`cuda-*` más `triton`, 5,8 GB de entorno frente a 1,3 GB). Esta barrera de dependencias es la garantía de RNF-05.
- **Por qué `librosa<1`**: `audio-separator` 0.47.0 importa `audioread` sin declararlo, y `librosa` 1.0 dejó de depender de él; con `librosa` 1.x el motor no se puede importar.
- **`soundfile` y `numpy`**: no se declaran como dependencias propias. Solo los usa el adaptador `AudioSeparatorEngine` (margen de entrada, guardián de reescalado) y los aporta `audio-separator`, que los declara (`soundfile>=0.12`, `numpy>=2`). Si el adaptador cambiara de motor, pasarían a declararse.
- **Red de los tests `slow`**: los pesos se descargan de releases de GitHub (`github.com`, `raw.githubusercontent.com`, `objects.githubusercontent.com`, `release-assets.githubusercontent.com`) o de `huggingface.co`.
- **Red de instalación**: `uv lock`, `uv sync` y el primer `uv run` de una sesión con caché limpia necesitan `pypi.org`, `files.pythonhosted.org`, `download.pytorch.org` (índice CPU de PyTorch) y `download-r2.pytorch.org` (observado en T-02; probablemente sirve las descargas de ese índice, no confirmado). Los tests en sí no usan red (RNF-07).

## 5. Estrategia de pruebas

| Nivel | Qué cubre | Dependencias | Marca |
|-------|-----------|--------------|-------|
| Unitario | Dominio, catálogo, validaciones, servicio con `FakeEngine` y medios simulados, CLI con `CliRunner` | Ninguna externa | (por defecto) |
| Integración FFmpeg | Adaptador FFmpeg real: sondeo, normalización de audio y video, exportación a WAV/FLAC/MP3 | FFmpeg instalado | `ffmpeg` |
| Integración modelo | Flujo completo con `audio-separator` y un modelo real sobre un clip de 10 s | Red (primera vez), CPU | `slow` |

- `FakeEngine` copia el audio de entrada como cada stem pedido y notifica LOADING_MODEL/SEPARATING al reporter; sirve para verificar orquestación, nombres, etapas y limpieza sin modelos.
- `pyproject.toml` fija `addopts = "-m 'not ffmpeg and not slow'"`: `uv run pytest` solo ejecuta la suite unitaria. Las otras se lanzan explícitamente con `uv run pytest -m ffmpeg` y `uv run pytest -m slow` (el `-m` de la línea de órdenes prevalece sobre el de `addopts`).
- El audio y video de prueba se generan en fixtures con `ffmpeg -f lavfi` (tono + ruido, y `testsrc` para video). Los tests `ffmpeg` se saltan si FFmpeg no está instalado.
- La variable `STEMSEPARATOR_SAMPLE_AUDIO` permite apuntar el test `slow` a un clip real del usuario.
- `ruff` se limita a archivos Python (`include = ["*.py", "*.pyi", "**/pyproject.toml"]` en `pyproject.toml`). Desde la versión 0.16 incluye `*.md` por defecto, y `ruff format` reescribe los bloques de código de `specs/` (ocurrió en T-02). No se quita esa restricción.

## 6. Puntos de extensión previstos (no implementar en I1)
- **I3**: una secuencia de pasos de separación (`SeparationPlan`) donde la salida `instrumental` de un modelo alimenta a otro. El servicio de I1 se diseña con un único paso; se refactoriza cuando I3 se especifique.
- **I7**: un `VideoMuxer` en `media/`.
- **I6**: capa de configuración y caché de resultados por hash de entrada + modelo.

## 7. Decisiones registradas
- ADR-001 — Motor de separación: `audio-separator` detrás de un puerto propio.
- ADR-002 — Modelos de voz en CPU: `vocals` (MDX-Net) predeterminado y `vocals-max` (BS-Roformer), con los tiempos medidos en T-01.
- ADR-003 — FFmpeg vía `subprocess` en lugar de `ffmpeg-python`.
- ADR-004 — Escritura atómica de salidas.
- ADR-005 — Seguridad de la API local (I8): loopback, Origin/Host, token y cabecera propia.
- ADR-006 — Un subproceso por trabajo en el backend web (I8).

## Registro de cambios
- v0.1 — Borrador inicial.
- v0.2 (2026-09-24) — `progress.py` movido a `domain`; `AudioNormalizer` y `SystemChecks` en el puerto Media; flujo con normalización universal, regla `attached_pic`, espacio y `NoAudioStreamError` en PREPARING, doble sondeo, reporter pasado al motor y copias de seguridad de `--overwrite`; creación y escritura del directorio de salida en VALIDATING (con excepción a P8 para el directorio vacío); comprobación de duración de stems en tiempo de ejecución (`ExportError`); Publicar dentro de EXPORTING y DONE no notificado; `requires-python` fijado; `addopts` de pytest.
- 2026-09-24 — Aprobado v0.2.
- v0.3 (2026-09-25) — T-01: `audio-separator` 0.47.0 fijado; `torch`/`torchvision` desde el índice CPU de PyTorch; `librosa<1`; hosts de red de los tests `slow`; `soundfile`/`numpy` transitivos en el adaptador; `output_gain` en el flujo de exportación.
- 2026-09-25 — Aprobado v0.3.
- v0.4 (2026-09-25) — Revisión de `i8-api.md`: `validate()` en el flujo (sondeo y espacio pasan a VALIDATING); `web/` e `i8-api.md` en la estructura; ADR-005 y ADR-006. Aprobado por el usuario.
- v0.5 (2026-09-26) — Hosts de red para instalar dependencias (PyPI e índice CPU de PyTorch); hueco detectado en T-02. §5: `ruff` limitado a archivos Python para que no reescriba `specs/`. Aprobado por el usuario.
- v0.6 (2026-09-26) — §2: `.gitattributes` (`* text=auto`, `*.py text eol=lf`) en la estructura. Motivo: RNF-06 y CA-20 exigen trabajar en Windows, Linux y macOS, y sin normalizar los finales de línea un checkout o una edición en Windows mete CRLF en el repo; la primera muestra fue `.claude/settings.json`, guardado en CRLF y normalizado en `aad7020`. Aprobado por el usuario.
- v0.7 (2026-10-03) — T-08: §3, orden de VALIDATING alineado con `domain.md` §4 (entrada, existencia y legibilidad y luego extensión, antes que `require_ffmpeg()`). Las dos specs se contradecían; se eligió la entrada primero porque el error que se arregla escribiendo bien la ruta es más accionable que el que pide instalar algo, en línea con `domain.md` v0.6. Aprobado por el usuario.
- v0.8 (2026-10-04) — T-09: §3, paso 5 (Publicar): se publica solo cuando todos los stems pedidos se han exportado y han pasado la comprobación de duración, y si falla un traslado intermedio se retiran los ya trasladados, alineado con `domain.md` v0.7. No era una contradicción sino una omisión en el documento que se sigue paso a paso al implementar. Aprobado por el usuario.
