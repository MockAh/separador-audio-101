# Interfaces y contratos

Estado: Aprobado v0.16 (2026-10-10)

Las firmas son contractuales: cambiarlas requiere actualizar este documento. Los cuerpos de ejemplo no lo son.

## 1. CLI

```
stemseparator separate INPUT [OPCIONES]
  INPUT                      Ruta al archivo de audio o video.
  -o, --output DIR           Directorio de salida. Por defecto: directorio actual.
  -m, --model ID             Identificador del catálogo. Por defecto: el modelo predeterminado.
  -s, --stems LISTA          Stems separados por comas. Por defecto: todos los del modelo.
  -f, --format [wav|flac|mp3]  Por defecto: wav.
      --overwrite            Sobrescribir archivos existentes. Durante el traslado se guardan
                             copias .stemseparator-tmp-*.bak que se borran al terminar (ADR-004).
  -v, --verbose              Logs detallados y traceback en errores.

stemseparator models         Lista el catálogo.
stemseparator doctor         Diagnóstico del entorno.
stemseparator --version
```

Ejemplo de I1:
```
stemseparator separate cancion.mp4 -o ./stems -m vocals-max -s vocals,instrumental -f flac
→ ./stems/cancion_vocals.flac
→ ./stems/cancion_instrumental.flac
```

Comportamiento de la CLI (T-12):

- **Flujos de salida**: el mensaje y la sugerencia de un error van a stderr; el progreso y la tabla de resumen, a stdout. Motivo: RNF-09 existe para que un script use los códigos de salida, y si todo va por un mismo flujo, cualquier redirección lo rompe.
- **`--verbose`** hace exactamente tres cosas: (1) muestra el traceback en los errores; (2) construye `AudioSeparatorEngine(verbose=True)`, es decir, `log_level=DEBUG` en el motor (§3); (3) sube el logger `stemseparator.media.ffmpeg` a DEBUG, que es por donde llega el stderr de FFmpeg (ADR-003). Nada más: en particular, no recupera las barras de progreso del motor.
- **`--stems`** se recibe como una cadena, y la CLI la parte por comas y convierte cada nombre en `StemName`:
  - La coincidencia es exacta y distingue mayúsculas, como en los ids de modelo.
  - Se recortan los espacios alrededor de cada nombre.
  - Los duplicados se colapsan conservando el orden.
  - Un nombre que no es un `StemName` (p. ej. `drums` en I1) da `UnsupportedStemError` (código 3), no un error de Typer (código 2). El mensaje lista los stems del modelo pedido, o del predeterminado si no se pasa `--model`.
  - La CLI hace esta comprobación antes de llamar al servicio, porque `SeparationRequest.stems` solo admite `StemName`. Por eso se adelanta a las comprobaciones de VALIDATING (entrada, FFmpeg). Para listar los stems resuelve el modelo con `get_model()`, que lanza `UnknownModelError` si el id no existe.
  - Consecuencia: `separate noexiste.wav -s drums` da el error de stem, no el de entrada. Un nombre mal escrito es un error de la invocación, como `--format xyz`, que también se adelanta a todo. La diferencia es el código, 3 en lugar de 2, para que el mensaje pueda listar los stems.
  - En ese camino no se notifica ninguna etapa al `ProgressReporter`, porque el trabajo no ha empezado.
  - Un stem válido que el modelo no produce sigue siendo cosa del servicio.
- **Progreso**: una sola etapa a la vez, con indicador de actividad y tiempo transcurrido (RF-08). No se muestran las barras del motor, ni siquiera con `--verbose`, porque `--verbose` hace solo las tres cosas de arriba. En LOADING_MODEL, si el archivo del modelo (`engine_filename`) no está en el directorio de modelos, el indicador dice que se están descargando los pesos. No se muestra ningún porcentaje, porque no se conoce.

## 2. Modelos de datos (`domain/models.py`)

