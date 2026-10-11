# Producto: StemSeparator

Estado: Aprobado v0.5 (2026-10-11)

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

**RF-02. Detección de medio y normalización del audio.** El tipo de medio se determina con `ffprobe` (no solo por la extensión). Un stream de video marcado como carátula (`disposition.attached_pic`), habitual en `.mp3` y `.m4a`, no convierte el archivo en video. Toda entrada, sea audio o video y sin excepción, se normaliza antes del motor: su primera pista de audio se convierte a un WAV temporal (PCM en coma flotante de 32 bits, 44 100 Hz, estéreo), que es el único audio que recibe el motor. La conversión a 44 100 Hz la hace FFmpeg: el motor trabaja a 44 100 Hz y reamuestrearía por su cuenta cualquier otra frecuencia, así que se hace un único reamuestreo, bajo nuestro control. El formato de coma flotante evita cuantizar el audio antes de separarlo. Si el sondeo indica que el archivo no tiene ninguna pista de audio, se informa con un error claro antes de escribir nada.

**RF-03. Selección de modelo.** El usuario elige el modelo con `--model <id>`, donde `<id>` es un identificador del catálogo interno (ver `domain.md` §3). Si no se indica, se usa el modelo por defecto del catálogo. Un identificador desconocido produce un error que sugiere `stemseparator models`.

**RF-04. Selección de stems.** El usuario elige qué stems exportar con `--stems vocals,instrumental`. Por defecto se exportan todos los stems que produce el modelo. Pedir un stem que el modelo no produce es un error que indica qué stems ofrece ese modelo.

**RF-05. Nombres de salida.** Cada stem se guarda como `<nombre_original>_<stem>.<ext>` en el directorio indicado con `--output` (por defecto, el directorio actual). El directorio se crea si no existe, durante la validación. Si el trabajo falla después, puede quedar vacío (excepción aceptada a P8, ver `architecture.md` §3). `<nombre_original>` es el nombre del archivo de entrada sin extensión.

**RF-06. Formatos y parámetros de salida.** Formatos `wav` (PCM 24 bits), `flac` (24 bits) y `mp3` (320 kbps CBR), elegidos con `--format` (por defecto `wav`). Frecuencia de muestreo de salida 44 100 Hz y 2 canales. La duración de cada stem coincide con la del audio de trabajo dentro de la tolerancia de `acceptance.md` §1. Se comprueba en tiempo de ejecución; si un stem se sale de la tolerancia, la ejecución falla y no se publica ningún archivo. Los stems intermedios del motor se escriben en coma flotante de 32 bits y **sin normalización de nivel por stem**. Al exportar se aplica a todos los stems una **ganancia común** `output_gain` (0 < g ≤ 1), la mínima reducción necesaria para que ningún stem sature. Así, la suma de `vocals` e `instrumental` reconstruye el audio de trabajo multiplicado por `output_gain`, dentro de la tolerancia de `acceptance.md` §1, y dividiendo por `output_gain` se recupera el nivel original. Es lo que hace los stems útiles para remezclar (UC-03). Los 24 bits de `wav` y `flac` son reales: no hay una cuantización intermedia a 16 bits.

**RF-07. Sobrescritura.** Si algún archivo de salida ya existe, la ejecución falla antes de separar y lista los archivos en conflicto, salvo que se use `--overwrite`.

**RF-08. Progreso y resumen.** Durante la ejecución se muestran las etapas (validación, preparación, carga del modelo, separación, exportación) con indicador de actividad y tiempo transcurrido. Al terminar se muestra una tabla con cada stem, su ruta, duración y tamaño, el tiempo total y la ganancia de salida `output_gain` (como factor y en dB, p. ej. "0,82 (−1,7 dB)"), con una línea que explica que la suma de los stems es la mezcla multiplicada por esa ganancia. `--verbose` muestra además los logs del motor.

**RF-09. Validaciones previas.** Antes de iniciar la separación se comprueba: que la entrada existe y es legible; que FFmpeg y FFprobe están disponibles; que el directorio de salida es escribible; y que hay espacio libre suficiente según la estimación de `domain.md` §5. El espacio se comprueba después de sondear la entrada, porque necesita su duración, y antes de normalizarla.

