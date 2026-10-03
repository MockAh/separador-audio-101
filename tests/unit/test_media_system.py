"""Local system checks without real FFmpeg, network or disk beyond tmp_path (T-07, RF-09, RF-12).

PATH is simulated with a directory of stub executables, and the environment with
monkeypatch. platformdirs and shutil.disk_usage are replaced where the real value
depends on this machine.
"""

import inspect
import platform
import re
import shutil
import sys
from pathlib import Path
from typing import Any, NamedTuple

import platformdirs
import pytest

from stemseparator.domain.errors import FFmpegNotFoundError
from stemseparator.media.base import SystemChecks
from stemseparator.media.system import LocalSystemChecks, default_model_dir

# Literal values from interfaces.md §3, in the fixed order Windows, macOS, Linux.
INSTALL_LINES = (
    "Windows: winget install Gyan.FFmpeg",
    "macOS: brew install ffmpeg",
    "Linux (Debian/Ubuntu): sudo apt install ffmpeg",
)
FINAL_LINE = "abre una terminal nueva y comprueba con `ffmpeg -version`"

ENV_VAR = "STEMSEPARATOR_MODEL_DIR"


def _fake_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *present: str) -> None:
    """Point PATH at a directory that holds only the given executables.

    Each stub is created with and without `.exe`, and PATHEXT is pinned, so shutil.which
    finds it on POSIX and on Windows alike.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in present:
        for file_name in (name, f"{name}.exe"):
            stub = bin_dir / file_name
            stub.write_text("")
            stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("PATHEXT", ".EXE")


def _named_binaries(message: str) -> set[str]:
    """Lowercase binary names the message mentions ("FFmpeg" as a product name is not one)."""
    return set(re.findall(r"\b(ffmpeg|ffprobe)\b", message))


def _assert_full_hint(hint: str) -> None:
    positions = [hint.find(line) for line in INSTALL_LINES]
    assert all(pos >= 0 for pos in positions), hint
    assert positions == sorted(positions), "order must be Windows, macOS, Linux"
    last_line = hint.strip().splitlines()[-1]
    assert FINAL_LINE in last_line


def test_local_system_checks_implements_protocol() -> None:
    checks: SystemChecks = LocalSystemChecks()
    assert checks is not None


# --- require_ffmpeg() --------------------------------------------------------------------


def test_require_ffmpeg_passes_with_both_binaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_path(tmp_path, monkeypatch, "ffmpeg", "ffprobe")

    LocalSystemChecks().require_ffmpeg()


@pytest.mark.parametrize(
    ("present", "missing"),
    [
        (("ffprobe",), {"ffmpeg"}),
        (("ffmpeg",), {"ffprobe"}),
        ((), {"ffmpeg", "ffprobe"}),
    ],
    ids=["missing-ffmpeg", "missing-ffprobe", "missing-both"],
)
def test_require_ffmpeg_names_missing_binary_and_lists_all_systems(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    present: tuple[str, ...],
    missing: set[str],
) -> None:
    _fake_path(tmp_path, monkeypatch, *present)

    with pytest.raises(FFmpegNotFoundError) as excinfo:
        LocalSystemChecks().require_ffmpeg()

    error = excinfo.value
    assert error.exit_code == 4
    assert _named_binaries(error.message) == missing
    _assert_full_hint(error.hint)


@pytest.mark.parametrize(
    ("sys_platform", "system_name"),
    [("win32", "Windows"), ("darwin", "Darwin"), ("linux", "Linux")],
)
def test_require_ffmpeg_hint_is_the_same_on_every_os(
    monkeypatch: pytest.MonkeyPatch, sys_platform: str, system_name: str
) -> None:
    # PATH="" makes shutil.which return None before any platform-specific branch, so the
    # simulated OS only reaches the code that builds the error. The expected values are the
    # module constants above: nothing here depends on the OS the test runs on.
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(sys, "platform", sys_platform)
    monkeypatch.setattr(platform, "system", lambda: system_name)

    with pytest.raises(FFmpegNotFoundError) as excinfo:
        LocalSystemChecks().require_ffmpeg()

    _assert_full_hint(excinfo.value.hint)


# --- free_bytes() ------------------------------------------------------------------------


class _Usage(NamedTuple):
    total: int
    used: int
    free: int


def test_free_bytes_on_existing_directory_is_positive(tmp_path: Path) -> None:
    free = LocalSystemChecks().free_bytes(tmp_path)

    assert isinstance(free, int)
    assert free > 0


def test_free_bytes_on_missing_path_measures_nearest_existing_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    measured: list[Path] = []

    def fake_disk_usage(path: Any) -> _Usage:
        # Like the real one: a missing path raises; a fixed value keeps the comparison exact.
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(path)
        measured.append(p)
        return _Usage(total=200_000, used=76_544, free=123_456)

    monkeypatch.setattr(shutil, "disk_usage", fake_disk_usage)
    missing = tmp_path / "a" / "b" / "c"
    checks = LocalSystemChecks()

    assert checks.free_bytes(missing) == checks.free_bytes(tmp_path) == 123_456
    assert set(measured) == {tmp_path}
    assert not missing.exists()


# --- default_model_dir() -----------------------------------------------------------------


@pytest.fixture
def fake_platformdirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace platformdirs.user_cache_path; record its arguments, bound by name."""
    calls: list[dict[str, Any]] = []
    signature = inspect.signature(platformdirs.user_cache_path)

    def fake_user_cache_path(*args: Any, **kwargs: Any) -> Path:
        calls.append(dict(signature.bind(*args, **kwargs).arguments))
        return tmp_path / "os-cache" / "stemseparator"

    monkeypatch.setattr(platformdirs, "user_cache_path", fake_user_cache_path)
    return calls


