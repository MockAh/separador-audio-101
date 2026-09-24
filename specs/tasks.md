# Backlog de tareas

Estado: Pendiente — se genera cuando el usuario apruebe las specs de I0.

## Reglas para generarlo
- Cada tarea: `T-xx — título`, descripción breve, RF/CA que cubre, criterio de terminado, dependencias.
- Tareas pequeñas: una sesión de trabajo cada una, verificables con tests.
- Estados: `[ ]` pendiente, `[~]` en curso, `[x]` hecha (con fecha).

## Restricciones ya decididas
- **T-01 será un spike** (sin código de producción): fijar versión de `audio-separator` y de Python compatibles; confirmar su API (parámetros de `Separator`, nombres de archivos de salida y etiquetas); listar los modelos candidatos de voz; medir en la CPU del usuario el tiempo por minuto de audio de cada candidato con un fragmento de 30 s. Resultado: ADR-002 completado y, si hace falta, ajustes propuestos a `interfaces.md` y `domain.md`.
- La segunda tarea será el esqueleto del proyecto (`uv init`, `pyproject.toml`, estructura de carpetas, configuración de pytest/mypy/ruff, `--version`).