```python
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from pydantic import BaseModel, ConfigDict

class StemName(StrEnum):
    VOCALS = "vocals"
    INSTRUMENTAL = "instrumental"
    # I3+: DRUMS, BASS, OTHER; I4+: GUITAR, PIANO

class OutputFormat(StrEnum):
    WAV = "wav"
    FLAC = "flac"
    MP3 = "mp3"

class MediaKind(StrEnum):
    AUDIO = "audio"
    VIDEO = "video"

class MediaInfo(BaseModel):
    model_config = ConfigDict(frozen=True)
    path: Path
    kind: MediaKind             # VIDEO solo si hay un stream de video que no es
                                # carátula (disposition.attached_pic == 0)
    duration_s: float
    has_audio: bool
    sample_rate: int | None     # de la primera pista de audio
    channels: int | None
    codec: str | None           # códec de la primera pista de audio

class ModelSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str                                  # "vocals"
    engine_filename: str                     # nombre en audio-separator
    stems: tuple[StemName, ...]
    engine_labels: Mapping[str, StemName]    # etiqueta del motor → StemName
    cpu_rtf: float                           # factor de tiempo real medido en CPU
                                             # (s de proceso por s de audio), p. ej. 2.4
    cpu_reference: str                       # equipo en el que se midió cpu_rtf (ADR-002)
    weights_license: str
    download_size_bytes: int                 # bytes que añade la descarga de pesos (ADR-002)
    description: str                         # en español, para `models`
    is_default: bool = False

class SeparationRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    input_path: Path
    output_dir: Path
    model_id: str | None = None              # None → predeterminado
    stems: frozenset[StemName] | None = None # None → todos los del modelo
    output_format: OutputFormat = OutputFormat.WAV
    overwrite: bool = False

class StemFile(BaseModel):
    model_config = ConfigDict(frozen=True)
    stem: StemName
    path: Path
    duration_s: float
    sample_rate: int
    channels: int
    size_bytes: int
    peak: float                              # pico lineal del stem exportado (ya con
                                             # output_gain): pico de origen × output_gain

class SeparationResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    input: MediaInfo
    work_duration_s: float                   # audio de trabajo, sondeado tras normalizar;
                                             # referencia de duración de los stems
    model_id: str
    stems: tuple[StemFile, ...]
    output_gain: float                       # ganancia común (0 < g ≤ 1) aplicada a todos
                                             # los stems al exportar para que ninguno sature;
                                             # la suma de los stems ≈ output_gain × audio de trabajo
    elapsed_s: float
    interrupted_during_publish: bool = False # True si llegó un SIGINT durante una publicación
                                             # que terminó bien (CA-19, ADR-004): el trabajo
                                             # se da por completado, el código es 0 y la CLI
                                             # avisa explícitamente

OUTPUT_PEAK_CEILING: float = 0.999            # techo de pico de los archivos exportados

# Tolerancia de duración de acceptance.md §1: |duración_stem − work_duration_s| ≤ valor.
# Mapa inmutable (no dict). La usan el servicio en tiempo de ejecución (T-09) y los
# tests de exportación (T-06).
DURATION_TOLERANCE_S: Mapping[OutputFormat, float] = MappingProxyType(
    {OutputFormat.WAV: 0.050, OutputFormat.FLAC: 0.050, OutputFormat.MP3: 0.100}
)
```

## 3. Puertos y servicios

