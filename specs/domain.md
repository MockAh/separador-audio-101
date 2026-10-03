# Dominio

Estado: Aprobado v0.6 (2026-10-03)

## 1. Glosario

| Término | Definición |
|---------|------------|
| **Medio de entrada** | Archivo que aporta el usuario (audio o video). |
| **Audio de trabajo** | WAV PCM en coma flotante de 32 bits, 44 100 Hz y estéreo que recibe el motor. Se obtiene siempre normalizando la primera pista de audio del medio de entrada (sea audio o video); el archivo original nunca se pasa al motor. Tras normalizarlo se sondea de nuevo, y su duración es la única referencia para validar los stems. |
| **Duración de entrada** | Duración del medio de entrada según el primer sondeo (VALIDATING). Solo se usa para estimar el espacio en disco (§5). En un video puede diferir de la del audio de trabajo. |
| **Stem** | Pista resultante de la separación que contiene una sola fuente (p. ej., la voz). |
| **Modelo** | Red neuronal preentrenada que separa un audio en un conjunto fijo de stems. |
| **Catálogo** | Lista curada y versionada de modelos admitidos por StemSeparator, con un identificador estable para cada uno. |
| **Motor** | Componente que ejecuta un modelo (en I1, `audio-separator`). |
| **Etiqueta del motor** | Nombre que el motor da a cada salida (p. ej., `Vocals`, `Instrumental`), definido en la configuración de cada modelo. El adaptador lo traduce a un `StemName` propio y fija con él el nombre de archivo de salida del motor (`custom_output_names`), sin interpretar nombres de archivo. |
| **Ganancia de salida** (`output_gain`) | Factor común 0 < g ≤ 1 que se aplica a todos los stems al exportar para que ninguno sature. La suma de los stems exportados es el audio de trabajo multiplicado por g. |
| **Trabajo de separación** | Una ejecución completa: validar, preparar, separar, exportar, limpiar. |
| **Bleed** | Restos de una fuente que se cuelan en el stem de otra. |
| **SDR** | Signal-to-Distortion Ratio, métrica objetiva de calidad de separación (dB, mayor es mejor). |

## 2. Stems

Nombres canónicos (`StemName`), en inglés y minúsculas porque forman parte de los nombres de archivo:

| StemName | Significado | Incremento |
|----------|-------------|------------|
| `vocals` | Todas las voces (principal y coros). | I1 |
| `instrumental` | Todo menos las voces. | I1 |
| `drums` | Batería y percusión. | I3 |
| `bass` | Bajo. | I3 |
| `other` | Resto de instrumentos no cubiertos por otros stems. | I3 |
| `guitar` | Guitarras. | I4 |
| `piano` | Piano y teclados. | I4 |

Los stems de I5 (coros, piezas de batería, vientos, etc.) se añadirán a esta tabla cuando se especifiquen.

## 3. Catálogo de modelos

El catálogo es un archivo de datos dentro del paquete (`src/stemseparator/domain/catalog.py`) con una entrada `ModelSpec` por modelo (campos en `interfaces.md`). Reglas:
- El identificador (`id`) es estable y legible (`vocals`, `vocals-max`). El nombre de archivo del motor (`engine_filename`) puede cambiar sin afectar al usuario.
- Cada entrada declara el mapeo de etiquetas del motor a `StemName`.
- Exactamente un modelo está marcado como predeterminado.
- Solo se usan modelos del catálogo. No se aceptan nombres de archivo arbitrarios del motor en I1.
- El orden de `list_models()` es estable: el modelo predeterminado primero y el resto por `cpu_rtf` creciente. Fija el orden de las filas de `models` (RF-10, CA-14).

### Catálogo de I1 (ADR-002, aceptado)

