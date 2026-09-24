# Producto: StemSeparator

Estado: Borrador v0.1 — pendiente de aprobación

## 1. Visión
Herramienta de línea de comandos, local y reproducible, que recibe un archivo de audio o video y produce pistas separadas por fuente (voz, instrumental y, en incrementos posteriores, batería, bajo, guitarra, piano y otras), exportadas como archivos independientes. Funciona sin servicios en la nube y en ordenadores sin GPU.

## 2. Usuarios y casos de uso

| ID | Usuario | Caso de uso |
|----|---------|-------------|
| UC-01 | Músico / cantante | Obtener la pista instrumental de una canción para practicar o hacer karaoke. |
| UC-02 | Músico / transcriptor | Aislar la voz para transcribir la letra o la melodía. |
| UC-03 | Productor / remixer | Obtener la voz limpia (acapella) para un remix. |
| UC-04 | Creador de contenido | Extraer la voz o la música de un video (.mp4/.mkv) sin tener que convertirlo antes a mano. |
| UC-05 (I3+) | Músico | Aislar batería, bajo u otros instrumentos para estudiarlos. |

## 3. Roadmap incremental

El desarrollo avanza **por modelo**, no por instrumento suelto: cada modelo de separación produce un conjunto fijo de stems. Cada incremento se especifica en detalle cuando llega su turno; aquí solo se fija su alcance.

| Incremento | Alcance | Estado |
|-----------|---------|--------|
| **I0** | Constitución, specs, CLAUDE.md, ADRs iniciales. | En curso |
| **I1 — MVP voz/instrumental** | CLI `separate`, `models`, `doctor`. Entrada audio o video. 2 stems: `vocals` e `instrumental`. Salida WAV/FLAC/MP3. Solo CPU. | Especificado |
| **I2 — Calidad de voz** | Script de evaluación (SDR + checklist de escucha), comparación de modelos candidatos, elección del modelo por defecto con datos. Opcional: ensemble de dos modelos si la mejora compensa el coste en CPU. | Por especificar |
| **I3 — 4 stems** | `vocals`, `drums`, `bass`, `other`. Estrategia en cascada: voz con el modelo de I1/I2 y el instrumental resultante con un modelo de 4 stems. | Por especificar |
| **I4 — 6 stems** | Añade `guitar` y `piano`. Calidad inferior esperada; se documentará como limitación. | Por especificar |
| **I5 — Separaciones especializadas** | Exploratorio: voz principal vs. coros, batería por piezas, percusión, vientos, eliminación de reverb. Un ADR por modelo adoptado. | Por especificar |
| **I6 — Lote y configuración** | Procesamiento de carpetas, caché de resultados, reanudación, archivo de configuración TOML. | Por especificar |
| **I7 — Exportación a video** | Generar `.mp4` por stem sustituyendo el audio original. | Por especificar |
| **I8 — Interfaz web local** | Opcional. FastAPI + frontend sencillo. | Por especificar |

El orden de I3–I8 puede cambiar a petición del usuario.

## 4. Requisitos funcionales — Incremento I1

**RF-01. Formatos de entrada.** El sistema acepta `.wav`, `.mp3`, `.flac`, `.m4a`, `.mp4` y `.mkv` (sin distinguir mayúsculas). Cualquier otra extensión se rechaza con un mensaje que lista las admitidas.

**RF-02. Detección y extracción de audio de video.** El tipo de medio se determina con `ffprobe` (no solo por la extensión). Si el archivo contiene video, se extrae la primera pista de audio a un WAV temporal (PCM, frecuencia de muestreo original, estéreo) y se procesa ese WAV. Si el archivo no tiene ninguna pista de audio, se informa con un error claro.

**RF-03. Selección de modelo.** El usuario elige el modelo con `--model <id>`, donde `<id>` es un identificador del catálogo interno (ver `domain.md` §3). Si no se indica, se usa el modelo por defecto del catálogo. Un identificador desconocido produce un error que sugiere `stemseparator models`.

**RF-04. Selección de stems.** El usuario elige qué stems exportar con `--stems vocals,instrumental`. Por defecto se exportan todos los stems que produce el modelo. Pedir un stem que el modelo no produce es un error que indica qué stems ofrece ese modelo.

**RF-05. Nombres de salida.** Cada stem se guarda como `<nombre_original>_<stem>.<ext>` en el directorio indicado con `--output` (por defecto, el directorio actual). El directorio se crea si no existe. `<nombre_original>` es el nombre del archivo de entrada sin extensión.