```python
# domain/progress.py  (en domain para que engines y application lo usen sin
#                      romper la regla de dependencias)
class JobStage(StrEnum):
    VALIDATING = "validating"; PREPARING = "preparing"; LOADING_MODEL = "loading_model"
    SEPARATING = "separating"; EXPORTING = "exporting"; DONE = "done"; FAILED = "failed"
    # DONE y FAILED son solo estados terminales del ciclo de vida (domain.md §4);
    # nunca se pasan como argumento a stage_started, stage_finished ni stage_failed.

class ProgressReporter(Protocol):
    """Recibe las transiciones de etapa de un trabajo de separación.

    Quien llamó stage_started de una etapa es responsable de llamar
    stage_failed de esa misma etapa antes de propagar la excepción. El
    servicio no reporta el fallo de las etapas que notifica el motor.
    Así, stage_failed se llama una sola vez por trabajo fallido, con la
    etapa en curso al fallar (incluye Ctrl+C). DONE y FAILED no se
    notifican: en un trabajo correcto, la última llamada es
    stage_finished(EXPORTING)."""
    def stage_started(self, stage: JobStage) -> None: ...
    def stage_finished(self, stage: JobStage, elapsed_s: float) -> None: ...
    def stage_failed(self, stage: JobStage, error: BaseException) -> None: ...

# engines/base.py
class SeparationEngine(Protocol):
    def separate(self, audio_path: Path, model: ModelSpec, work_dir: Path,
                 model_dir: Path,
                 reporter: ProgressReporter | None = None) -> dict[StemName, Path]:
        """Separa audio_path con el modelo. Escribe solo dentro de work_dir.
        Devuelve una ruta WAV por cada stem de model.stems.
        Si hay reporter, notifica stage_started/stage_finished (o stage_failed)
        de LOADING_MODEL (descarga y carga de pesos) y de SEPARATING (inferencia).
        Lanza EngineError si el motor falla, si falta alguna salida esperada o
        si el motor eligió CUDA (RNF-05)."""

# media/base.py
class MediaProbe(Protocol):
    def probe(self, path: Path) -> MediaInfo: ...
        # Lanza MediaReadError si ffprobe no puede leer el archivo.
        # duration_s: format.duration del JSON de ffprobe; si falta, la duration de la primera pista de audio.
        # Sin duración legible o no convertible a float → MediaReadError.
        # ffprobe con tiempo límite de 30 s (agotarlo → MediaReadError); la conversión de T-06 no lleva límite.
        # ffprobe se invoca con -v error para conservar el stderr (ADR-003); el JSON va por stdout.

class AudioNormalizer(Protocol):
    def normalize(self, src_path: Path, dest: Path) -> Path: ...
        # Se aplica a TODA entrada (audio o video): primera pista de audio →
        # WAV pcm_f32le, 44 100 Hz, 2 canales. NoAudioStreamError si no hay audio.
        # Mapeo de errores: entrada sin pista de audio → NoAudioStreamError; cualquier otro
        # fallo de ffmpeg al decodificar la entrada → MediaReadError.

class StemExporter(Protocol):
    # Un fallo de peak() o de export() → ExportError.
    def peak(self, src_wav: Path) -> float: ...
        # Pico absoluto lineal de las muestras (1.0 = 0 dBFS; puede superar 1.0 en float).
        # El medidor mide en coma flotante y NO recorta en 1.0: devuelve el pico real aunque
        # lo supere (ADR-002 midió 1,15 y 1,22 en stems del motor). De esta lectura depende
        # output_gain; un medidor que saturase en 0 dBFS devolvería 1.0 para todo y rompería
        # CA-25 sin avisar.
    def export(self, src_wav: Path, dest: Path, fmt: OutputFormat, gain: float) -> Path: ...
        # Multiplica por gain; 44 100 Hz, 2 canales; wav=pcm_s24le, flac=24 bits, mp3=320k CBR.

class SystemChecks(Protocol):
    def require_ffmpeg(self) -> None: ...
        # Comprueba que `ffmpeg` Y `ffprobe` están en el PATH. Si falta alguno →
        # FFmpegNotFoundError; el mensaje nombra cuál falta (o los dos).
        # El hint lista SIEMPRE los tres sistemas (CA-04), en orden fijo Windows, macOS,
        # Linux, para que el test no dependa del SO donde corre, con estos valores literales:
        #     Windows: winget install Gyan.FFmpeg
        #     macOS: brew install ffmpeg
        #     Linux (Debian/Ubuntu): sudo apt install ffmpeg
        # y una línea final: abre una terminal nueva y comprueba con `ffmpeg -version`.
    def free_bytes(self, path: Path) -> int: ...
        # Bytes libres del volumen de `path`. Si `path` no existe, se mide el primer
        # directorio ancestro que exista (la raíz siempre existe, así que termina).
        # El servicio la llama sobre el volumen temporal y el de salida, que normalmente
        # ya existen: es defensa, y medir el volumen del padre es preferible a
        # FileNotFoundError.

# media/system.py
class LocalSystemChecks: ...                 # implementa SystemChecks
def default_model_dir() -> Path: ...         # STEMSEPARATOR_MODEL_DIR o platformdirs; solo la usa cli
    # STEMSEPARATOR_MODEL_DIR si está definida y no está vacía ni en blanco; se expande
    # `~` y se resuelve a ruta absoluta. Vacía o solo espacios = no definida.
    # Si no, platformdirs.user_cache_path(appname="stemseparator", appauthor=False) más
    # un subdirectorio `models`. appauthor=False evita el nivel extra del autor en
    # Windows; el subdirectorio deja sitio a la caché de resultados de I6 sin migrar nada.
    # NO crea ningún directorio: solo calcula la ruta. Quien descargue los pesos (T-11)
    # lo crea.

# domain/catalog.py
def list_models() -> tuple[ModelSpec, ...]: ...
def get_model(model_id: str | None) -> ModelSpec: ...  # UnknownModelError

# application/service.py
class SeparationService:
    def __init__(self, engine: SeparationEngine, probe: MediaProbe,
                 normalizer: AudioNormalizer, exporter: StemExporter,
                 system: SystemChecks, model_dir: Path) -> None: ...
    def validate(self, request: SeparationRequest,
                 reporter: ProgressReporter | None = None) -> MediaInfo: ...
        # Etapa VALIDATING completa (domain.md §4): comprobaciones previas,
        # creación del directorio de salida, sondeo de la entrada (NoAudioStreamError)
        # y espacio en disco (domain.md §5). No escribe nada salvo el directorio de
        # salida. Cuesta milisegundos: el backend web la llama en POST /jobs para
        # rechazar al instante (i8-api.md §6). Lanza las excepciones de §4 con código 3 o 4.
    def run(self, request: SeparationRequest,
            reporter: ProgressReporter | None = None) -> SeparationResult: ...
        # Empieza llamando a validate(): no hay lógica de validación duplicada, y un
        # trabajo que esperó en cola se revalida al ejecutarse.
        # El servicio notifica VALIDATING, PREPARING y EXPORTING (y el fallo de
        # esas etapas); LOADING_MODEL y SEPARATING las notifica el motor.
        # En EXPORTING calcula output_gain = min(1.0, OUTPUT_PEAK_CEILING / pico máximo)
        # sobre TODOS los stems que devolvió el motor (no solo los pedidos, para que la
        # ganancia no dependa de --stems) y la pasa a export() para cada stem.
        # Si ese pico máximo es ≤ 0 (todos los stems en silencio), output_gain = 1,0:
        # la fórmula dividiría por cero.
```

