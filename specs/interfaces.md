# Interfaces y contratos

Estado: Borrador v0.1 — pendiente de aprobación

Las firmas son contractuales: cambiarlas requiere actualizar este documento. Los cuerpos de ejemplo no lo son.

## 1. CLI

```
stemseparator separate INPUT [OPCIONES]
  INPUT                      Ruta al archivo de audio o video.
  -o, --output DIR           Directorio de salida. Por defecto: directorio actual.
  -m, --model ID             Identificador del catálogo. Por defecto: el modelo predeterminado.
  -s, --stems LISTA          Stems separados por comas. Por defecto: todos los del modelo.
  -f, --format [wav|flac|mp3]  Por defecto: wav.
      --overwrite            Sobrescribir archivos existentes.
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
    kind: MediaKind
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
    model_id: str
    stems: tuple[StemFile, ...]
    elapsed_s: float
```

## 3. Puertos y servicios

```python
# engines/base.py
class SeparationEngine(Protocol):
    def separate(self, audio_path: Path, model: ModelSpec, work_dir: Path,
                 model_dir: Path) -> dict[StemName, Path]:
        """Separa audio_path con el modelo. Escribe solo dentro de work_dir.
        Devuelve una ruta WAV por cada stem de model.stems.
        Lanza EngineError si el motor falla o si falta alguna salida esperada."""

# media/base.py
class MediaProbe(Protocol):
    def probe(self, path: Path) -> MediaInfo: ...
        # Lanza MediaReadError si ffprobe no puede leer el archivo.

class AudioExtractor(Protocol):
    def extract(self, video_path: Path, dest: Path) -> Path: ...
        # WAV PCM, sample rate original, 2 canales. NoAudioStreamError si no hay audio.

class StemExporter(Protocol):
    def export(self, src_wav: Path, dest: Path, fmt: OutputFormat) -> Path: ...
        # 44 100 Hz, 2 canales; wav=pcm_s24le, flac=24 bits, mp3=320k CBR.

# media/system.py
def require_ffmpeg() -> None: ...            # FFmpegNotFoundError con instrucciones por SO
def free_bytes(path: Path) -> int: ...
def default_model_dir() -> Path: ...         # STEMSEPARATOR_MODEL_DIR o platformdirs

# domain/catalog.py
def list_models() -> tuple[ModelSpec, ...]: ...
def get_model(model_id: str | None) -> ModelSpec: ...  # UnknownModelError

# application/progress.py
class JobStage(StrEnum):
    VALIDATING = "validating"; PREPARING = "preparing"; LOADING_MODEL = "loading_model"
    SEPARATING = "separating"; EXPORTING = "exporting"; DONE = "done"

class ProgressReporter(Protocol):
    def stage_started(self, stage: JobStage) -> None: ...
    def stage_finished(self, stage: JobStage, elapsed_s: float) -> None: ...

# application/service.py
class SeparationService:
    def __init__(self, engine: SeparationEngine, probe: MediaProbe,
                 extractor: AudioExtractor, exporter: StemExporter,
                 model_dir: Path) -> None: ...
    def run(self, request: SeparationRequest,
            reporter: ProgressReporter | None = None) -> SeparationResult: ...
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
| `ExportError` | Falla la conversión de un stem | 5 |
| (interrupción) | El usuario pulsa Ctrl+C; se limpia según ADR-004 | 130 |
