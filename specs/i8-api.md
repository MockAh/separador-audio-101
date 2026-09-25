# API de la interfaz web local (I8)

Estado: Aprobado v0.2 (2026-09-25)
Depende de: `product.md`, `domain.md`, `interfaces.md`, `acceptance.md`, `architecture.md`, `ADR-002`, `ADR-004`, `ADR-005`, `ADR-006`

Este documento especifica el contrato entre el backend web y el front. Se adelanta de I8 **solo como spec**: no cambia el orden de implementación, que sigue siendo I1 primero. Su propósito es que el trabajo de front pueda avanzar en paralelo al backend.

Identificadores propios para no chocar con los de I1 (`constitution.md` §2): `RFW-xx` (requisitos funcionales), `RNFW-xx` (no funcionales) y `CAW-xx` (criterios de aceptación).

## 1. Alcance y supuestos

Decididos por el usuario el 2026-09-25:

- **Solo local.** El backend corre en la máquina del usuario y escucha solo en loopback. Un solo usuario. Coherente con la promesa de `product.md` §1: sin servicios en la nube. "Local" **no** significa "seguro": cualquier web abierta en el navegador puede alcanzar `127.0.0.1`. Por eso la API exige las protecciones de §11 (ADR-005). No hay cuentas de usuario.
- **Un trabajo a la vez, con cola.** Las separaciones se ejecutan en serie. El usuario puede encolar varias y la cola es visible. Razón: en CPU, dos separaciones simultáneas tardan más que las mismas dos en serie, y compiten por RAM.
- **Un subproceso por trabajo** (ADR-006). El servidor HTTP no ejecuta el motor: lo hace un subproceso que se lanza para cada trabajo y termina con él.
- **Mismo repositorio.** Backend en `src/`, front en `web/` (`architecture.md` §2). Este documento es la frontera entre ambos.

**Fuera de alcance:** usuarios múltiples, cuentas, acceso por red, procesamiento por lotes desde carpeta, exportación a video (I7), y cualquier stem más allá de los que I1 y sus incrementos hayan habilitado.

## 2. Por qué esta forma y no otra

El dato que determina todo el diseño viene de T-01 (ADR-002): con el modelo predeterminado `vocals`, una canción de 3:30 tarda **unos 8,5 minutos**; con `vocals-max`, **más de dos horas**.

Eso descarta el patrón "subir y esperar":

- Una petición HTTP que tarda 8 minutos se muere en cualquier proxy o balanceador intermedio, y deja al usuario sin nada si se cierra la pestaña.
- El usuario **va a cerrar la pestaña**. Ocho minutos es tiempo de sobra para irse a otra cosa.

Por tanto: **el trabajo se crea, se identifica y sobrevive al cliente.** El front consulta o se suscribe; no espera colgado de una petición.

## 3. Ciclo de vida de un trabajo

El backend expone más estados que el `JobStage` de `interfaces.md` §3, porque la cola y la cancelación no existen en la CLI.

```
queued → running → done
   │        │
   │        └──→ failed
   └──────┴──→ cancelled
```

- **`queued`**: aceptado y validado (§6), esperando su turno. Cancelable sin coste. Sobrevive a un reinicio del backend (RFW-02).
- **`running`**: en ejecución en su subproceso (ADR-006). Su detalle interno, en el campo `stage`, es una de las cinco etapas de `domain.md` §4, en este orden: `validating`, `preparing`, `loading_model`, `separating`, `exporting`. `run()` repite la validación al empezar (`interfaces.md` §3), así que un trabajo que esperó en cola se revalida.
- **`done`**: stems disponibles para descarga.
- **`failed`**: terminó con error. Lleva `error` (código y mensaje) y `failed_stage`.
- **`cancelled`**: cancelado por el usuario, en cola o en ejecución.

**Importante para quien implemente:** `queued` y `cancelled` son estados de la capa API. No son valores de `JobStage` y no deben añadirse a ese enum. `JobStage.DONE` y `JobStage.FAILED` tampoco aparecen en `stage`: `domain.md` §4 los define como estados terminales que no se notifican. El campo `stage` solo tiene valor cuando `status` es `running`.