| id | engine_filename | Familia | Coste en CPU medido (`cpu_rtf`) | Licencia de los pesos | Predeterminado |
|----|-----------------|---------|--------------------------------|------------------------|----------------|
| `vocals` | `UVR-MDX-NET-Voc_FT.onnx` | MDX-Net de voz (ONNX) | 2,4× | `MIT (UVR), con atribución` | sí |
| `vocals-max` | `model_bs_roformer_ep_317_sdr_12.9755.ckpt` | BS-Roformer de voz | 37,1× | `Sin licencia declarada` | no |

Tamaño de descarga (`download_size_bytes`, ADR-002): `vocals` 66 762 490 bytes (66,8 MB); `vocals-max` 639 333 486 bytes (639,3 MB).

Etiquetas del motor en ambos: `Vocals` → `vocals`, `Instrumental` → `instrumental`.

Equipo de referencia de `cpu_rtf` (`cpu_reference`), valor literal idéntico en ambos: `Intel Core i7-8565U, 4 núcleos / 8 hilos, WSL2`. Es la forma corta; el registro completo del equipo de medición está en ADR-002, y `models` remite a él (RF-10).

Descripción (`description`), valores literales:
- `vocals`: `Separador de voz MDX-Net del proyecto UVR. Buena calidad con un coste en CPU moderado; opción práctica para uso habitual.`
- `vocals-max`: `Separador de voz BS-Roformer (viperx). El SDR de referencia más alto del catálogo, con un coste en CPU mucho mayor.`

## 4. Ciclo de vida de un trabajo de separación

```
VALIDATING → PREPARING → LOADING_MODEL → SEPARATING → EXPORTING → DONE
      │            │             │              │             │
      └────────────┴─────────────┴──────────────┴─────────────┴──→ FAILED
```

- **VALIDATING** (`SeparationService.validate()`), en este orden:
  1. Entrada, dependencias, catálogo, stems pedidos y conflictos de salida. La entrada se comprueba en este orden: primero existencia y legibilidad (no existe, no es un archivo normal, p. ej. un directorio, o no se puede leer → `InputNotFoundError`, con la ruta en el mensaje), y después la extensión (`UnsupportedFormatError`). Motivo: con una ruta mal escrita que además no tiene extensión admitida, "no existe" es más accionable que "extensión no admitida".
  2. Si todo pasó, creación del directorio de salida y comprobación de escritura.
  3. Sondeo del medio de entrada con ffprobe. Si `has_audio` es falso, se lanza `NoAudioStreamError` de inmediato, sin comprobar el espacio.
  4. Comprobación de espacio en disco (§5) con la duración de entrada.
  No escribe nada salvo el directorio de salida y cuesta milisegundos, así que el backend web la ejecuta antes de encolar (`i8-api.md`). `run()` la vuelve a ejecutar al empezar.
- **PREPARING**, en este orden:
  1. Normalización de la entrada a WAV (audio de trabajo). `normalize()` conserva `NoAudioStreamError` como defensa, pero el camino normal de detección es el sondeo de VALIDATING.
  2. Sondeo del audio de trabajo para obtener la duración de referencia (§6).
- **LOADING_MODEL**: descarga (si hace falta) y carga del modelo. La notifica el motor. En `audio-separator` corresponde a construir `Separator` y llamar a `load_model()`.
- **SEPARATING**: el motor escribe sus salidas en el directorio de trabajo temporal. La notifica el motor. En `audio-separator` corresponde a `separate()`.
- **EXPORTING**: cálculo de `output_gain` con el pico de todos los stems del motor; conversión de cada stem pedido al formato final, con esa ganancia, en el directorio temporal; después, traslado al directorio de salida.
- **DONE / FAILED**: limpieza del directorio temporal en ambos casos. En FAILED, el directorio de salida no contiene archivos nuevos y los archivos reemplazados con `--overwrite` se restauran (ADR-004).