**RF-10. Comando `models`.** `stemseparator models` lista el catálogo: identificador, descripción, stems que produce, coste en CPU, licencia de los pesos (o "sin licencia declarada") y si ya está descargado, y marca el predeterminado. El coste en CPU se muestra como el factor de tiempo real medido, traducido a tiempo para una canción de 3:30 (p. ej. "≈2,4× (unos 8 min para una canción de 3:30)"), e indica que se midió en el equipo de referencia de ADR-002.

**RF-11. Comando `doctor`.** `stemseparator doctor` informa: versión de Python, versión de StemSeparator y de `audio-separator`, disponibilidad y versión de FFmpeg/FFprobe, número de hilos de CPU, ruta de la caché de modelos y dispositivo de inferencia que elige el motor (`cpu` o `mps`). Termina con código 4 si falta alguna dependencia obligatoria (FFmpeg, FFprobe o `audio-separator`) y con 0 si están todas.

**RF-12. Caché persistente de modelos.** Los pesos descargados se guardan en un directorio persistente del usuario (por defecto, el directorio de caché del sistema operativo, vía `platformdirs`), configurable con la variable de entorno `STEMSEPARATOR_MODEL_DIR`. Un modelo ya descargado no se vuelve a descargar.

**RF-13. Limpieza.** Los archivos temporales (audio de trabajo, salidas intermedias del motor) se eliminan al terminar, tanto si la ejecución tiene éxito como si falla. Una interrupción con Ctrl+C se trata como fallo: se limpia, se restauran los archivos reemplazados y se sale con código 130. Si la restauración falla, se sale con código 6 y se indica cómo recuperar cada archivo (ADR-004).

## 5. Requisitos no funcionales

**RNF-01. SDD estricto.** Ver `constitution.md`.
**RNF-02. Idiomas.** Código en inglés; specs, docs y mensajes al usuario en español.
**RNF-03. Tipado.** `mypy --strict` sin errores en `src/`.
**RNF-04. Entorno.** Dependencias y entorno virtual gestionados con `uv`; `uv.lock` versionado.
**RNF-05. Solo CPU.** La garantía vive en las dependencias, no en la configuración del motor:
(i) el árbol de dependencias resuelto (`uv.lock`) no contiene paquetes CUDA ni GPU;
(ii) el producto funciona correctamente en máquinas sin GPU;
(iii) el adaptador del motor registra en el log el dispositivo que eligió el motor y lanza `EngineError` solo si es CUDA, porque eso indicaría que la barrera de dependencias falló. Se aceptan CPU y MPS (Apple Silicon). `doctor` muestra el dispositivo elegido.
**RNF-06. Portabilidad.** Funciona en Windows, Linux y macOS. Rutas siempre con `pathlib`; nada de rutas con separadores escritos a mano.
**RNF-07. Tests sin red.** La suite por defecto no usa red, no descarga modelos, no necesita FFmpeg y termina en menos de 30 s. Excluye dos marcas: `ffmpeg` (adaptador FFmpeg real) y `slow` (modelo real), según el `addopts` de `architecture.md` §5.
**RNF-08. Validación por metadatos.** Los tests verifican los audios generados por metadatos (existencia, decodificable, duración, frecuencia de muestreo, canales, códec), no por fidelidad acústica. La calidad acústica se evalúa con el protocolo aparte (`acceptance.md` §4).
**RNF-09. Errores accionables y códigos de salida.** Ver tabla de códigos en `interfaces.md` §4.
**RNF-10. Licencias.** Las dependencias deben tener licencias de código abierto. La licencia de los pesos de cada modelo se registra en el catálogo; si un modelo tiene uso restringido (p. ej., no comercial), se indica en `models`.
**RNF-11. Rendimiento medido.** No hay un objetivo de tiempo fijo en I1. El tiempo de separación por minuto de audio de cada modelo del catálogo se mide en la tarea de spike y se registra en el ADR-002.
**RNF-12. Sin telemetría.** El producto no envía datos a terceros. Las únicas conexiones de red son las descargas de pesos y metadatos de modelos (RF-12). Las dependencias que emiten telemetría por defecto se desactivan explícitamente antes de usarlas (en I1, `onnxruntime`: variable de entorno `ORT_DISABLE_TELEMETRY=1` antes de importarlo; T-01 comprobó que `disable_telemetry_events()` por sí sola no evita las conexiones durante la separación). Motivo: la visión (§1) promete funcionar sin servicios en la nube.