**`failed_stage`**: en un trabajo `failed`, la etapa en la que falló. Sale de `ProgressReporter.stage_failed(stage, error)`, que se llama exactamente una vez por trabajo fallido (CA-13). Vale `null` en `interrupted_by_restart` si el trabajo no había notificado ninguna etapa.

## 4. Requisitos funcionales

**RFW-01. Crear un trabajo.** El usuario sube un archivo de audio o video y elige modelo, stems y formato. El backend guarda el archivo, ejecuta `SeparationService.validate()` (`interfaces.md` §3: comprobaciones previas, sondeo y espacio en disco, en milisegundos) y, si todo pasa, encola el trabajo y devuelve su identificador. Los errores de validación se devuelven **al instante, en la respuesta** (§6, tabla A), nunca tras la espera en cola. Responde sin esperar a que la separación termine.

**RFW-02. Persistencia del trabajo.** Un trabajo sobrevive a que el cliente cierre la pestaña. El usuario puede volver más tarde y recuperar su estado y sus resultados. Los trabajos sobreviven también a un reinicio del backend:
- los `queued` **se conservan con su orden y se reanudan**, porque no habían empezado y no hay nada que perder;
- los `running` pasan a `failed` con `code: "interrupted_by_restart"` y sus archivos parciales se borran;
- los `done`, `failed` y `cancelled` se conservan hasta su retención (RFW-11).

**RFW-03. Cola visible.** El usuario ve todos sus trabajos, su estado y su posición en la cola.

**RFW-04. Progreso en vivo.** Durante `running`, el front recibe cambios de etapa conforme ocurren, sin sondear activamente. El subproceso emite los eventos del `ProgressReporter` por un canal de líneas JSON y el servidor los reenvía por SSE (ADR-006).

**RFW-05. Tiempo estimado.** Al aceptar el trabajo, el backend devuelve una estimación calculada como `input.duration_s × cpu_rtf` del modelo elegido (`ModelSpec.cpu_rtf`, ADR-002). Es una estimación, no una promesa, y así se etiqueta. Base de la estimación:
- `cpu_rtf` se midió sobre un clip de 30 s e **incluye la carga del modelo** (unos 9 s con `vocals` y 4 s con `vocals-max`). En una canción larga esa carga fija pesa menos, así que la estimación **sobrestima algo**: para 3:30 con `vocals`, estima unos 510 s frente a unos 460 s de carga más inferencia.
- **No incluye** la descarga de pesos la primera vez (ver `is_downloaded` en §6) ni la preparación y la exportación (unos segundos).
- Se midió en el equipo de referencia (`cpu_reference`). En otro equipo puede variar.

**RFW-06. Cancelación.** El usuario puede cancelar un trabajo en cola o en ejecución.
- En cola: se retira sin haber llegado a ejecutarse.
- En ejecución: el servidor envía la señal de interrupción a su subproceso (ADR-006), que sigue la misma limpieza que Ctrl+C en la CLI (RF-13, CA-19, ADR-004). No quedan archivos parciales. Si el subproceso no termina en el plazo de gracia, el servidor lo mata y borra el directorio del trabajo.

**RFW-07. Descarga de stems.** Cada stem de un trabajo `done` se descarga individualmente. También se ofrece la descarga conjunta en un ZIP.

**RFW-08. Reproducción.** Los stems se pueden reproducir en el navegador antes de descargarlos, con peticiones de rango (`Range`) para no cargar el archivo entero.

**RFW-09. Catálogo de modelos.** El front obtiene la lista de modelos con los mismos datos que muestra `stemseparator models` (RF-10): identificador, stems, factor de tiempo real, equipo de referencia, licencia de los pesos, si está descargado y el tamaño de la descarga (`ModelSpec.download_size_bytes`).

