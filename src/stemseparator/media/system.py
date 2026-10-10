"""Local system checks: FFmpeg binaries, free disk space and the model cache path."""

import os
import shutil
from pathlib import Path

import platformdirs

from stemseparator.domain.errors import FFmpegNotFoundError

MODEL_DIR_ENV = "STEMSEPARATOR_MODEL_DIR"

_REQUIRED_BINARIES = ("ffmpeg", "ffprobe")

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

    def free_bytes(self, path: Path) -> int:
        """Free bytes on the volume of path, or of its nearest existing ancestor."""
        target = path.absolute()
        while not target.exists() and target != target.parent:
            target = target.parent
        return shutil.disk_usage(target).free


def default_model_dir() -> Path:
    """Model cache path: STEMSEPARATOR_MODEL_DIR if set and not blank, else the OS cache.

    Only computes the path; the directory is created by whoever downloads the weights.
    """
    configured = os.environ.get(MODEL_DIR_ENV, "")
    if configured.strip():
        return Path(configured).expanduser().resolve()
    return platformdirs.user_cache_path(appname="stemseparator", appauthor=False) / "models"