### `AudioSeparatorEngine` (`engines/audio_separator_engine.py`)

Contrato con `audio-separator` 0.47.0, confirmado en el spike T-01 (ADR-002):

```python
class AudioSeparatorEngine:                  # implementa SeparationEngine
    def __init__(self, verbose: bool = False) -> None: ...
        # verbose -> log_level=logging.DEBUG; si no, logging.WARNING (los avisos del motor
        # se ven sin --verbose). Lo construye la CLI con su --verbose.
    def separate(self, audio_path: Path, model: ModelSpec, work_dir: Path,
                 model_dir: Path,
                 reporter: ProgressReporter | None = None) -> dict[StemName, Path]: ...
        # Firma idéntica a la de SeparationEngine en engines/base.py (fijada en T-08).
    def inference_device(self) -> str: ...   # "cpu" | "mps" | "cuda"; lo usa doctor
        # Lo llama doctor (T-13) sin haber separado nada. La primera llamada construye un
        # Separator desechable con un directorio temporal (model_file_dir y output_dir,
        # borrado al terminar), con la telemetría desactivada (paso 1), y lee el
        # dispositivo como en el paso 4. Sin load_model() ni separate(): no descarga ni
        # separa. Cacheado en la instancia: las llamadas siguientes no construyen otro
        # Separator. No lanza EngineError por CUDA (informar es de doctor; el rechazo, del paso 4).
```

No se pregunta a `torch` por separado: duplicaría la detección del motor y podría discrepar de ella, y el adaptador no toca sus interioridades (ADR-001).

Etapas de `separate()`: **LOADING_MODEL** cubre los puntos 1 a 4 y **SEPARATING** los puntos 5 a 8. El margen de entrada (punto 2) va dentro de LOADING_MODEL para que un fallo al leer la entrada tenga etapa en la que reportar `stage_failed` (CA-13). Dentro del punto 3, la comprobación del dispositivo (punto 4) se hace entre construir `Separator` y `load_model()`: con CUDA no se llega a `load_model()` (CA-23).