**RFW-10. Estado del entorno.** Equivalente a `stemseparator doctor` (RF-11): versión de Python, de StemSeparator y de `audio-separator`, disponibilidad y versión de FFmpeg y FFprobe, hilos de CPU, directorio de modelos y dispositivo de inferencia, más el espacio libre y la longitud de la cola. Permite al front avisar antes de aceptar un archivo si falta una dependencia o si no hay espacio.

**RFW-11. Retención y borrado.** Los archivos de un trabajo se conservan hasta que el usuario lo borra, o hasta que pasa el plazo de retención configurado (por defecto, 24 horas). El usuario puede borrar un trabajo y sus archivos explícitamente.

**RFW-12. Ganancia aplicada.** El resultado incluye `output_gain` (RF-06 de `product.md`) y el front **lo muestra siempre**, igual que la CLI (RF-08). Sin ese número, la suma de los stems no reconstruye el **audio de trabajo**: el audio normalizado a 44 100 Hz, estéreo y float que recibió el motor. No es la mezcla original: con un MP3, un audio a 48 kHz o un archivo mono, no son lo mismo.

## 4 bis. Requisitos no funcionales

**RNFW-01. Seguridad de la API local.** La API aplica las cinco protecciones de §11 (ADR-005): escucha solo en loopback, lista blanca de `Host` y `Origin`, token aleatorio por arranque exigido en cada petición y cabecera propia obligatoria en las peticiones que cambian estado.

**RNFW-02. Sin recursos de terceros.** El front no carga fuentes, CDNs, analítica ni ningún otro recurso de un origen distinto del propio backend. Todo se sirve desde `127.0.0.1`. Una fuente de Google, por ejemplo, filtra la IP y el uso en cada carga, que es justo lo que prohíbe RNF-12. El backend lo refuerza enviando `Content-Security-Policy: default-src 'self'` en las páginas del front.

## 5. Endpoints

Base: `http://127.0.0.1:<puerto>/api`. Todas las respuestas son `application/json` salvo descargas y el flujo de eventos. Todas las peticiones a `/api` llevan el token de §11.

| Método | Ruta | Qué hace |
|---|---|---|
| `POST` | `/jobs` | Valida y crea un trabajo. `multipart/form-data`. → `202`, o un error de §6 tabla A |
| `GET` | `/jobs` | Lista los trabajos, más recientes primero. |
| `GET` | `/jobs/{id}` | Estado y resultado de un trabajo. |
| `GET` | `/jobs/{id}/events` | Flujo SSE de progreso. |
| `DELETE` | `/jobs/{id}` | Cancela si está `queued` o `running`; borra el trabajo y sus archivos si está `done`, `failed` o `cancelled`. |
| `GET` | `/jobs/{id}/stems/{stem}` | Descarga un stem. Admite `Range`. |
| `GET` | `/jobs/{id}/archive` | Descarga todos los stems en un ZIP. |
| `GET` | `/models` | Catálogo de modelos. |
| `GET` | `/system` | Estado del entorno (equivalente a `doctor`). |

### Progreso: SSE, no WebSocket

`GET /jobs/{id}/events` devuelve `text/event-stream`. Elegido porque el progreso es unidireccional —del servidor al cliente—, viaja sobre HTTP normal, y `EventSource` reconecta solo cuando se corta. Un WebSocket añadiría un protocolo bidireccional que aquí no se usa.

**Fallback obligatorio:** el front debe funcionar sondeando `GET /jobs/{id}` cada 2 segundos si el SSE no está disponible. Un trabajo de 8 minutos con sondeo cada 2 s son 240 peticiones locales, que no es problema en localhost.

Eventos emitidos (`elapsed_s` es el tiempo desde que el trabajo pasó a `running`):

```
event: stage
data: {"job_id":"...","status":"running","stage":"separating","elapsed_s":42.1}

event: done
data: {"job_id":"...","status":"done"}

event: failed
data: {"job_id":"...","status":"failed","failed_stage":"separating","error":{...}}

event: cancelled
data: {"job_id":"...","status":"cancelled"}
```

