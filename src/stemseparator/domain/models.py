"""Domain data models and constants (interfaces.md §2)."""

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
    kind: MediaKind  # VIDEO only if a non-cover video stream exists (attached_pic == 0)
    duration_s: float
    has_audio: bool
    sample_rate: int | None  # of the first audio stream
    channels: int | None
    codec: str | None  # codec of the first audio stream


class ModelSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    engine_filename: str  # name in audio-separator
    stems: tuple[StemName, ...]
    engine_labels: Mapping[str, StemName]  # engine label -> StemName
    cpu_rtf: float  # CPU real-time factor (processing s per audio s)
    cpu_reference: str  # machine where cpu_rtf was measured (ADR-002)
    weights_license: str
    download_size_bytes: int  # bytes added by downloading the weights (ADR-002)
    description: str  # Spanish, shown by `models`
    is_default: bool = False


class SeparationRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_path: Path
    output_dir: Path
    model_id: str | None = None  # None -> default model
    stems: frozenset[StemName] | None = None  # None -> all stems of the model
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
    peak: float  # linear peak of the exported stem: source peak x output_gain


class SeparationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    input: MediaInfo
    work_duration_s: float  # working audio, probed after normalizing; stem duration reference
    model_id: str
    stems: tuple[StemFile, ...]
    output_gain: float  # common gain (0 < g <= 1) applied to every stem on export
    elapsed_s: float
    # SIGINT arrived during a publication that finished well (CA-19, ADR-004): the job is
    # complete, exit code 0, and the CLI warns explicitly.
    interrupted_during_publish: bool = False


OUTPUT_PEAK_CEILING: float = 0.999  # peak ceiling of exported files

# Duration tolerance from acceptance.md §1: |stem duration - work_duration_s| <= value.
DURATION_TOLERANCE_S: Mapping[OutputFormat, float] = MappingProxyType(
    {OutputFormat.WAV: 0.050, OutputFormat.FLAC: 0.050, OutputFormat.MP3: 0.100}
)
