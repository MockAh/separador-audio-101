"""Local system checks: FFmpeg binaries and versions, free disk space and the model cache path."""

import os
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path

import platformdirs

from stemseparator.domain.errors import FFmpegNotFoundError

MODEL_DIR_ENV = "STEMSEPARATOR_MODEL_DIR"

_REQUIRED_BINARIES = ("ffmpeg", "ffprobe")
_VERSION_TIMEOUT_S = 10
UNKNOWN_VERSION = "desconocida"

# Fixed order Windows, macOS, Linux on every OS (interfaces.md §3, CA-04). Public: media/ffmpeg.py
# uses the same text when a binary turns out to be missing (decision of 2026-10-03, T-12).
INSTALL_HINT = "\n".join(
    (
        "Instala FFmpeg (incluye ffmpeg y ffprobe):",
        "  Windows: winget install Gyan.FFmpeg",
        "  macOS: brew install ffmpeg",
        "  Linux (Debian/Ubuntu): sudo apt install ffmpeg",
        "Después, abre una terminal nueva y comprueba con `ffmpeg -version`.",
    )
)


class LocalSystemChecks:
    """SystemChecks implementation backed by the PATH and the local file system."""

    def require_ffmpeg(self) -> None:
        """Raise FFmpegNotFoundError naming whichever of ffmpeg/ffprobe is not on the PATH."""
        missing = [name for name in _REQUIRED_BINARIES if shutil.which(name) is None]
        if not missing:
            return
        if len(missing) == 1:
            message = f"No se encontró {missing[0]} en el PATH."
        else:
            message = f"No se encontraron {' ni '.join(missing)} en el PATH."
        raise FFmpegNotFoundError(message, INSTALL_HINT)

    def ffmpeg_versions(self) -> Mapping[str, str | None]:
        """Version of each binary: None if not on the PATH, UNKNOWN_VERSION if unreadable."""
        return {name: _binary_version(name) for name in _REQUIRED_BINARIES}

    def free_bytes(self, path: Path) -> int:
        """Free bytes on the volume of path, or of its nearest existing ancestor."""
        target = path.absolute()
        while not target.exists() and target != target.parent:
            target = target.parent
        return shutil.disk_usage(target).free


def _binary_version(name: str) -> str | None:
    """The token after "version" on the first line of `<name> -version`; never raises."""
    path = shutil.which(name)
    if path is None:
        return None
    try:
        completed = subprocess.run(
            [path, "-version"],
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            timeout=_VERSION_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN_VERSION
    if completed.returncode != 0:
        return UNKNOWN_VERSION
    first_line = completed.stdout.splitlines()[0] if completed.stdout else ""
    tokens = first_line.split()
    if "version" not in tokens[:-1]:
        return UNKNOWN_VERSION
    return tokens[tokens.index("version") + 1]


def default_model_dir() -> Path:
    """Model cache path: STEMSEPARATOR_MODEL_DIR if set and not blank, else the OS cache.

    Only computes the path; the directory is created by whoever downloads the weights.
    """
    configured = os.environ.get(MODEL_DIR_ENV, "")
    if configured.strip():
        return Path(configured).expanduser().resolve()
    return platformdirs.user_cache_path(appname="stemseparator", appauthor=False) / "models"