Tras `done`, `failed` o `cancelled`, el servidor cierra el flujo. El front recupera el resultado completo con `GET /jobs/{id}`.

## 6. Esquemas

### Crear un trabajo — `POST /jobs`

`multipart/form-data`:

| Campo | Tipo | Obligatorio | Notas |
|---|---|---|---|
| `file` | archivo | sí | Extensiones de RF-01. Máximo 500 MB (configurable); por encima → `file_too_large`. |
| `model_id` | texto | no | Por defecto, el predeterminado del catálogo. |
| `stems` | texto | no | Separados por comas. Por defecto, todos los del modelo. |
| `output_format` | texto | no | `wav`, `flac` o `mp3`. Por defecto `wav`. |

Respuesta `202`. `input` sale del `MediaInfo` que devuelve `validate()`:

```json
{
  "id": "j_01HQ...",
  "status": "queued",
  "queue_position": 2,
  "created_at": "2026-09-25T09:14:02Z",
  "input": {
    "filename": "cancion.mp3",
    "duration_s": 212.4,
    "kind": "audio"
  },
  "model_id": "vocals",
  "stems_requested": ["vocals", "instrumental"],
  "output_format": "wav",
  "estimated_seconds": 510,
  "estimate_basis": "input.duration_s * cpu_rtf (2.4); incluye la carga del modelo, no la descarga ni la preparación"
}
```

### Consultar un trabajo — `GET /jobs/{id}`

Mismo objeto, más los campos que aparecen según avanza:

```json
{
  "id": "j_01HQ...",
  "status": "done",
  "stage": null,
  "failed_stage": null,
  "queue_position": null,
  "started_at": "2026-09-25T09:14:05Z",
  "finished_at": "2026-09-25T09:22:38Z",
  "elapsed_s": 513.2,
  "output_gain": 0.82,
  "work_duration_s": 212.38,
  "stems": [
    {
      "stem": "vocals",
      "duration_s": 212.38,
      "sample_rate": 44100,
      "channels": 2,
      "size_bytes": 74829312,
      "peak": 0.71,
      "url": "/api/jobs/j_01HQ.../stems/vocals"
    }
  ],
  "error": null
}
```

- `output_gain` y `work_duration_s` son los de `SeparationResult`; `stems[]` sigue a `StemFile` (`interfaces.md` §2), incluido `peak`: el pico del stem exportado, ya con `output_gain` aplicado.
- `output_gain` es la ganancia común aplicada a todos los stems (RF-06). Si se piden todos los stems del modelo, sumarlos y dividir la suma por `output_gain` reconstruye el **audio de trabajo** dentro de la tolerancia de `acceptance.md` §1. **El front lo muestra siempre** (RFW-12), también cuando vale 1,0.
- `work_duration_s` es la duración del audio de trabajo, la referencia con la que se validó la duración de cada stem (RF-06). Puede diferir de `input.duration_s` (por ejemplo, en un video).

### Errores

Todos los errores usan la misma forma, derivada de la jerarquía `StemSeparatorError` de `interfaces.md` §4:

```json
{
  "error": {
    "code": "unsupported_format",
    "message": "El archivo .txt no es un formato admitido.",
    "hint": "Formatos admitidos: wav, mp3, flac, m4a, mp4, mkv.",
    "exit_code": 3
  }
}
```

`message` y `hint` van en español y son directamente presentables al usuario; el front no reescribe textos de error. `code` es estable y en inglés, para que el front pueda ramificar. `exit_code` es el código equivalente de la CLI, o `null` si el error solo existe en la API.

**Tabla A — errores comprobables en `POST /jobs`.** Se devuelven en la respuesta, con código HTTP, antes de encolar. No se crea ningún trabajo.

