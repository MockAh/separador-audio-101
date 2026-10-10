"""FFmpeg/FFprobe adapters invoked through subprocess (ADR-003)."""

import json
import logging
import math
import subprocess
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

from stemseparator.domain.errors import (
    ExportError,
    FFmpegNotFoundError,
    MediaReadError,
    NoAudioStreamError,
)
from stemseparator.domain.models import MediaInfo, MediaKind, OutputFormat
from stemseparator.media.system import INSTALL_HINT

logger = logging.getLogger(__name__)

FFPROBE_TIMEOUT_S: float = 30

_UNREADABLE_HINT = (
    "Comprueba que el archivo no esté dañado y que sea un audio o video compatible con FFmpeg. "
    "Usa --verbose para ver el detalle de ffprobe."
)


def _unreadable(path: Path, reason: str) -> MediaReadError:
    return MediaReadError(f"No se pudo leer el archivo {path.name}: {reason}.", _UNREADABLE_HINT)


def _as_float(value: object) -> float | None:
    if not isinstance(value, str | int | float) or isinstance(value, bool):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _as_int(value: object) -> int | None:
    number = _as_float(value)
    return int(number) if number is not None else None


def parse_ffprobe_output(
    path: Path, stdout: str, *, returncode: int = 0, stderr: str = ""
) -> MediaInfo:
    """Build MediaInfo from `ffprobe -print_format json -show_format -show_streams` output."""
    if stderr:
        logger.debug("ffprobe stderr for %s:\n%s", path, stderr)
    if returncode != 0:
        raise _unreadable(path, f"ffprobe terminó con código {returncode}")
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise _unreadable(path, "la salida de ffprobe no es JSON válido") from exc
    if not isinstance(data, dict):
        raise _unreadable(path, "la salida de ffprobe no es JSON válido")

    raw_streams = data.get("streams", [])
    streams: list[dict[str, Any]] = (
        [s for s in raw_streams if isinstance(s, dict)] if isinstance(raw_streams, list) else []
    )
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    has_video = any(
        s.get("codec_type") == "video"
        and _as_int((s.get("disposition") or {}).get("attached_pic")) != 1
        for s in streams
    )

    raw_format = data.get("format")
    fmt: dict[str, Any] = raw_format if isinstance(raw_format, dict) else {}
    if "duration" in fmt:
        duration = _as_float(fmt["duration"])
    elif audio is not None:
        duration = _as_float(audio.get("duration"))
    else:
        duration = None
    if duration is None:
        raise _unreadable(path, "ffprobe no informa de una duración legible")

    codec = audio.get("codec_name") if audio is not None else None
    return MediaInfo(
        path=path,
        kind=MediaKind.VIDEO if has_video else MediaKind.AUDIO,
        duration_s=duration,
        has_audio=audio is not None,
        sample_rate=_as_int(audio.get("sample_rate")) if audio is not None else None,
        channels=_as_int(audio.get("channels")) if audio is not None else None,
        codec=codec if isinstance(codec, str) else None,
    )


class FFprobeMediaProbe:
    """MediaProbe implemented with the ffprobe binary."""

    def probe(self, path: Path) -> MediaInfo:
        args = [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ]  # fmt: skip
        try:
            completed = subprocess.run(
                args, capture_output=True, text=True, timeout=FFPROBE_TIMEOUT_S, check=False
            )
        except FileNotFoundError as exc:
            raise FFmpegNotFoundError("No se encontró ffprobe.", INSTALL_HINT) from exc
        except subprocess.TimeoutExpired as exc:
            logger.debug("ffprobe timed out after %s s on %s", FFPROBE_TIMEOUT_S, path)
            raise _unreadable(path, f"ffprobe no respondió en {FFPROBE_TIMEOUT_S:g} s") from exc
        return parse_ffprobe_output(
            path, completed.stdout, returncode=completed.returncode, stderr=completed.stderr
        )


# ffmpeg runs without a timeout: converting a long file can legitimately take a while.
_FFMPEG = ["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-v", "error"]
_OUTPUT_AUDIO = ["-ar", "44100", "-ac", "2"]
_CODEC_ARGS: Mapping[OutputFormat, tuple[str, ...]] = MappingProxyType(
    {
        OutputFormat.WAV: ("-c:a", "pcm_s24le"),
        # s32 makes the flac encoder write 24-bit samples (bits_per_raw_sample == 24).
        OutputFormat.FLAC: ("-c:a", "flac", "-sample_fmt", "s32"),
        OutputFormat.MP3: ("-c:a", "libmp3lame", "-b:a", "320k"),
    }
)
# astats on doubles never clips: Peak_level is 20*log10(max |sample|) and exceeds 0 dB
# for float samples above 1.0 (volumedetect, by contrast, works in s16 and saturates).
# With reset=0 each frame carries the running value, so the last line is the file peak.
_PEAK_KEY = "lavfi.astats.Overall.Peak_level"
_PEAK_FILTER = (
    f"aformat=sample_fmts=dbl,astats=metadata=1:reset=0,ametadata=mode=print:key={_PEAK_KEY}:file=-"
)
_NO_AUDIO_MARKER = "matches no streams"  # stderr of `-map 0:a:0` on a file without audio

