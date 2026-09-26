# CLAUDE.md — Reglas permanentes del proyecto StemSeparator

Este archivo contiene las instrucciones operativas para Claude Code en este repositorio. Las reglas de fondo están en `specs/constitution.md`. Si hay conflicto, manda la constitución.

## Qué es este proyecto
StemSeparator es una CLI local en Python que separa un archivo de audio o video en pistas (stems). El motor de separación es la librería `audio-separator`, envuelta detrás de una interfaz propia. Se ejecuta **solo en CPU**. El desarrollo es **incremental por modelo**: primero voz/instrumental en máxima calidad, luego 4 stems, luego 6, luego separaciones especializadas (ver `specs/product.md`, sección Roadmap).

## Fuente de verdad
- `specs/` es la única fuente de verdad. No hay documentación duplicada en otro lugar.
- Orden de lectura al empezar una sesión: `CLAUDE.md` → `specs/constitution.md` → `specs/tasks.md` → la spec relevante para la tarea en curso.
- Identificadores: `RF-xx`, `RNF-xx` (product.md), `CA-xx` (acceptance.md), `T-xx` (tasks.md), `ADR-xxx` (specs/adr/).

## Flujo de trabajo obligatorio (SDD)
1. **Spec**: si la tarea no está cubierta por una spec aprobada, detente y propón el cambio de spec. No programes.
2. **Plan**: `specs/tasks.md` se genera solo después de que el usuario apruebe las specs. Cada tarea referencia los RF/CA que cubre.
3. **Implementación**: una tarea a la vez. Primero el test (TDD), luego el código mínimo para pasarlo.
4. **Verificación**: ejecuta `uv run pytest`, `uv run mypy src` y `uv run ruff check .` antes de declarar una tarea terminada.
5. **Revisión**: marca la tarea como hecha en `tasks.md`, entrega el reporte (plantilla abajo) y **pregunta antes de pasar a la siguiente**.

### Puntos de parada obligatorios
- Tras redactar o modificar cualquier spec.
- Tras completar cada tarea.
- Ante cualquier ambigüedad: propón 2-3 opciones con sus trade-offs y pide decisión.
- Antes de añadir una dependencia no listada en `specs/architecture.md`.

## Comandos
```bash
uv sync                       # instalar dependencias
uv run stemseparator --help   # ejecutar la CLI
uv run pytest                 # tests rápidos (addopts excluye ffmpeg y slow: sin red, sin modelos, sin FFmpeg)
uv run pytest -m ffmpeg       # tests de integración con FFmpeg real
uv run pytest -m slow         # tests de integración con modelo real (lentos, descargan modelos; necesitan la red permitida abajo)
uv run mypy src               # tipado estricto
uv run ruff check . && uv run ruff format --check .
```

## Reglas técnicas
- Código, identificadores, commits y mensajes de log técnicos en **inglés**. Specs, docs y mensajes de la CLI dirigidos al usuario en **español**.
- Python `>=3.11,<3.13` (ver `specs/architecture.md` §4; confirmado en T-01 con `audio-separator` 0.47.0), tipado estricto (`mypy --strict`), sin `Any` implícito.
- `audio-separator` solo se importa en `src/stemseparator/engines/audio_separator_engine.py`, y de forma perezosa (dentro de la función), para que la CLI arranque rápido y los tests unitarios no dependan de él.
- FFmpeg/FFprobe se invocan solo desde `src/stemseparator/media/`.
- `ProgressReporter` y `JobStage` viven en `src/stemseparator/domain/progress.py`, nunca en `application/`: `engines` los necesita y no puede importar `application` (regla de dependencias de `specs/architecture.md` §1).
- No instalar paquetes CUDA/GPU. El extra `[cpu]` de `audio-separator` **no basta**: solo elige `onnxruntime`, y `torch` desde PyPI en Linux es la build CUDA. `torch` y `torchvision` se instalan desde `https://download.pytorch.org/whl/cpu` vía `[tool.uv.sources]` (ADR-002, T-01). CA-23 lo verifica sobre `uv.lock`.
- `librosa<1`: `audio-separator` 0.47.0 importa `audioread` sin declararlo, y `librosa` 1.0 dejó de traerlo.
- Sin telemetría (RNF-12): el adaptador fija `ORT_DISABLE_TELEMETRY=1` en el entorno antes de importar `onnxruntime`/`audio_separator`, y además llama a `onnxruntime.disable_telemetry_events()`. La llamada sola no basta (T-01).
- Red permitida en el sandbox (`.claude/settings.json`): `github.com`, `raw.githubusercontent.com`, `objects.githubusercontent.com`, `release-assets.githubusercontent.com` y `huggingface.co`. Motivo: `audio-separator` descarga los pesos de los modelos desde releases de GitHub, y esas descargas redirigen a `release-assets.githubusercontent.com`. Sin ellos no funcionan ni T-01 ni los tests `slow` de T-11. Además, para instalar dependencias (`uv lock`, `uv sync` y el primer `uv run` de una sesión): `pypi.org`, `files.pythonhosted.org`, `download.pytorch.org` (índice CPU de PyTorch, ADR-002) y `download-r2.pytorch.org` (observado en T-02; probablemente sirve las descargas del índice de PyTorch, no confirmado). Hueco preexistente: T-01 ya los necesitó.
- Los tests unitarios **no** usan red, no descargan modelos y no dependen de FFmpeg real salvo los marcados `@pytest.mark.ffmpeg`. Los que usan modelos reales van marcados `@pytest.mark.slow`.
- Los audios de prueba se generan sintéticamente con FFmpeg (`lavfi`) en fixtures. No subir audio con copyright al repositorio.
- No crear un paquete llamado `io` (choca con la librería estándar). El paquete de medios se llama `media`.
- Nunca dejar archivos parciales en el directorio de salida: escribir en temporal y mover al final, con copia de seguridad `.stemseparator-tmp-*` al sobrescribir (ver `specs/adr/ADR-004-escritura-atomica.md`).

## Commits
Conventional Commits con referencias: `feat(cli): add separate command [T-05, RF-03, CA-01]`.

## Plantilla de reporte al terminar una tarea
```
Tarea: T-xx — <título>
Requisitos cubiertos: RF-.., CA-..
Qué se hizo: ...
Cómo se verificó: comandos ejecutados y resultado
Desviaciones respecto a la spec: ninguna / ...
Pendiente o riesgos: ...
¿Continúo con T-yy?
```