| Excepción (I1) | `code` | Salida CLI | HTTP |
|---|---|---|---|
| (campo mal formado o que falta; en la CLI, error de uso de Typer) | `invalid_request` | 2 | 422 |
| `FileTooLargeError` | `file_too_large` | 3 | 413 |
| `InputNotFoundError` (el archivo subido no se pudo guardar o leer) | `input_not_found` | 3 | 400 |
| `UnsupportedFormatError` | `unsupported_format` | 3 | 415 |
| `NoAudioStreamError` | `no_audio_stream` | 3 | 422 |
| `MediaReadError` | `media_read_error` | 3 | 422 |
| `UnknownModelError` | `unknown_model` | 3 | 400 |
| `UnsupportedStemError` | `unsupported_stem` | 3 | 400 |
| `FFmpegNotFoundError` | `ffmpeg_not_found` | 4 | 503 |
| `InsufficientDiskSpaceError` (estimado con la duración que da `validate()`) | `insufficient_disk_space` | 4 | 507 |
| (excepción no prevista) | `internal_error` | 1 | 500 |

Las peticiones rechazadas por seguridad (§11) responden `403` (`forbidden`) o `401` (`unauthorized`), con `exit_code: null`.

**Tabla B — errores de ejecución.** No llevan código HTTP: el `POST` ya respondió `202`. Llegan por el evento `failed` y quedan en `error` y `failed_stage` de `GET /jobs/{id}`.

| Origen | `code` | Salida CLI |
|---|---|---|
| Revalidación al ejecutar (`run()` llama a `validate()`): cualquier error de la tabla A de código 3 o 4, p. ej. si otro trabajo de la cola consumió el espacio | el mismo `code` de la tabla A | 3 / 4 |
| `EngineError` | `engine_error` | 5 |
| `ExportError` | `export_error` | 5 |
| (excepción no prevista, o el subproceso terminó sin informar; ADR-006) | `internal_error` | 1 |
| (backend reiniciado con el trabajo en `running`) | `interrupted_by_restart` | `null` |

La cancelación no es un error: el trabajo pasa a `status: "cancelled"` con `error: null`. Equivale al código 130 de la CLI.

No aparecen `OutputConflictError`, `OutputNotWritableError` ni `OutputRestoreError`: el backend gestiona su propio almacenamiento, el usuario nunca elige rutas de salida y nunca hay `--overwrite`. Sin `.bak` que restaurar, el código 6 es inalcanzable, y su pista (renombrar archivos a mano) pediría algo que el usuario no puede hacer dentro del directorio de la aplicación.

### Catálogo — `GET /models`

```json
{
  "models": [
    {
      "id": "vocals",
      "stems": ["vocals", "instrumental"],
      "cpu_rtf": 2.4,
      "cpu_reference": "Intel Core i7-8565U, 4c/8t, 7.8 GB RAM (WSL2)",
      "weights_license": "MIT (UVR), con atribución",
      "description": "...",
      "is_default": true,
      "is_downloaded": true,
      "download_size_bytes": 66762490
    }
  ]
}
```

Los campos salen de `ModelSpec` (`interfaces.md` §2) más `is_downloaded`. `is_downloaded: false` implica que la primera ejecución añadirá el tiempo de descarga al total. **El front debe advertirlo**, porque son 66,8 MB con `vocals` y 639 MB con `vocals-max` (ADR-002).

### Entorno — `GET /system`

Mismos datos que `doctor` (RF-11), más el espacio libre y la cola:

```json
{
  "ok": true,
  "version": "0.1.0",
  "python_version": "3.12.3",
  "audio_separator": {"available": true, "version": "0.47.0"},
  "ffmpeg": {"available": true, "version": "6.1.1"},
  "ffprobe": {"available": true, "version": "6.1.1"},
  "inference_device": "cpu",
  "cpu_threads": 8,
  "free_disk_bytes": 980251508736,
  "model_dir": "/home/user/.cache/stemseparator",
  "queue_length": 0,
  "problems": []
}
```

`ok` es `false` si falta FFmpeg, FFprobe o `audio-separator`, igual que el código 4 de `doctor`. En ese caso, `problems` contiene objetos de error con la misma forma de §6, y el front debe impedir la creación de trabajos.

## 7. Almacenamiento y ejecución