1. Antes de importar `onnxruntime` y `audio_separator` (import perezoso): `os.environ["ORT_DISABLE_TELEMETRY"] = "1"`, y después `onnxruntime.disable_telemetry_events()` (RNF-12). La variable de entorno es la que funciona: en T-01, la llamada a la API sola no evitó las conexiones a `mobile.events.data.microsoft.com` durante `separate()`.
   En el mismo momento, antes de importar `audio_separator`, el adaptador también silencia la consola del motor:
   - Fija `os.environ["TQDM_DISABLE"] = "1"`. tqdm lee sus variables `TQDM_*` al importarse, así que fijarla después no tiene efecto. Esto quita las barras de la inferencia y también la de la descarga de pesos.
   - Añade al logger `audio_separator.separator.separator` un `logging.Filter` que descarta el registro "Using soundfile for writing.". Lo añade una sola vez por proceso, aunque se construyan varios motores.

   Motivo: las barras de tqdm y ese aviso, que sale con nivel WARNING una vez por cada stem escrito, ensucian la salida de cualquier consumidor del servicio, la CLI y el backend web de I8. Con un indicador de Rich activo es peor: Rich desvía `sys.stderr`, y cada actualización de una barra se apila como una línea nueva (observado en T-12). Lo demás que registra el motor se conserva (decisión de T-11).
   El filtro depende del texto literal, en inglés, de `audio-separator` 0.47.0. Si ese texto cambia, el aviso vuelve a aparecer, sin causar daño.
2. **Margen de entrada**: el adaptador escribe `work_dir/engine_input.wav` = `audio_path` × `ENGINE_INPUT_HEADROOM` (0,5, es decir −6 dB; float32) y es eso lo que pasa al motor. Las muestras se leen y escriben con `soundfile` y `numpy`, importados de forma perezosa en el adaptador; son dependencias declaradas de `audio-separator` (ver `architecture.md` §4). Motivo: `audio-separator` reescala por separado cualquier stem cuyo pico pase de `normalization_threshold`, y ese umbral no admite valores mayores que 1,0 (T-01). Sin margen, el instrumental real (pico de 1,15 a 1,22 medido) se reescalaría y la suma de los stems dejaría de reconstruir la mezcla.
3. **Carga del modelo** (dentro de LOADING_MODEL) = construir `Separator(...)` + `load_model(model_filename=model.engine_filename)`. `load_model()` descarga los pesos si faltan. Parámetros del constructor:
   `model_file_dir=model_dir`, `output_dir=work_dir`, `output_format="WAV"`, `sample_rate=44100`, `use_soundfile=True` (no cuantiza a 16 bits), `normalization_threshold=1.0` (el máximo que admite; con el margen del paso 2, ningún stem debería llegar a él), `log_level` según `verbose` (ver `__init__`).
   **`AUDIO_SEPARATOR_MODEL_DIR`**: si está definida, el constructor de `Separator` la usa en lugar de `model_file_dir` sin avisar (y lanza si la ruta no existe), lo que anularía `STEMSEPARATOR_MODEL_DIR` (RF-12). El adaptador la retira de `os.environ` mientras construye `Separator` y la restaura después, también si la construcción falla. En `separate()` registra además un `logger.warning` que dice que `AUDIO_SEPARATOR_MODEL_DIR` se ignora y cuál es el `model_dir` que manda. En `inference_device()` la retira y restaura igual, y avisa sin ruta: dice que `AUDIO_SEPARATOR_MODEL_DIR` se ignora y remite a `STEMSEPARATOR_MODEL_DIR` (ahí no hay `model_dir` que nombrar, pero quien tiene la variable puesta ejecuta `doctor` para entender qué pasa).
4. Dispositivo: `audio-separator` no tiene un parámetro para fijarlo; lo detecta solo (CUDA si `torch` la ve, MPS en Apple Silicon, si no CPU). El adaptador no toca sus atributos internos (ADR-001): solo lee, sin modificarlo, el atributo público `torch_device` (`str(separator.torch_device)`, la misma lectura que usó el spike de T-01). Tras construir `Separator`, registra en el log el dispositivo elegido y lanza `EngineError` si es CUDA (RNF-05).
5. **SEPARATING** = `separate(str(work_dir / "engine_input.wav"), custom_output_names={etiqueta: stem.value for etiqueta, stem in model.engine_labels.items()})`. Así el motor escribe `work_dir/vocals.wav` y `work_dir/instrumental.wav` y el adaptador no interpreta nombres de archivo. Las claves de `custom_output_names` no distinguen mayúsculas.
6. Si falta alguna salida de `model.stems` → `EngineError`.
7. **Guardián**: si el pico de algún stem es ≥ `ENGINE_RESCALE_GUARD` (0,9999), el motor lo reescaló → `EngineError`. No se compara con 1,0 exacto porque esa igualdad es frágil en float tras conversiones de formato.
8. Deshace el margen: multiplica cada stem por 1 / `ENGINE_INPUT_HEADROOM` y lo reescribe **en su sitio** en float32 (puede superar 1,0 sin saturar). Así, los stems que recibe el servicio están al nivel del audio de trabajo y su suma lo reconstruye.

