# Dominio

Estado: Borrador v0.1 — pendiente de aprobación

## 1. Glosario

| Término | Definición |
|---------|------------|
| **Medio de entrada** | Archivo que aporta el usuario (audio o video). |
| **Audio de trabajo** | WAV que recibe el motor: el propio archivo si es audio compatible, o el audio extraído de un video. |
| **Stem** | Pista resultante de la separación que contiene una sola fuente (p. ej., la voz). |
| **Modelo** | Red neuronal preentrenada que separa un audio en un conjunto fijo de stems. |
| **Catálogo** | Lista curada y versionada de modelos admitidos por StemSeparator, con un identificador estable para cada uno. |
| **Motor** | Componente que ejecuta un modelo (en I1, `audio-separator`). |
| **Etiqueta del motor** | Nombre que el motor da a cada salida (p. ej., `Vocals`, `Instrumental`). Se traduce a un `StemName` propio. |
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
- El identificador (`id`) es estable y legible (`vocals-hq`, `vocals-fast`). El nombre de archivo del motor (`engine_filename`) puede cambiar sin afectar al usuario.
- Cada entrada declara el mapeo de etiquetas del motor a `StemName`.
- Exactamente un modelo está marcado como predeterminado.
- Solo se usan modelos del catálogo. No se aceptan nombres de archivo arbitrarios del motor en I1.

### Candidatos para I1 (a confirmar en el spike T-01)

Los nombres exactos de archivo se verificarán con `audio-separator --list_models` en la versión fijada. La elección final y los tiempos medidos se registran en el ADR-002.

| id propuesto | Familia | Motivo | Coste CPU esperado |
|--------------|---------|--------|--------------------|
| `vocals-hq` | BS-Roformer o MelBand-Roformer de voz | Mejor calidad disponible en voz/instrumental. | Alto |
| `vocals-fast` | MDX-Net de voz (ONNX) | Buena calidad con mucho menor coste en CPU. | Medio |

## 4. Ciclo de vida de un trabajo de separación

```
VALIDATING → PREPARING → LOADING_MODEL → SEPARATING → EXPORTING → DONE
      │            │             │              │             │
      └────────────┴─────────────┴──────────────┴─────────────┴──→ FAILED
```

- **VALIDATING**: entrada, dependencias, catálogo, stems pedidos, conflictos de salida, espacio en disco.
- **PREPARING**: sondeo con ffprobe; extracción de audio si es video.
- **LOADING_MODEL**: descarga (si hace falta) y carga del modelo.
- **SEPARATING**: el motor escribe sus salidas en el directorio de trabajo temporal.
- **EXPORTING**: conversión de cada stem pedido al formato final, en el directorio temporal; después, traslado al directorio de salida.
- **DONE / FAILED**: limpieza del directorio temporal en ambos casos. En FAILED, el directorio de salida no contiene archivos nuevos.

Cada transición se notifica al `ProgressReporter` (ver `interfaces.md`).

## 5. Estimación de espacio en disco

Estimación conservadora de bytes necesarios:

```
duración_s × 44100 × 2 canales × 4 bytes × (n_stems_modelo + 1) × 2
```

El factor `+1` cubre el audio de trabajo; el `× 2` final cubre las salidas intermedias del motor y las copias exportadas. Se compara con el espacio libre del volumen temporal y del de salida. Si no alcanza, error antes de empezar.

## 6. Invariantes
- Un stem exportado siempre corresponde a un `StemName` del catálogo del modelo usado.
- Dos stems del mismo trabajo nunca comparten ruta de salida.
- La duración de cada stem exportado coincide con la del audio de trabajo dentro de la tolerancia definida.