## 6. Fuera de alcance en I1
Más de 2 stems, procesamiento por lotes, archivo de configuración, exportación a video, GPU, interfaz web, entrenamiento o ajuste fino de modelos, descarga de audio desde URLs.

## 7. Limitaciones conocidas
- En CPU, los modelos de mayor calidad (Roformer) pueden tardar varias veces la duración de la canción. Se mide y se informa; no se oculta.
- La separación nunca es perfecta: pueden quedar restos de instrumentos en la voz ("bleed") o artefactos. La calidad depende de la mezcla original.
- El usuario es responsable de tener derechos sobre el material que procesa.
- Toda entrada se normaliza a WAV. Una entrada con pérdida (`.mp3`, `.m4a`) se decodifica y se vuelve a codificar al exportar; con `--format mp3` hay una segunda generación con pérdida.
- La normalización fija 2 canales: una entrada mono se exporta en estéreo y una multicanal se mezcla a estéreo.
- La normalización fija 44 100 Hz: una entrada de 48 kHz o 96 kHz se reamuestrea (el motor trabaja a 44 100 Hz en cualquier caso).
- Los pesos de `vocals-max` no declaran licencia. StemSeparator no los redistribuye (los descarga el usuario desde el repositorio de UVR), pero **para uso comercial el usuario debe comprobar por su cuenta si puede usarlos**. `models` muestra "sin licencia declarada".
- `vocals-max` (BS-Roformer) tarda unas 37 veces la duración del audio en el equipo de referencia; el predeterminado, `vocals`, unas 2,4 veces.
- Los stems exportados llevan una ganancia común `output_gain` ≤ 1, así que pueden sonar algo más bajos que la mezcla original. La ganancia se muestra al terminar.
- En Macs con Apple Silicon, el motor puede elegir MPS en lugar de CPU. Se acepta (RNF-05); `doctor` lo muestra.
- En macOS solo se admiten Macs con Apple Silicon y macOS 14 o posterior: `torch`, `torchvision` y `onnxruntime` solo publican wheels para macOS arm64 ≥ 14. Los Mac Intel y las versiones de macOS anteriores a la 14 quedan fuera (la instalación con `uv sync` falla).

## Registro de cambios
- v0.1 — Borrador inicial.
- v0.2 (2026-09-24) — RF-02: regla `attached_pic` y normalización universal a WAV; RF-05: creación del directorio en la validación y excepción a P8; RF-06: duración contra el audio de trabajo, comprobada en ejecución; RF-08: "preparación" en lugar de "extracción"; RF-09: momento de la comprobación de espacio; RF-11: código 4; RF-13: Ctrl+C y código 6; RNF-07: excluye `ffmpeg` y `slow`; §7: limitaciones de la normalización.
- 2026-09-24 — Aprobado v0.2.
- v0.3 (2026-09-25) — T-01: RF-06 con ganancia común `output_gain`; RF-08 la muestra; RF-10 muestra el factor de tiempo real medido en lugar del coste relativo; §7 con la licencia de `vocals-max`, los tiempos y la ganancia; RF-02 normaliza a PCM float 32 bits y 44 100 Hz; RF-06 sin normalización por stem, reconstrucción por suma y 24 bits reales; RF-11 muestra el dispositivo; RNF-05 reformulado (barrera de dependencias, CUDA → `EngineError`, CPU y MPS aceptados); nuevo RNF-12 (sin telemetría, vía `ORT_DISABLE_TELEMETRY`); §7 con 44 100 Hz y MPS.
- 2026-09-25 — Aprobado v0.3.
- v0.4 (2026-09-26) — §7: limitación de macOS (solo arm64 con macOS ≥ 14, por los wheels de `torch`, `torchvision` y `onnxruntime`); detectada en T-02. Aprobado por el usuario.
- v0.5 (2026-10-11) — T-13: RF-10 exige la descripción del modelo (`description`), que `models` ya iba a mostrar por la decisión de T-04 (2026-09-30) sin que ningún RF lo pidiera. Aprobado por el usuario.