## 4. Errores y códigos de salida (`domain/errors.py`)

Todas las excepciones heredan de `StemSeparatorError(message: str, hint: str)`. La CLI muestra `message` y `hint` en español.

```python
from typing import ClassVar

class StemSeparatorError(Exception):
    exit_code: ClassVar[int]     # solo la anotación, sin valor: toda subclase
                                 # está obligada a fijar el código de la tabla
    def __init__(self, message: str, hint: str) -> None: ...
```

- `hint` es obligatorio en toda la jerarquía: no tiene valor por defecto (tampoco `""`).
- Cada excepción de la tabla es una subclase que fija `exit_code`. Se implementan todas en I1, incluida `FileTooLargeError`, aunque solo la lance el backend web de I8: la tabla es la spec de `errors.py` y no se deja con huecos (P2).
- Los códigos 0, 1, 2 y 130 no son excepciones del dominio: los decide la CLI (T-12).

| Excepción | Cuándo | Código de salida |
|-----------|--------|------------------|
| — | Éxito | 0 |
| (inesperada) | Cualquier excepción no prevista | 1 |
| (Typer) | Error de uso / argumentos | 2 |
| `InputNotFoundError` | La entrada no existe o no es legible | 3 |
| `UnsupportedFormatError` | Extensión no admitida | 3 |
| `NoAudioStreamError` | El archivo no tiene pista de audio | 3 |
| `MediaReadError` | ffprobe o ffmpeg no pueden leer o decodificar el archivo de entrada | 3 |
| `UnknownModelError` | Id no está en el catálogo | 3 |
| `UnsupportedStemError` | Stem pedido que el modelo no produce | 3 |
| `OutputConflictError` | Salidas existentes sin `--overwrite` | 3 |
| `OutputNotWritableError` | Directorio de salida no escribible | 3 |
| `FileTooLargeError` | El archivo subido supera el límite de tamaño (500 MB por defecto). Solo lo lanza el backend web (`i8-api.md`); la CLI no tiene límite | 3 |
| `FFmpegNotFoundError` | Falta ffmpeg o ffprobe | 4 |
| `InsufficientDiskSpaceError` | Espacio insuficiente. El mensaje indica el espacio necesario y el disponible en MB (10⁶ bytes, con un decimal y coma decimal, p. ej. "26,5 MB") y la ruta del volumen que falló (CA-17) | 4 |
| `EngineError` | Falla la descarga, carga o ejecución del modelo, o el motor eligió CUDA (RNF-05) | 5 |
| `ExportError` | Falla la medición o la conversión de un stem, o su duración se sale de la tolerancia | 5 |
| `OutputRestoreError` | Falla la restauración de un `.bak` tras un fallo; el original queda bajo otro nombre y hay que intervenir (ADR-004). Prioridad sobre 5 y 130 | 6 |
| (interrupción) | El usuario pulsa Ctrl+C fuera de la sección crítica; se limpia según ADR-004 | 130 |

`stemseparator doctor` termina con código 4 si falta `ffmpeg`, `ffprobe` o
`audio-separator` (no importable); con 0 si todo está presente.

## Registro de cambios
- v0.2 (2026-09-24) — Protocolo `SystemChecks`; `JobStage.FAILED`; DONE y FAILED solo
  estados terminales, no notificados; y `stage_failed()` con regla de propiedad; `JobStage`/`ProgressReporter`
  movidos a `domain/progress.py`; `separate()` recibe `reporter`;
  `AudioExtractor.extract()` → `AudioNormalizer.normalize()`, aplicado a toda
  entrada; regla `attached_pic`; códigos de salida de `doctor`;
  `SeparationResult.work_duration_s`; `OutputRestoreError` (código 6).