_DECODE_HINT = (
    "Comprueba que el archivo no esté dañado ni incompleto y que sea un audio o video "
    "compatible con FFmpeg. Usa --verbose para ver el detalle de ffmpeg."
)
_EXPORT_HINT = (
    "Comprueba que haya espacio libre y permisos de escritura en el directorio temporal. "
    "Usa --verbose para ver el detalle de ffmpeg."
)


def normalize_args(src_path: Path, dest: Path) -> list[str]:
    """ffmpeg arguments: first audio stream of src_path → WAV pcm_f32le, 44 100 Hz, stereo."""
    return [
        *_FFMPEG, "-y", "-i", str(src_path),
        "-map", "0:a:0", "-c:a", "pcm_f32le", *_OUTPUT_AUDIO, str(dest),
    ]  # fmt: skip


def peak_args(src_wav: Path) -> list[str]:
    """ffmpeg arguments that print the running sample peak of src_wav to stdout."""
    return [*_FFMPEG, "-i", str(src_wav), "-map", "0:a:0", "-af", _PEAK_FILTER, "-f", "null", "-"]


def export_args(src_wav: Path, dest: Path, fmt: OutputFormat, gain: float) -> list[str]:
    """ffmpeg arguments: src_wav times gain → dest in fmt, 44 100 Hz, stereo."""
    return [
        *_FFMPEG, "-y", "-i", str(src_wav),
        "-map", "0:a:0", "-af", f"volume={gain!r}", *_OUTPUT_AUDIO,
        *_CODEC_ARGS[fmt], str(dest),
    ]  # fmt: skip


def _log_stderr(path: Path, stderr: str) -> None:
    if stderr:
        logger.debug("ffmpeg stderr for %s:\n%s", path, stderr)


def check_normalize_result(src_path: Path, *, returncode: int, stderr: str) -> None:
    """Map a failed normalization to NoAudioStreamError or MediaReadError."""
    _log_stderr(src_path, stderr)
    if returncode == 0:
        return
    if _NO_AUDIO_MARKER in stderr:
        raise NoAudioStreamError(
            f"El archivo {src_path.name} no contiene ninguna pista de audio.",
            "Elige un archivo de audio o un video que tenga sonido.",
        )
    raise MediaReadError(
        f"No se pudo decodificar el archivo {src_path.name}: "
        f"ffmpeg terminó con código {returncode}.",
        _DECODE_HINT,
    )


def _export_error(path: Path, reason: str) -> ExportError:
    return ExportError(f"No se pudo exportar el stem {path.name}: {reason}.", _EXPORT_HINT)


def parse_peak_output(
    src_wav: Path, stdout: str, *, returncode: int = 0, stderr: str = ""
) -> float:
    """Linear sample peak from the astats metadata printed by peak_args()."""
    _log_stderr(src_wav, stderr)
    if returncode != 0:
        raise _export_error(src_wav, f"ffmpeg terminó con código {returncode} al medir el pico")
    values = [
        line.partition("=")[2] for line in stdout.splitlines() if line.startswith(f"{_PEAK_KEY}=")
    ]
    if not values:
        raise _export_error(src_wav, "ffmpeg no informó del pico")
    try:
        peak_db = float(values[-1])
    except ValueError as exc:
        raise _export_error(src_wav, "ffmpeg informó de un pico ilegible") from exc
    peak = 10 ** (peak_db / 20)  # -inf dB (silence) → 0.0
    if not math.isfinite(peak):
        raise _export_error(src_wav, "ffmpeg informó de un pico ilegible")
    return peak


def check_export_result(src_wav: Path, dest: Path, *, returncode: int, stderr: str) -> None:
    """Map a failed export to ExportError."""
    _log_stderr(src_wav, stderr)
    if returncode != 0:
        raise _export_error(dest, f"ffmpeg terminó con código {returncode}")


def _run_ffmpeg(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise FFmpegNotFoundError("No se encontró ffmpeg.", INSTALL_HINT) from exc


class FFmpegAudioNormalizer:
    """AudioNormalizer implemented with the ffmpeg binary."""

    def normalize(self, src_path: Path, dest: Path) -> Path:
        completed = _run_ffmpeg(normalize_args(src_path, dest))
        check_normalize_result(src_path, returncode=completed.returncode, stderr=completed.stderr)
        return dest


class FFmpegStemExporter:
    """StemExporter implemented with the ffmpeg binary."""

    def peak(self, src_wav: Path) -> float:
        completed = _run_ffmpeg(peak_args(src_wav))
        return parse_peak_output(
            src_wav, completed.stdout, returncode=completed.returncode, stderr=completed.stderr
        )

    def export(self, src_wav: Path, dest: Path, fmt: OutputFormat, gain: float) -> Path:
        completed = _run_ffmpeg(export_args(src_wav, dest, fmt, gain))
        check_export_result(src_wav, dest, returncode=completed.returncode, stderr=completed.stderr)
        return dest