- Cada trabajo tiene su directorio bajo un directorio de datos de la aplicación. Nunca se escribe en carpetas del usuario.
- El estado de cada trabajo se persiste en disco, en su directorio, para sobrevivir a los reinicios (RFW-02). Solo el proceso servidor lo escribe.
- **Ejecución en subproceso** (ADR-006): cada trabajo `running` se ejecuta en un subproceso propio, que llama a `SeparationService.run()` y termina. Así se puede cancelar con la misma semántica que Ctrl+C, la RAM del motor (unos 3,2 GB de pico, ADR-002) vuelve al sistema al acabar, y un fallo nativo del motor no tumba el servidor. El subproceso comunica el progreso por líneas JSON. La cola ejecuta un subproceso a la vez.
- El archivo subido se conserva mientras el trabajo exista, para poder reprocesarlo con otro modelo sin volver a subirlo.
- La escritura atómica de ADR-004 se mantiene: los stems solo aparecen como descargables cuando todos están completos y verificados.
- Retención por defecto: 24 horas desde `finished_at`. Configurable. El barrido se ejecuta al arrancar y cada hora. No afecta a los trabajos `queued` ni `running`.
- `DELETE /jobs/{id}` sobre un trabajo terminado borra su directorio completo.

## 8. Criterios de aceptación

**CAW-01. El trabajo sobrevive al cliente.** Dado un trabajo en `running`, cuando cierro el navegador y vuelvo a abrirlo, entonces `GET /jobs` lo muestra con su estado actual y, al terminar, sus stems son descargables.

**CAW-02. La cola es serie.** Dados tres trabajos creados seguidos, entonces solo uno está en `running` en cada momento, hay como máximo un subproceso de trabajo vivo, y los otros dos indican su `queue_position`.

**CAW-03. Cancelar en cola.** Dado un trabajo en `queued`, cuando lo cancelo, entonces pasa a `cancelled`, no llega a ejecutarse (no se lanza ningún subproceso para él) y no deja archivos.

**CAW-04. Cancelar en ejecución.** Dado un trabajo en `running`, cuando lo cancelo, entonces su subproceso recibe la señal de interrupción y termina, el trabajo pasa a `cancelled`, el directorio del trabajo no contiene stems parciales y el siguiente de la cola arranca. Si el subproceso ignora la señal más allá del plazo de gracia, se mata y el resultado es el mismo.

**CAW-05. Validación antes de encolar.** Dados un archivo con extensión no admitida, un archivo sin pista de audio y un archivo de más de 500 MB, entonces `POST /jobs` responde al instante `415` (`unsupported_format`), `422` (`no_audio_stream`) y `413` (`file_too_large`) respectivamente, y no se crea ningún trabajo.

**CAW-06. Progreso por SSE.** Dado un trabajo en ejecución, entonces el flujo de eventos emite las etapas en el orden de `domain.md` §4 y cierra tras `done`. Si el trabajo falla, el evento `failed` lleva `failed_stage` con la etapa que llamó a `stage_failed`.

**CAW-07. Sondeo como alternativa.** Dado un cliente que no usa SSE, entonces sondear `GET /jobs/{id}` refleja los mismos cambios de estado.

**CAW-08. Ganancia y duración presentes y coherentes.** Dado un trabajo `done`, entonces la respuesta incluye `output_gain` con 0 < `output_gain` ≤ 1 y `work_duration_s` > 0; la `duration_s` de cada stem cumple la tolerancia de duración de `acceptance.md` §1 respecto a `work_duration_s`; y el `peak` de cada stem es ≤ `OUTPUT_PEAK_CEILING`. La prueba acústica de la reconstrucción no se repite aquí: es CA-25, en la capa de servicio, donde el audio de trabajo todavía existe.

**CAW-09. Reinicio con un trabajo en ejecución.** Dado un trabajo en `running` cuando el backend se detiene, entonces al arrancar de nuevo ese trabajo aparece como `failed` con `code: "interrupted_by_restart"` y sin archivos parciales, y no queda ningún subproceso de trabajo huérfano.

