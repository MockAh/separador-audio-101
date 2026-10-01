"""FFmpeg/FFprobe adapters invoked through subprocess (ADR-003)."""

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from stemseparator.domain.errors import FFmpegNotFoundError, MediaReadError
from stemseparator.domain.models import MediaInfo, MediaKind

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
            raise FFmpegNotFoundError(
                "No se encontró ffprobe.",
                "Instala FFmpeg (incluye ffprobe) y comprueba que esté en el PATH. "
                "Ejecuta `stemseparator doctor` para más detalles.",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            logger.debug("ffprobe timed out after %s s on %s", FFPROBE_TIMEOUT_S, path)
            raise _unreadable(path, f"ffprobe no respondió en {FFPROBE_TIMEOUT_S:g} s") from exc
        return parse_ffprobe_output(
            path, completed.stdout, returncode=completed.returncode, stderr=completed.stderr
        )
