"""Media ports (interfaces.md §3). Implementations live in media/ffmpeg.py and media/system.py."""

from pathlib import Path
from typing import Protocol

from stemseparator.domain.models import MediaInfo, OutputFormat


class MediaProbe(Protocol):
    def probe(self, path: Path) -> MediaInfo:
        """Describe the media file; MediaReadError if ffprobe cannot read it."""
        ...


class AudioNormalizer(Protocol):
    def normalize(self, src_path: Path, dest: Path) -> Path:
        """First audio stream → WAV pcm_f32le, 44 100 Hz, 2 channels; NoAudioStreamError."""
        ...


class StemExporter(Protocol):
    def peak(self, src_wav: Path) -> float:
        """Absolute linear sample peak (1.0 = 0 dBFS; may exceed 1.0 in float)."""
        ...

    def export(self, src_wav: Path, dest: Path, fmt: OutputFormat, gain: float) -> Path:
        """Multiply by gain and write dest in fmt at 44 100 Hz, 2 channels."""
        ...


class SystemChecks(Protocol):
    def require_ffmpeg(self) -> None:
        """FFmpegNotFoundError, with per-OS install instructions, if ffmpeg/ffprobe are missing."""
        ...

    def free_bytes(self, path: Path) -> int:
        """Free bytes on the volume that holds path."""
        ...