**CAW-10. Estimación presente.** Dado un trabajo recién creado, entonces `estimated_seconds` es coherente con `input.duration_s × cpu_rtf` del modelo elegido y `estimate_basis` describe esa base.

**CAW-11. Reproducción parcial.** Dada una petición con cabecera `Range` a un stem, entonces el backend responde `206` con el fragmento pedido.

**CAW-12. Reinicio con trabajos en cola.** Dados dos trabajos en `queued` cuando el backend se detiene, entonces al arrancar de nuevo siguen en `queued`, con el mismo orden, y se ejecutan sin que el usuario haga nada.

**CAW-13. Peticiones hostiles** (RNFW-01, ADR-005). Con el servidor en marcha:
- una petición con `Host: ataque.example` recibe `403`;
- una petición con `Origin: http://ataque.example` recibe `403`, y el preflight `OPTIONS` no devuelve ninguna cabecera `Access-Control-Allow-*`;
- una petición a `/api` sin token, o con un token incorrecto, recibe `401`;
- un `POST /jobs` o un `DELETE` con el token solo en la query, sin la cabecera `X-StemSeparator-Token`, recibe `401`;
- configurar el servidor para escuchar en `0.0.0.0` impide que arranque;
- todas las respuestas llevan `Referrer-Policy: no-referrer`, y una petición con `?token=` no deja el token en el log del servidor;
- en ningún caso se crea ni se borra un trabajo.

**CAW-14. Sin recursos de terceros** (RNFW-02). Dado el front servido por el backend, entonces ninguna página ni recurso referencia un origen distinto del propio, y las respuestas HTML llevan `Content-Security-Policy: default-src 'self'`. Se verifica con una comprobación automática sobre los archivos de `web/` y, a mano, con la pestaña de red del navegador durante un trabajo completo.

## 9. Qué puede construir el front desde hoy

Sin backend, contra datos simulados que cumplan estos esquemas:

- La pantalla de subida, con validación de extensión y tamaño en cliente. El servidor valida igualmente (tabla A de §6).
- La lista de trabajos con sus estados, posición en cola y tiempo estimado.
- La vista de un trabajo con las cinco etapas, el progreso y, si falla, la etapa en la que falló.
- El reproductor y la descarga de stems.
- Los estados de error, uno por cada `code` de las tablas A y B de §6, más `401`/`403` de §11.
- El selector de modelo con el aviso de tiempo y de descarga pendiente.
- El manejo del token (§11): leerlo de la URL, guardarlo en `sessionStorage`, enviarlo y pedir la URL nueva ante un `401`.

**Lo que conviene diseñar con cuidado, porque es lo que distingue a este producto:** la espera. Ocho minutos es demasiado para mirar una barra y demasiado poco para olvidarse. Merece pensar qué ve el usuario mientras tanto, cómo se le avisa al terminar si cambió de pestaña, y cómo se le comunica sin alarmar que elegir `vocals-max` significa dos horas.

## 10. Decisiones abiertas

1. **Aviso al terminar.** ¿Notificaciones del navegador, título de pestaña parpadeante, o nada? Afecta a permisos y al diseño.
2. **Reprocesar con otro modelo.** ¿Se ofrece un botón que reutilice el archivo ya subido? El almacenamiento lo permite; falta decidir si entra en I8.
3. **Puerto y arranque.** ¿La interfaz se lanza con un comando propio (`stemseparator serve`, que podría abrir el navegador con la URL del token) o con un ejecutable aparte? Afecta a `interfaces.md` §1.
4. **Tecnología del front.** No la fija este documento; la decide el arquitecto. Restricción: RNFW-02 (sin recursos de terceros en tiempo de ejecución).

## 11. Seguridad

Resumen operativo de ADR-005. Cualquier web abierta en el navegador del usuario puede alcanzar `127.0.0.1` (CSRF, DNS rebinding), así que "local" no basta.

