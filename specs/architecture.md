# Arquitectura

Estado: Borrador v0.1 — pendiente de aprobación

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
│  ProgressReporter (protocolo)                                │
└──────┬──────────────────────┬──────────────────────┬────────┘
       │ depende de puertos   │                      │
┌──────▼───────────┐  ┌───────▼──────────┐  ┌────────▼────────┐
│ Dominio          │  │ Puerto Engine    │  │ Puerto Media     │
│ models, errors,  │  │ SeparationEngine │  │ MediaProbe,      │
│ catalog          │  │ (Protocol)       │  │ AudioExtractor,  │
│ (sin dependencias│  └───────▲──────────┘  │ StemExporter     │
│  externas salvo  │          │ implementa  └────────▲────────┘
│  Pydantic)       │  ┌───────┴──────────┐  ┌────────┴────────┐
└──────────────────┘  │ AudioSeparator-  │  │ FFmpeg adapter   │
                      │ Engine           │  │ (subprocess)     │
                      │ (audio-separator)│  │ ffmpeg / ffprobe │
                      └──────────────────┘  └─────────────────┘
```

### Regla de dependencias
- `domain` no importa nada del proyecto salvo `domain`.
- `application` importa `domain` y los protocolos; nunca adaptadores concretos.
- `engines` y `media` importan `domain`.
- `cli` es el único lugar que construye los adaptadores concretos y los inyecta en `SeparationService` (composición manual, sin framework de inyección).
- `audio-separator` solo se importa en `engines/audio_separator_engine.py`, de forma perezosa.

## 2. Estructura del repositorio

```
stemseparator/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── uv.lock
├── specs/
│   ├── constitution.md  product.md  domain.md  architecture.md
│   ├── interfaces.md    acceptance.md  tasks.md
│   └── adr/
├── src/stemseparator/
│   ├── __init__.py            # __version__
│   ├── __main__.py            # python -m stemseparator
│   ├── cli.py
│   ├── application/
│   │   ├── service.py         # SeparationService
│   │   └── progress.py        # ProgressReporter, JobStage
│   ├── domain/
│   │   ├── models.py          # Pydantic models, enums
│   │   ├── errors.py          # jerarquía de excepciones
│   │   └── catalog.py         # catálogo de modelos
│   ├── engines/
│   │   ├── base.py            # SeparationEngine (Protocol)
│   │   └── audio_separator_engine.py
│   └── media/
│       ├── base.py            # MediaProbe, AudioExtractor, StemExporter (Protocols)
│       ├── ffmpeg.py          # implementación con subprocess
│       └── system.py          # detección de binarios, espacio en disco, rutas de caché
├── tests/
│   ├── conftest.py            # fixtures: audio/video sintético, FakeEngine
│   ├── unit/
│   └── integration/           # marcados ffmpeg / slow
└── scripts/                   # (I2) evaluate.py
```

## 3. Flujo del comando `separate`

1. `cli` valida la sintaxis (Typer) y construye un `SeparationRequest`.
2. `SeparationService.run(request, reporter)`:
   1. **Validar**: `system.require_ffmpeg()`, existencia de la entrada, extensión, modelo en catálogo, stems ⊆ stems del modelo, conflictos de salida, espacio.
   2. **Preparar**: `MediaProbe.probe()`. Si es video → `AudioExtractor.extract()` a `work_dir/source.wav`. Si es audio → se usa directamente.
   3. **Separar**: `SeparationEngine.separate(audio, model, work_dir/engine)` → `dict[StemName, Path]`.
   4. **Exportar**: para cada stem pedido, `StemExporter.export()` a `work_dir/export/<nombre>_<stem>.<ext>`; después `MediaProbe.probe()` de cada archivo para rellenar `StemFile`.
   5. **Publicar**: mover los archivos exportados a `output_dir` (ver ADR-004).
   6. **Limpiar**: `work_dir` es un `tempfile.TemporaryDirectory`; se borra siempre.
3. `cli` muestra la tabla de resumen o, ante `StemSeparatorError`, el mensaje y la sugerencia, y sale con el código correspondiente.

## 4. Dependencias

| Paquete | Uso | Tipo |
|---------|-----|------|
| `audio-separator[cpu]` | Motor de separación | runtime |
| `typer` | CLI | runtime |
| `rich` | Salida por consola | runtime |
| `pydantic` (v2) | Modelos de datos | runtime |
| `platformdirs` | Directorio de caché de modelos | runtime |
| `pytest`, `pytest-cov` | Tests | dev |
| `mypy`, `ruff` | Calidad | dev |
| FFmpeg + FFprobe (binarios del sistema) | Sondeo, extracción, exportación | sistema |

Versiones fijadas en `uv.lock`. `requires-python = ">=3.11,<3.13"` provisional, a confirmar en el spike según la compatibilidad de `audio-separator` y `onnxruntime`.

## 5. Estrategia de pruebas

| Nivel | Qué cubre | Dependencias | Marca |
|-------|-----------|--------------|-------|
| Unitario | Dominio, catálogo, validaciones, servicio con `FakeEngine` y medios simulados, CLI con `CliRunner` | Ninguna externa | (por defecto) |
| Integración FFmpeg | Adaptador FFmpeg real: sondeo, extracción de video, exportación a WAV/FLAC/MP3 | FFmpeg instalado | `ffmpeg` |
| Integración modelo | Flujo completo con `audio-separator` y un modelo real sobre un clip de 10 s | Red (primera vez), CPU | `slow` |

- `FakeEngine` copia el audio de entrada como cada stem pedido; sirve para verificar orquestación, nombres y limpieza sin modelos.
- El audio y video de prueba se generan en fixtures con `ffmpeg -f lavfi` (tono + ruido, y `testsrc` para video). Los tests `ffmpeg` se saltan si FFmpeg no está instalado.
- La variable `STEMSEPARATOR_SAMPLE_AUDIO` permite apuntar el test `slow` a un clip real del usuario.

## 6. Puntos de extensión previstos (no implementar en I1)
- **I3**: una secuencia de pasos de separación (`SeparationPlan`) donde la salida `instrumental` de un modelo alimenta a otro. El servicio de I1 se diseña con un único paso; se refactoriza cuando I3 se especifique.
- **I7**: un `VideoMuxer` en `media/`.
- **I6**: capa de configuración y caché de resultados por hash de entrada + modelo.

## 7. Decisiones registradas
- ADR-001 — Motor de separación: `audio-separator` detrás de un puerto propio.
- ADR-002 — Modelo por defecto de voz en CPU (pendiente de benchmark).
- ADR-003 — FFmpeg vía `subprocess` en lugar de `ffmpeg-python`.
- ADR-004 — Escritura atómica de salidas.
