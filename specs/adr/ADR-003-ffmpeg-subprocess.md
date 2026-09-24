# ADR-003 — Invocar FFmpeg con subprocess en lugar de ffmpeg-python

Estado: Aceptado · Fecha: 2026-09-24

## Contexto
El prompt semilla sugiere `ffmpeg-python`. Necesitamos: sondear archivos (ffprobe, salida JSON), extraer audio de video y convertir stems a WAV/FLAC/MP3. Son pocas órdenes, fijas y bien conocidas.

## Opciones
1. **ffmpeg-python.** API fluida para construir grafos de filtros. Lleva años sin publicar versiones nuevas y no trae tipos, lo que choca con `mypy --strict`.
2. **subprocess + ffprobe JSON.** Sin dependencia extra, control total de argumentos y de errores, fácil de tipar y de simular en tests.

## Decisión
Opción 2. Todas las invocaciones viven en `media/ffmpeg.py`, construyen la lista de argumentos explícitamente (nunca `shell=True`) y convierten los errores de FFmpeg en `MediaReadError`, `NoAudioStreamError` o `ExportError` con el stderr relevante en modo `--verbose`.

## Consecuencias
- Una dependencia menos y tipado estricto sin excepciones.
- Si en el futuro (I7) se necesitan grafos de filtros complejos, se reevaluará.
- Esta decisión se aparta del prompt semilla; el usuario la aprobó explícitamente el 2026-09-24 (`subprocess` en lugar de `ffmpeg-python`).