1. **Solo loopback.** El servidor escucha únicamente en `127.0.0.1` (y `::1` si se habilita). Si se configura otra dirección, no arranca.
2. **Lista blanca de `Host`**: `127.0.0.1:<puerto>`, `localhost:<puerto>`, `[::1]:<puerto>`. Cualquier otro → `403 forbidden`. Neutraliza el DNS rebinding.
3. **Lista blanca de `Origin`**: si la petición trae `Origin`, debe ser `http://` más uno de esos hosts; si no → `403 forbidden`. El servidor nunca emite cabeceras CORS.
4. **Token por arranque.** Al arrancar se genera un token aleatorio (`secrets.token_urlsafe(32)`) y se imprime la URL de acceso `http://127.0.0.1:<puerto>/?token=<token>`. Cada petición a `/api` debe llevarlo; si falta o no es válido → `401 unauthorized`. El front lo toma de la URL, lo guarda en `sessionStorage` y lo quita de la barra de direcciones. Un reinicio del servidor genera otro.
5. **Cabecera propia.** `POST` y `DELETE` deben enviar el token en `X-StemSeparator-Token`. Al ser una cabecera no estándar, el navegador exige preflight, y el preflight de un origen ajeno falla (punto 3). Un formulario de CSRF no puede añadirla.
6. **Lecturas en streaming.** `EventSource`, `<audio>` y los enlaces de descarga no pueden enviar cabeceras. Solo en `GET /jobs/{id}/events`, `GET /jobs/{id}/stems/{stem}` y `GET /jobs/{id}/archive`, el token puede ir como `?token=`. Siguen aplicando los puntos 2 y 3. Se evaluó sustituirlo por una cookie `SameSite=Strict` y se descartó porque las cookies no aíslan por puerto (ADR-005).
7. **Sin fugas del token**: `Referrer-Policy: no-referrer` en todas las respuestas (API y front), `Cache-Control: no-store` en `/api`, y el servidor no registra la query string en sus logs.

El resto de `GET` a `/api` lleva el token en la cabecera. Los archivos estáticos del front (`/`, JS, CSS) no requieren token porque no contienen datos, pero sí las comprobaciones de `Host` y `Origin`.

## Registro de cambios

- v0.1 — Borrador inicial. Alcance local, cola en serie y mismo repositorio, decididos por el usuario el 2026-09-25. Diseño derivado de las mediciones de T-01 (ADR-002).
- v0.2 (2026-09-25) — Revisión contra las specs aprobadas v0.3:
  - Seguridad: nueva §11, RNFW-01, CAW-13 y ADR-005.
  - Subproceso por trabajo: ADR-006, §7, RFW-04, RFW-06, CAW-02, CAW-04 y CAW-09.
  - Validación en el `POST` con `SeparationService.validate()`: RFW-01 y CAW-05.
  - Errores: tabla dividida en A (`POST`, con HTTP) y B (ejecución, sin HTTP); `invalid_request`, `file_too_large` e `internal_error`; quitado `OutputRestoreError`.
  - Estados: `failed_stage`; los trabajos `queued` sobreviven al reinicio (RFW-02, CAW-12).
  - Ganancia y campos: RFW-12 habla de audio de trabajo y la ganancia se muestra siempre; CAW-08 reformulado; `peak` y `download_size_bytes` añadidos a `interfaces.md`; ejemplo corregido a 66,8 MB; base de la estimación documentada.
  - Referencias: `acceptance.md` §1; RF-13, CA-19 y ADR-004 citados juntos; `RFW`, `RNFW` y `CAW` en `constitution.md` §2; `web/` en `architecture.md` §2; cabecera "Depende de" completada.
  - Otros: RNFW-02 y CAW-14 (sin recursos de terceros); `/system` completo según RF-11.
  - Tras la aprobación: `Referrer-Policy: no-referrer`, `Cache-Control: no-store` y logs sin query (§11 punto 7, CAW-13); la alternativa de cookie `SameSite=Strict` se evaluó y se descartó en ADR-005.
- 2026-09-25 — Aprobado v0.2.
