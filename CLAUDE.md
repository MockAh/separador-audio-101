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
uv run pytest                 # tests rápidos (sin red ni modelos)
uv run pytest -m slow         # tests de integración con modelo real (lentos, descargan modelos)
uv run mypy src               # tipado estricto
uv run ruff check . && uv run ruff format --check .
```

## Reglas técnicas
- Código, identificadores, commits y mensajes de log técnicos en **inglés**. Specs, docs y mensajes de la CLI dirigidos al usuario en **español**.
- Python 3.11+, tipado estricto (`mypy --strict`), sin `Any` implícito.
- `audio-separator` solo se importa en `src/stemseparator/engines/audio_separator_engine.py`, y de forma perezosa (dentro de la función), para que la CLI arranque rápido y los tests unitarios no dependan de él.
- FFmpeg/FFprobe se invocan solo desde `src/stemseparator/media/`.
- No instalar paquetes CUDA/GPU. Usar el extra CPU de `audio-separator`.
- Los tests unitarios **no** usan red, no descargan modelos y no dependen de FFmpeg real salvo los marcados `@pytest.mark.ffmpeg`. Los que usan modelos reales van marcados `@pytest.mark.slow`.
- Los audios de prueba se generan sintéticamente con FFmpeg (`lavfi`) en fixtures. No subir audio con copyright al repositorio.
- No crear un paquete llamado `io` (choca con la librería estándar). El paquete de medios se llama `media`.
- Nunca dejar archivos parciales en el directorio de salida: escribir en temporal y mover al final (ver ADR-004 en architecture.md).

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