def _assert_nothing_created(tmp_path: Path) -> None:
    assert list(tmp_path.iterdir()) == []


def test_default_model_dir_uses_absolute_env_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_platformdirs: list[dict[str, Any]]
) -> None:
    target = tmp_path / "models"
    monkeypatch.setenv(ENV_VAR, str(target))

    assert default_model_dir() == target.resolve()
    assert fake_platformdirs == []
    _assert_nothing_created(tmp_path)


def test_default_model_dir_expands_user_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_platformdirs: list[dict[str, Any]]
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv(ENV_VAR, "~/algo")

    result = default_model_dir()

    assert result == (home / "algo").resolve()
    assert result.is_absolute()
    _assert_nothing_created(tmp_path)


def test_default_model_dir_makes_relative_env_path_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_platformdirs: list[dict[str, Any]]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(ENV_VAR, str(Path("rel") / "models"))

    result = default_model_dir()

    assert result.is_absolute()
    assert result == (tmp_path / "rel" / "models").resolve()
    _assert_nothing_created(tmp_path)


@pytest.mark.parametrize("value", ["", "   ", "\t \n"], ids=["empty", "spaces", "whitespace"])
def test_default_model_dir_ignores_blank_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_platformdirs: list[dict[str, Any]],
    value: str,
) -> None:
    monkeypatch.setenv(ENV_VAR, value)

    assert default_model_dir() == tmp_path / "os-cache" / "stemseparator" / "models"
    assert len(fake_platformdirs) == 1
    _assert_nothing_created(tmp_path)


def test_default_model_dir_falls_back_to_platformdirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_platformdirs: list[dict[str, Any]]
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)

    result = default_model_dir()

    assert result == tmp_path / "os-cache" / "stemseparator" / "models"
    assert result.name == "models"
    assert len(fake_platformdirs) == 1
    call = fake_platformdirs[0]
    assert call["appname"] == "stemseparator"
    assert call["appauthor"] is False
    _assert_nothing_created(tmp_path)
