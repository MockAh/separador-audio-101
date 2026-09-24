# Interfaces y contratos

Estado: Aprobado v0.2 (2026-09-24)

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
stemseparator separate cancion.mp4 -o ./stems -m vocals-hq -s vocals,instrumental -f flac
→ ./stems/cancion_vocals.flac
→ ./stems/cancion_instrumental.flac
```

## 2. Modelos de datos (`domain/models.py`)

```python
from enum import StrEnum
from pathlib import Path
from typing import Literal
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
    id: str                                  # "vocals-hq"
    engine_filename: str                     # nombre en audio-separator
    stems: tuple[StemName, ...]
    engine_labels: dict[str, StemName]       # etiqueta del motor → StemName
    cpu_cost: Literal["low", "medium", "high"]
    weights_license: str
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

class SeparationResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    input: MediaInfo
    work_duration_s: float                   # audio de trabajo, sondeado tras normalizar;
                                             # referencia de duración de los stems
    model_id: str
    stems: tuple[StemFile, ...]
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
        Lanza EngineError si el motor falla o si falta alguna salida esperada."""
    # Nota para T-01: el corte entre LOADING_MODEL y SEPARATING asume que
    # audio-separator expone la carga de pesos y la inferencia como llamadas
    # separadas. Si el spike demuestra que no, el adaptador reportará ambas
    # como una sola etapa y se revisa este punto.

# media/base.py
class MediaProbe(Protocol):
    def probe(self, path: Path) -> MediaInfo: ...
        # Lanza MediaReadError si ffprobe no puede leer el archivo.

class AudioNormalizer(Protocol):
    def normalize(self, src_path: Path, dest: Path) -> Path: ...
        # Se aplica a TODA entrada (audio o video): primera pista de audio →
        # WAV PCM, sample rate original, 2 canales. NoAudioStreamError si no hay audio.

class StemExporter(Protocol):
    def export(self, src_wav: Path, dest: Path, fmt: OutputFormat) -> Path: ...
        # 44 100 Hz, 2 canales; wav=pcm_s24le, flac=24 bits, mp3=320k CBR.

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
    def run(self, request: SeparationRequest,
            reporter: ProgressReporter | None = None) -> SeparationResult: ...
        # El servicio notifica VALIDATING, PREPARING y EXPORTING (y el fallo de
        # esas etapas); LOADING_MODEL y SEPARATING las notifica el motor.
```

`AudioSeparatorEngine` configura `audio-separator` para CPU, con `output_dir=work_dir`, `model_file_dir=model_dir` y salida WAV, y traduce las etiquetas de sus archivos de salida mediante `ModelSpec.engine_labels`. Los parámetros exactos de su API se confirman en el spike T-01.

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
| `FFmpegNotFoundError` | Falta ffmpeg o ffprobe | 4 |
| `InsufficientDiskSpaceError` | Espacio insuficiente | 4 |
| `EngineError` | Falla la descarga, carga o ejecución del modelo | 5 |
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
