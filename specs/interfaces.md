# Interfaces y contratos

Estado: Aprobado v0.4 (2026-09-25)

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

## 2. Modelos de datos (`domain/models.py`)

```python
from enum import StrEnum
from pathlib import Path
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
    engine_labels: dict[str, StemName]       # etiqueta del motor → StemName
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

class AudioNormalizer(Protocol):
    def normalize(self, src_path: Path, dest: Path) -> Path: ...
        # Se aplica a TODA entrada (audio o video): primera pista de audio →
        # WAV pcm_f32le, 44 100 Hz, 2 canales. NoAudioStreamError si no hay audio.

class StemExporter(Protocol):
    def peak(self, src_wav: Path) -> float: ...
        # Pico absoluto lineal de las muestras (1.0 = 0 dBFS; puede superar 1.0 en float).
    def export(self, src_wav: Path, dest: Path, fmt: OutputFormat, gain: float) -> Path: ...
        # Multiplica por gain; 44 100 Hz, 2 canales; wav=pcm_s24le, flac=24 bits, mp3=320k CBR.

class SystemChecks(Protocol):
    def require_ffmpeg(self) -> None: ...    # FFmpegNotFoundError con instrucciones por SO
    def free_bytes(self, path: Path) -> int: ...

# media/system.py
class LocalSystemChecks: ...                 # implementa SystemChecks
def default_model_dir() -> Path: ...         # STEMSEPARATOR_MODEL_DIR o platformdirs; solo la usa cli

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

# domain/models.py
OUTPUT_PEAK_CEILING: float = 0.999            # techo de pico de los archivos exportados
```

### `AudioSeparatorEngine` (`engines/audio_separator_engine.py`)

Contrato con `audio-separator` 0.47.0, confirmado en el spike T-01 (ADR-002):

```python
class AudioSeparatorEngine:                  # implementa SeparationEngine
    def separate(...) -> dict[StemName, Path]: ...
    def inference_device(self) -> str: ...   # "cpu" | "mps" | "cuda"; lo usa doctor
```

1. Antes de importar `onnxruntime` y `audio_separator` (import perezoso): `os.environ["ORT_DISABLE_TELEMETRY"] = "1"`, y después `onnxruntime.disable_telemetry_events()` (RNF-12). La variable de entorno es la que funciona: en T-01, la llamada a la API sola no evitó las conexiones a `mobile.events.data.microsoft.com` durante `separate()`.
2. **Margen de entrada**: el adaptador escribe `work_dir/engine_input.wav` = `audio_path` × `ENGINE_INPUT_HEADROOM` (0,5, es decir −6 dB; float32) y es eso lo que pasa al motor. Las muestras se leen y escriben con `soundfile` y `numpy`, importados de forma perezosa en el adaptador; son dependencias declaradas de `audio-separator` (ver `architecture.md` §4). Motivo: `audio-separator` reescala por separado cualquier stem cuyo pico pase de `normalization_threshold`, y ese umbral no admite valores mayores que 1,0 (T-01). Sin margen, el instrumental real (pico de 1,15 a 1,22 medido) se reescalaría y la suma de los stems dejaría de reconstruir la mezcla.
3. **LOADING_MODEL** = construir `Separator(...)` + `load_model(model_filename=model.engine_filename)`. `load_model()` descarga los pesos si faltan. Parámetros del constructor:
   `model_file_dir=model_dir`, `output_dir=work_dir`, `output_format="WAV"`, `sample_rate=44100`, `use_soundfile=True` (no cuantiza a 16 bits), `normalization_threshold=1.0` (el máximo que admite; con el margen del paso 2, ningún stem debería llegar a él), `log_level` según `--verbose`.
4. Dispositivo: `audio-separator` no tiene un parámetro para fijarlo; lo detecta solo (CUDA si `torch` la ve, MPS en Apple Silicon, si no CPU). El adaptador no toca sus atributos internos (ADR-001). Tras construir `Separator`, registra en el log el dispositivo elegido y lanza `EngineError` si es CUDA (RNF-05).
5. **SEPARATING** = `separate(str(work_dir / "engine_input.wav"), custom_output_names={etiqueta: stem.value for etiqueta, stem in model.engine_labels.items()})`. Así el motor escribe `work_dir/vocals.wav` y `work_dir/instrumental.wav` y el adaptador no interpreta nombres de archivo. Las claves de `custom_output_names` no distinguen mayúsculas.
6. Si falta alguna salida de `model.stems` → `EngineError`.
7. **Guardián**: si el pico de algún stem es ≥ `ENGINE_RESCALE_GUARD` (0,9999), el motor lo reescaló → `EngineError`. No se compara con 1,0 exacto porque esa igualdad es frágil en float tras conversiones de formato.
8. Deshace el margen: multiplica cada stem por 1 / `ENGINE_INPUT_HEADROOM` y lo reescribe **en su sitio** en float32 (puede superar 1,0 sin saturar). Así, los stems que recibe el servicio están al nivel del audio de trabajo y su suma lo reconstruye.

## 4. Errores y códigos de salida (`domain/errors.py`)

Todas las excepciones heredan de `StemSeparatorError(message: str, hint: str)`. La CLI muestra `message` y `hint` en español.

| Excepción | Cuándo | Código de salida |
|-----------|--------|------------------|
| — | Éxito | 0 |
| (inesperada) | Cualquier excepción no prevista | 1 |
| (Typer) | Error de uso / argumentos | 2 |
| `InputNotFoundError` | La entrada no existe o no es legible | 3 |
| `UnsupportedFormatError` | Extensión no admitida | 3 |
| `NoAudioStreamError` | El archivo no tiene pista de audio | 3 |
| `MediaReadError` | ffprobe no puede leer el archivo | 3 |
| `UnknownModelError` | Id no está en el catálogo | 3 |
| `UnsupportedStemError` | Stem pedido que el modelo no produce | 3 |
| `OutputConflictError` | Salidas existentes sin `--overwrite` | 3 |
| `OutputNotWritableError` | Directorio de salida no escribible | 3 |
| `FileTooLargeError` | El archivo subido supera el límite de tamaño (500 MB por defecto). Solo lo lanza el backend web (`i8-api.md`); la CLI no tiene límite | 3 |
| `FFmpegNotFoundError` | Falta ffmpeg o ffprobe | 4 |
| `InsufficientDiskSpaceError` | Espacio insuficiente | 4 |
| `EngineError` | Falla la descarga, carga o ejecución del modelo, o el motor eligió CUDA (RNF-05) | 5 |
| `ExportError` | Falla la conversión de un stem o su duración se sale de la tolerancia | 5 |
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