**RF-06. Formatos y parámetros de salida.** Formatos `wav` (PCM 24 bits), `flac` (24 bits) y `mp3` (320 kbps CBR), elegidos con `--format` (por defecto `wav`). Frecuencia de muestreo de salida 44 100 Hz y 2 canales. La duración de cada stem coincide con la de la entrada dentro de la tolerancia de `acceptance.md`.

**RF-07. Sobrescritura.** Si algún archivo de salida ya existe, la ejecución falla antes de separar y lista los archivos en conflicto, salvo que se use `--overwrite`.

**RF-08. Progreso y resumen.** Durante la ejecución se muestran las etapas (validación, extracción, carga del modelo, separación, exportación) con indicador de actividad y tiempo transcurrido. Al terminar se muestra una tabla con cada stem, su ruta, duración y tamaño, y el tiempo total. `--verbose` muestra además los logs del motor.

**RF-09. Validaciones previas.** Antes de iniciar la separación se comprueba: que la entrada existe y es legible; que FFmpeg y FFprobe están disponibles; que el directorio de salida es escribible; y que hay espacio libre suficiente según la estimación de `domain.md` §5.

**RF-10. Comando `models`.** `stemseparator models` lista el catálogo: identificador, stems que produce, coste relativo en CPU, licencia de los pesos y si ya está descargado.

**RF-11. Comando `doctor`.** `stemseparator doctor` informa: versión de Python, versión de StemSeparator y de `audio-separator`, disponibilidad y versión de FFmpeg/FFprobe, número de hilos de CPU y ruta de la caché de modelos. Termina con código distinto de 0 si falta alguna dependencia obligatoria.

**RF-12. Caché persistente de modelos.** Los pesos descargados se guardan en un directorio persistente del usuario (por defecto, el directorio de caché del sistema operativo, vía `platformdirs`), configurable con la variable de entorno `STEMSEPARATOR_MODEL_DIR`. Un modelo ya descargado no se vuelve a descargar.

**RF-13. Limpieza.** Los archivos temporales (audio extraído, salidas intermedias del motor) se eliminan al terminar, tanto si la ejecución tiene éxito como si falla.

## 5. Requisitos no funcionales

**RNF-01. SDD estricto.** Ver `constitution.md`.
**RNF-02. Idiomas.** Código en inglés; specs, docs y mensajes al usuario en español.
**RNF-03. Tipado.** `mypy --strict` sin errores en `src/`.
**RNF-04. Entorno.** Dependencias y entorno virtual gestionados con `uv`; `uv.lock` versionado.
**RNF-05. Solo CPU.** No se instalan dependencias CUDA. El motor se configura explícitamente para CPU.
**RNF-06. Portabilidad.** Funciona en Windows, Linux y macOS. Rutas siempre con `pathlib`; nada de rutas con separadores escritos a mano.
**RNF-07. Tests sin red.** La suite por defecto no usa red, no descarga modelos y termina en menos de 30 s. Los tests con modelo real se marcan `slow`.
**RNF-08. Validación por metadatos.** Los tests verifican los audios generados por metadatos (existencia, decodificable, duración, frecuencia de muestreo, canales, códec), no por fidelidad acústica. La calidad acústica se evalúa con el protocolo aparte (`acceptance.md` §4).
**RNF-09. Errores accionables y códigos de salida.** Ver tabla de códigos en `interfaces.md` §4.
**RNF-10. Licencias.** Las dependencias deben tener licencias de código abierto. La licencia de los pesos de cada modelo se registra en el catálogo; si un modelo tiene uso restringido (p. ej., no comercial), se indica en `models`.
**RNF-11. Rendimiento medido.** No hay un objetivo de tiempo fijo en I1. El tiempo de separación por minuto de audio de cada modelo del catálogo se mide en la tarea de spike y se registra en el ADR-002.

## 6. Fuera de alcance en I1
Más de 2 stems, procesamiento por lotes, archivo de configuración, exportación a video, GPU, interfaz web, entrenamiento o ajuste fino de modelos, descarga de audio desde URLs.

## 7. Limitaciones conocidas
- En CPU, los modelos de mayor calidad (Roformer) pueden tardar varias veces la duración de la canción. Se mide y se informa; no se oculta.
- La separación nunca es perfecta: pueden quedar restos de instrumentos en la voz ("bleed") o artefactos. La calidad depende de la mezcla original.
- El usuario es responsable de tener derechos sobre el material que procesa.

## Registro de cambios
- v0.1 — Borrador inicial.