- 2026-09-24 — Aprobado v0.2.
- v0.3 (2026-09-25) — T-01: `ModelSpec.cpu_cost` sustituido por `cpu_rtf` + `cpu_reference`; `SeparationResult.output_gain` y `OUTPUT_PEAK_CEILING`; `StemExporter.peak()` y `export(..., gain)`; quitada la nota sobre LOADING_MODEL/SEPARATING (son llamadas separadas); contrato de `AudioSeparatorEngine` con los parámetros confirmados, `custom_output_names` a partir de `engine_labels`, telemetría desactivada (`ORT_DISABLE_TELEMETRY`), margen de entrada de −6 dB y guardián de reescalado (pico ≥ 0,9999), `inference_device()` y `EngineError` si el motor elige CUDA; `AudioNormalizer` a `pcm_f32le` y 44 100 Hz.
- 2026-09-25 — Aprobado v0.3.
- v0.4 (2026-09-25) — Revisión de `i8-api.md`: `SeparationService.validate()` (VALIDATING + sondeo + espacio), que `run()` llama internamente; `StemFile.peak`; `ModelSpec.download_size_bytes`; `FileTooLargeError` (código 3, solo backend web). Aprobado por el usuario.
- v0.5 (2026-09-26) — T-03: §2 `DURATION_TOLERANCE_S` (mapa inmutable por `OutputFormat`: 50 ms WAV/FLAC, 100 ms MP3), la tolerancia de `acceptance.md` §1 como interfaz pública en `domain/models.py`; §4 `StemSeparatorError.exit_code: ClassVar[int]` (sin valor en la base; cada subclase fija el suyo), `hint` obligatorio en toda la jerarquía y `FileTooLargeError` implementada ya en I1; `OUTPUT_PEAK_CEILING` movida de §3 a §2, junto a `DURATION_TOLERANCE_S` (ambas son de `domain/models.py`). Aprobado por el usuario.
- v0.6 (2026-09-30) — T-04: `ModelSpec.engine_labels` pasa de `dict` a `Mapping[str, StemName]` (inmutabilidad verificada por mypy, sin validador). Aprobado por el usuario.
- v0.7 (2026-10-01) — T-05: contrato de `MediaProbe.probe()`: `duration_s` sale de `format.duration` y, si falta, de la primera pista de audio; sin duración legible o no convertible a float → `MediaReadError`; ffprobe con tiempo límite de 30 s (agotarlo → `MediaReadError`), la conversión de T-06 sin límite. Aprobado por el usuario.
- v0.8 (2026-10-01) — T-05: `MediaProbe.probe()` invoca ffprobe con `-v error` para conservar el stderr (ADR-003); el JSON va por stdout. Aprobado por el usuario.
- v0.9 (2026-10-02) — T-06: `StemExporter.peak()` mide en coma flotante y no recorta en 1,0 (de esa lectura depende `output_gain`, CA-25); mapeo de errores de `AudioNormalizer.normalize()` (sin pista de audio → `NoAudioStreamError`; otro fallo al decodificar la entrada → `MediaReadError`) y de `StemExporter` (fallo de `peak()` o `export()` → `ExportError`); §4 `MediaReadError` cubre también que ffmpeg no pueda leer o decodificar la entrada (un archivo truncado que ffprobe leyó puede fallar al normalizarse: error del usuario, código 3, no de exportación, código 5); §4 `ExportError` cubre también que falle la medición de un stem, en coherencia con §3. Aprobado por el usuario.
- v0.10 (2026-10-03) — T-07: contrato de `SystemChecks.require_ffmpeg()` (comprueba `ffmpeg` y `ffprobe`, el mensaje nombra el que falta, y el hint lista siempre Windows, macOS y Linux en ese orden, con comandos literales y la línea final de comprobación, como exige CA-04; sustituye "instrucciones por SO", que se leía como si bastara el SO actual); `free_bytes()` mide el primer ancestro existente si `path` no existe; contrato de `default_model_dir()` (`STEMSEPARATOR_MODEL_DIR` no vacía, con `~` expandido y resuelta a absoluta; si no, `platformdirs.user_cache_path(appname="stemseparator", appauthor=False) / "models"`; no crea directorios). Aprobado por el usuario.
- v0.11 (2026-10-03) — T-08: §4, `InsufficientDiskSpaceError` indica en el mensaje el espacio necesario y el disponible en MB (10⁶ bytes, un decimal, coma decimal) y la ruta del volumen que falló (CA-17), para que T-12 no lo redescubra. Aprobado por el usuario.
- v0.12 (2026-10-04) — T-09: §3, comentario de `run()`: si el pico máximo de los stems del motor es ≤ 0 (todos en silencio), `output_gain` = 1,0. Motivo: la fórmula divide por ese pico y una entrada muda la hacía fallar con `ZeroDivisionError`. Aprobado por el usuario.
- v0.13 (2026-10-04) — T-10: §2, `SeparationResult.interrupted_during_publish: bool = False`, verdadero cuando llegó un `SIGINT` durante una publicación que terminó bien (CA-19): el trabajo se da por completado, el código es 0 y la CLI avisa explícitamente. Motivo: el servicio no tenía forma de comunicarlo, y la alternativa (un `logger.warning`) acoplaría la CLI al log. Aprobado por el usuario.
- v0.14 (2026-10-06) — T-11: §3, la firma de `AudioSeparatorEngine.separate()` se escribe completa, copiada de `SeparationEngine` en `engines/base.py` (fijada en T-08; antes figuraba con puntos suspensivos en un documento cuyas firmas son contractuales); contrato de `inference_device()` (`Separator` desechable con directorio temporal, sin `load_model()` ni `separate()`, resultado cacheado en la instancia, devuelve `"cpu"`, `"mps"` o `"cuda"` sin lanzar por CUDA, sin consultar a `torch` aparte); el paso 4 precisa cómo se lee el dispositivo (atributo público `torch_device`, solo lectura). Aprobado por el usuario.
- v0.15 (2026-10-06) — T-11: §3, `AudioSeparatorEngine.__init__(verbose: bool = False)` (`logging.DEBUG` con `verbose`, si no `logging.WARNING`, para que los avisos del motor se vean sin `--verbose`); qué etapa cubre cada punto del contrato (LOADING_MODEL = 1 a 4, con el margen de entrada dentro para que un fallo al leer la entrada tenga dónde reportar `stage_failed`; SEPARATING = 5 a 8; con CUDA no se llega a `load_model()`); `AUDIO_SEPARATOR_MODEL_DIR` retirada del entorno mientras se construye `Separator` y restaurada después, con aviso en el log en `separate()` (con el `model_dir` que manda) y en `inference_device()` (sin ruta, remitiendo a `STEMSEPARATOR_MODEL_DIR`) (la variable anulaba `model_file_dir` sin avisar y con ella `STEMSEPARATOR_MODEL_DIR`, RF-12). Aprobado por el usuario.
- v0.16 (2026-10-10) — T-12: §1, comportamiento de la CLI: los errores (mensaje y sugerencia) van a stderr, y el progreso y la tabla a stdout (RNF-09: si todo va por un mismo flujo, la redirección se rompe); `--verbose` hace exactamente tres cosas (traceback, `log_level=DEBUG` en el motor y `stemseparator.media.ffmpeg` en DEBUG, decisión del 2026-10-01); cómo se analiza `--stems` (comas, coincidencia exacta y sensible a mayúsculas, espacios recortados, duplicados colapsados conservando el orden; un nombre desconocido da `UnsupportedStemError`, código 3, y no un error de Typer, y esa comprobación se hace en la CLI antes de VALIDATING, sin notificar ninguna etapa, así que `separate noexiste.wav -s drums` da el error de stem y no el de entrada); el progreso es una etapa a la vez, sin las barras del motor, y avisa de la descarga de pesos cuando el modelo no está en el directorio de modelos. §3, punto 1: el adaptador fija `TQDM_DISABLE=1` antes de importar `audio_separator` y filtra el aviso "Using soundfile for writing." en `audio_separator.separator.separator`. Motivo: con un indicador de Rich activo, las barras de tqdm se apilan una línea por actualización (observado en T-12), y el silencio tiene que heredarlo cualquier consumidor del servicio, no solo la CLI. El filtro depende del texto en inglés de 0.47.0. Aprobado por el usuario.
