# Dominio

Estado: Aprobado v0.3 (2026-09-25)

## 1. Glosario

| Término | Definición |
|---------|------------|
| **Medio de entrada** | Archivo que aporta el usuario (audio o video). |
| **Audio de trabajo** | WAV PCM en coma flotante de 32 bits, 44 100 Hz y estéreo que recibe el motor. Se obtiene siempre normalizando la primera pista de audio del medio de entrada (sea audio o video); el archivo original nunca se pasa al motor. Tras normalizarlo se sondea de nuevo, y su duración es la única referencia para validar los stems. |
| **Duración de entrada** | Duración del medio de entrada según el primer sondeo. Solo se usa para estimar el espacio en disco (§5). En un video puede diferir de la del audio de trabajo. |
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

### Catálogo de I1 (ADR-002, aceptado)

| id | engine_filename | Familia | Coste en CPU medido (`cpu_rtf`) | Licencia de los pesos | Predeterminado |
|----|-----------------|---------|--------------------------------|------------------------|----------------|
| `vocals` | `UVR-MDX-NET-Voc_FT.onnx` | MDX-Net de voz (ONNX) | 2,4× | MIT (UVR), con atribución | sí |
| `vocals-max` | `model_bs_roformer_ep_317_sdr_12.9755.ckpt` | BS-Roformer de voz | 37,1× | Sin licencia declarada | no |

Etiquetas del motor en ambos: `Vocals` → `vocals`, `Instrumental` → `instrumental`. Equipo de referencia de `cpu_rtf` (`cpu_reference`): el de ADR-002 (Intel Core i7-8565U, 4 núcleos / 8 hilos, WSL2).

## 4. Ciclo de vida de un trabajo de separación

```
VALIDATING → PREPARING → LOADING_MODEL → SEPARATING → EXPORTING → DONE
      │            │             │              │             │
      └────────────┴─────────────┴──────────────┴─────────────┴──→ FAILED
```

- **VALIDATING**: entrada, dependencias, catálogo, stems pedidos, conflictos de salida; al final, y solo si todo pasó, creación del directorio de salida y comprobación de escritura.
- **PREPARING**, en este orden:
  1. Sondeo del medio de entrada con ffprobe. Si `has_audio` es falso, se lanza `NoAudioStreamError` de inmediato, sin comprobar espacio ni normalizar.
  2. Comprobación de espacio en disco (§5) con la duración de entrada, antes de escribir nada.
  3. Normalización de la entrada a WAV (audio de trabajo). `normalize()` conserva `NoAudioStreamError` como defensa, pero el camino normal de detección es el paso 1.
  4. Sondeo del audio de trabajo para obtener la duración de referencia (§6).
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

El factor `+1` cubre el audio de trabajo; el `× 2` final cubre las salidas intermedias del motor y las copias exportadas. `duración_s` es la duración de entrada (primer sondeo de PREPARING), no la del audio de trabajo, que aún no existe. Se compara con el espacio libre del volumen temporal y del de salida antes de normalizar la entrada. Si no alcanza, error antes de escribir nada.

## 6. Invariantes
- La suma de los stems `vocals` e `instrumental` de un mismo trabajo, dividida por `output_gain`, reconstruye el audio de trabajo dentro de la tolerancia de `acceptance.md` §1: ningún stem se normaliza por separado, y la única ganancia es común a todos.
- Un stem exportado siempre corresponde a un `StemName` del catálogo del modelo usado.
- Dos stems del mismo trabajo nunca comparten ruta de salida.
- La duración de cada stem exportado coincide con la del audio de trabajo (segundo sondeo de PREPARING) dentro de la tolerancia definida. Nunca se compara con la duración de entrada.

## Registro de cambios
- v0.2 (2026-09-24) — "Audio de trabajo" siempre normalizado; separadas la duración de entrada (solo para espacio) y la del audio de trabajo (única referencia de los stems); `NoAudioStreamError` detectado en el primer sondeo; espacio en disco movido de VALIDATING a PREPARING (tras el sondeo, antes de normalizar); LOADING_MODEL/SEPARATING notificadas por el motor; DONE y FAILED solo estados terminales, no notificados; restauración de archivos reemplazados en FAILED.
- 2026-09-24 — Aprobado v0.2.
- v0.3 (2026-09-25) — T-01: audio de trabajo en PCM float 32 bits a 44 100 Hz; etiquetas del motor usadas vía `custom_output_names`; correspondencia de LOADING_MODEL/SEPARATING con `load_model()`/`separate()`; invariante de reconstrucción con `output_gain`; catálogo definitivo `vocals` (predeterminado) y `vocals-max` con `cpu_rtf` medido.
- 2026-09-25 — Aprobado v0.3.