Cada transición se notifica al `ProgressReporter` (ver `interfaces.md` §3). La etapa la notifica quien la ejecuta: el servicio notifica VALIDATING, PREPARING y EXPORTING; el motor, LOADING_MODEL y SEPARATING. Quien inició una etapa reporta también su fallo. DONE y FAILED son solo estados terminales: no se notifican como etapas. El progreso termina al cerrar EXPORTING (o al reportar un fallo); el resultado lo entrega `run()` como `SeparationResult`.

## 5. Estimación de espacio en disco

Estimación conservadora de bytes necesarios:

```
duración_s × 44100 × 2 canales × 4 bytes × (n_stems_modelo + 1) × 2
```

El factor `+1` cubre el audio de trabajo; el `× 2` final cubre las salidas intermedias del motor y las copias exportadas. `duración_s` es la duración de entrada (sondeo de VALIDATING), no la del audio de trabajo, que aún no existe. Se compara con el espacio libre del volumen temporal y del de salida al final de VALIDATING, antes de normalizar la entrada. El "volumen temporal" es el del directorio temporal del sistema, porque el directorio de trabajo todavía no existe en VALIDATING. Si no alcanza, error antes de escribir nada.

## 6. Invariantes
- La suma de los stems `vocals` e `instrumental` de un mismo trabajo, dividida por `output_gain`, reconstruye el audio de trabajo dentro de la tolerancia de `acceptance.md` §1: ningún stem se normaliza por separado, y la única ganancia es común a todos.
- Un stem exportado siempre corresponde a un `StemName` del catálogo del modelo usado.
- Dos stems del mismo trabajo nunca comparten ruta de salida.
- La duración de cada stem exportado coincide con la del audio de trabajo (sondeo del audio de trabajo en PREPARING) dentro de la tolerancia definida. Nunca se compara con la duración de entrada.

## Registro de cambios
- v0.2 (2026-09-24) — "Audio de trabajo" siempre normalizado; separadas la duración de entrada (solo para espacio) y la del audio de trabajo (única referencia de los stems); `NoAudioStreamError` detectado en el primer sondeo; espacio en disco movido de VALIDATING a PREPARING (tras el sondeo, antes de normalizar); LOADING_MODEL/SEPARATING notificadas por el motor; DONE y FAILED solo estados terminales, no notificados; restauración de archivos reemplazados en FAILED.
- 2026-09-24 — Aprobado v0.2.
- v0.3 (2026-09-25) — T-01: audio de trabajo en PCM float 32 bits a 44 100 Hz; etiquetas del motor usadas vía `custom_output_names`; correspondencia de LOADING_MODEL/SEPARATING con `load_model()`/`separate()`; invariante de reconstrucción con `output_gain`; catálogo definitivo `vocals` (predeterminado) y `vocals-max` con `cpu_rtf` medido.
- 2026-09-25 — Aprobado v0.3.
- v0.4 (2026-09-25) — Revisión de `i8-api.md`: el primer sondeo y la comprobación de espacio pasan de PREPARING a VALIDATING (`SeparationService.validate()`), para que el backend web rechace al instante; PREPARING queda en normalizar y sondear el audio de trabajo. Aprobado por el usuario.
- v0.5 (2026-09-30) — T-04: §3 licencias de los pesos como valores literales (`MIT (UVR), con atribución`, `Sin licencia declarada`); `cpu_reference` como valor literal (`Intel Core i7-8565U, 4 núcleos / 8 hilos, WSL2`), con el equipo completo en ADR-002; cadenas literales de `description`; regla de orden estable de `list_models()` (predeterminado primero, el resto por `cpu_rtf` creciente). Aprobado por el usuario.
- v0.6 (2026-10-03) — T-08: §4, VALIDATING paso 1, orden de la comprobación de la entrada (primero existencia y legibilidad → `InputNotFoundError` con la ruta, incluido un directorio o un archivo no legible; después la extensión → `UnsupportedFormatError`); §5, el volumen temporal es el del directorio temporal del sistema, porque el directorio de trabajo aún no existe en VALIDATING. Aprobado por el usuario.
