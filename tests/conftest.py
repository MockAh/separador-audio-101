"""Shared test fixtures: synthetic audio/video generated with FFmpeg `lavfi`.

The media fixtures skip the test when FFmpeg/FFprobe are not installed. FakeEngine is the
SeparationEngine double of the service tests (T-09).
"""

import contextlib
import errno
import json
import os
import shutil
import signal
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from stemseparator.domain.errors import EngineError
from stemseparator.domain.models import ModelSpec, StemName
from stemseparator.domain.progress import JobStage, ProgressReporter


@pytest.fixture(autouse=True)
def _sigint_handler_restored() -> Iterator[None]:
    """Every test must leave the SIGINT handler it found (ADR-004 critical section).

    A leaked handler would silently change Ctrl+C for every later test, with the symptom
    showing up in another file. The handler is put back before failing, so the leak stops
    here and the failure points at the test that caused it.
    """
    before = signal.getsignal(signal.SIGINT)
    yield
    after = signal.getsignal(signal.SIGINT)
    if after is not before:
        signal.signal(signal.SIGINT, before)
        pytest.fail(f"test leaked a SIGINT handler: {after!r} (was {before!r})")


@contextlib.contextmanager
def sigint_must_be_deferred() -> Iterator[None]:
    """Turn an escaped KeyboardInterrupt into a test failure.

    pytest treats a KeyboardInterrupt as "stop the whole session", so a publication that
    fails to defer SIGINT would abort every later test instead of failing this one.
    """
    try:
        yield
    except KeyboardInterrupt as exc:
        pytest.fail(f"SIGINT was not deferred: KeyboardInterrupt escaped ({exc!r})")


_FFMPEG = ("ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y")
_TONE = "sine=frequency=440:sample_rate=44100:duration=2"
_VIDEO = "testsrc=duration=2:size=160x120:rate=25"


def _run_ffmpeg(*args: str) -> None:
    subprocess.run([*_FFMPEG, *args], check=True, capture_output=True, text=True)


def make_stereo_wav(dest: Path) -> Path:
    """Stereo 44 100 Hz WAV tone."""
    _run_ffmpeg("-f", "lavfi", "-i", _TONE, "-ac", "2", str(dest))
    return dest


def make_float_wav(
    dest: Path, *, amplitude: float, sample_rate: int = 44100, channels: int = 2
) -> Path:
    """pcm_f32le WAV sine of exactly `amplitude` peak (may exceed 1.0), 441 Hz, 2 s."""
    wave = f"{amplitude}*sin(2*PI*441*t)"
    expr = "|".join([wave] * channels)
    _run_ffmpeg(
        "-f", "lavfi", "-i", f"aevalsrc={expr}:s={sample_rate}:d=2",
        "-c:a", "pcm_f32le", str(dest),
    )  # fmt: skip
    return dest


def make_video_mp4(dest: Path) -> Path:
    """MP4 with a video stream first and a mono 48 kHz AAC audio stream second."""
    _run_ffmpeg(
        "-f", "lavfi", "-i", _VIDEO,
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        str(dest),
    )  # fmt: skip
    return dest


def make_mute_mkv(dest: Path) -> Path:
    """MKV with a video stream and no audio stream."""
    _run_ffmpeg(
        "-f", "lavfi", "-i", _VIDEO,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an",
        str(dest),
    )  # fmt: skip
    return dest


def make_cover_mp3(dest: Path) -> Path:
    """Stereo MP3 with an embedded cover (one-frame video stream with attached_pic)."""
    _run_ffmpeg(
        "-f", "lavfi", "-i", _TONE,
        "-f", "lavfi", "-i", "color=c=red:size=64x64:rate=1:duration=1",
        "-map", "0:a", "-map", "1:v", "-ac", "2", "-c:a", "libmp3lame", "-b:a", "128k",
        "-c:v", "mjpeg", "-disposition:v:0", "attached_pic", "-id3v2_version", "3",
        str(dest),
    )  # fmt: skip
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(dest)],
        check=True,
        capture_output=True,
        text=True,
    )
    video = [s for s in json.loads(probe.stdout)["streams"] if s["codec_type"] == "video"]
    if len(video) != 1 or video[0]["disposition"]["attached_pic"] != 1:
        raise RuntimeError(f"cover fixture is not an attached_pic stream: {video}")
    return dest


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("FFmpeg/FFprobe not installed")
    return tmp_path_factory.mktemp("media")


@pytest.fixture(scope="session")
def stereo_wav(media_dir: Path) -> Path:
    return make_stereo_wav(media_dir / "tone.wav")


@pytest.fixture(scope="session")
def video_mp4(media_dir: Path) -> Path:
    return make_video_mp4(media_dir / "video.mp4")


@pytest.fixture(scope="session")
def mute_mkv(media_dir: Path) -> Path:
    return make_mute_mkv(media_dir / "mudo.mkv")


@pytest.fixture(scope="session")
def cover_mp3(media_dir: Path) -> Path:
    return make_cover_mp3(media_dir / "cover.mp3")


@pytest.fixture(scope="session")
def hot_float_wav(media_dir: Path) -> Path:
    """Float WAV with a 1.2 peak, above 0 dBFS like the engine stems of ADR-002."""
    return make_float_wav(media_dir / "hot.wav", amplitude=1.2)


@pytest.fixture(scope="session")
def half_float_wav(media_dir: Path) -> Path:
    return make_float_wav(media_dir / "half.wav", amplitude=0.5)


@pytest.fixture(scope="session")
def mono_wav(media_dir: Path) -> Path:
    return make_float_wav(media_dir / "mono.wav", amplitude=0.5, channels=1)


@pytest.fixture(scope="session")
def wav_48k(media_dir: Path) -> Path:
    return make_float_wav(media_dir / "tone48k.wav", amplitude=0.5, sample_rate=48000)


class FakeEngine:
    """SeparationEngine double: no model, no network, no FFmpeg.

    Like the real engine (interfaces.md §3), it notifies LOADING_MODEL and SEPARATING
    itself and reports its own failure. It writes one placeholder `<stem>.wav` per stem of
    the model inside work_dir, which must already exist. Every call is appended to the
    shared call log `calls`, the same one the other doubles of a test use. `during`
    runs inside SEPARATING, before the outputs are written (e.g. to raise SIGINT).
    """

    def __init__(self, calls: list[tuple[Any, ...]]) -> None:
        self.calls = calls
        self.fail_at: JobStage | None = None  # LOADING_MODEL or SEPARATING
        self.error = EngineError("El motor de separación falló.", "Vuelve a intentarlo.")
        self.during: Callable[[], None] | None = None

    def separate(
        self,
        audio_path: Path,
        model: ModelSpec,
        work_dir: Path,
        model_dir: Path,
        reporter: ProgressReporter | None = None,
    ) -> dict[StemName, Path]:
        self.calls.append(("separate", audio_path, model, work_dir, model_dir, reporter))
        outputs: dict[StemName, Path] = {}
        for stage in (JobStage.LOADING_MODEL, JobStage.SEPARATING):
            if reporter is not None:
                reporter.stage_started(stage)
            try:
                if self.fail_at is stage:
                    raise self.error
                if stage is JobStage.SEPARATING:
                    if self.during is not None:
                        self.during()
                    for stem in model.stems:
                        outputs[stem] = work_dir / f"{stem.value}.wav"
                        outputs[stem].write_bytes(b"engine stem placeholder")
            except BaseException as exc:  # Ctrl+C included, like the real engine
                if reporter is not None:
                    reporter.stage_failed(stage, exc)
                raise
            if reporter is not None:
                reporter.stage_finished(stage, 0.0)
        return outputs


TMP_PREFIX = ".stemseparator-tmp-"  # ADR-004: every auxiliary file in the output directory

FsAction = str | Callable[[], None]  # "fail", "partial" (copy only) or a hook run first


class FsFaults:
    """Fault injection on the file primitives of the atomic publication (ADR-004).

    Patches os.replace, shutil.copyfile and os.unlink (Path.unlink goes through it). Each
    call is classified and counted per kind, 1-based:
      backup   os.replace(dest, <TMP_PREFIX>...bak)
      to_dest  os.replace(file, dest), dest without the prefix
      restore  os.replace(<TMP_PREFIX>...bak, dest)
      tmp      os.replace(src, <TMP_PREFIX>...), not a .bak (not used by ADR-004)
      copy     shutil.copyfile(src, <output>/<TMP_PREFIX>...), the cross-volume copy
      unlink / unlink_tmp   os.unlink of a path without / with the prefix
    `faults[(kind, n)]` makes the n-th call of that kind fail ("fail"), leave a partial
    destination and fail ("partial"), or run a hook before going on (a callable).
    With `cross_volume`, os.replace between different directories raises EXDEV, like the
    system temp dir and the output dir on different volumes. Calls with dir_fd (rmtree of
    a TemporaryDirectory) pass through unrecorded.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.cross_volume = False
        self.faults: dict[tuple[str, int], FsAction] = {}
        self.log: list[tuple[str, Path, Path | None]] = []
        self._real_replace = os.replace
        self._real_copyfile = shutil.copyfile
        self._real_unlink = os.unlink
        monkeypatch.setattr(os, "replace", self._replace)
        monkeypatch.setattr(shutil, "copyfile", self._copyfile)
        monkeypatch.setattr(os, "unlink", self._unlink)

    def count(self, kind: str) -> int:
        return sum(1 for entry in self.log if entry[0] == kind)

    def of(self, kind: str) -> list[tuple[str, Path, Path | None]]:
        return [entry for entry in self.log if entry[0] == kind]

    def _record(self, kind: str, src: Path, dst: Path | None) -> None:
        self.log.append((kind, src, dst))
        action = self.faults.get((kind, self.count(kind)))
        if action is None:
            return
        if callable(action):
            action()
        elif action == "partial" and dst is not None:
            dst.write_bytes(b"partial copy")
            raise OSError(errno.ENOSPC, f"injected partial {kind}", str(dst))
        else:
            raise OSError(errno.EIO, f"injected {kind} failure", str(src))

    def _replace(self, src: Any, dst: Any, **kwargs: Any) -> None:
        if kwargs:
            self._real_replace(src, dst, **kwargs)
            return
        src_path, dst_path = Path(src), Path(dst)
        if src_path.name.startswith(TMP_PREFIX) and src_path.name.endswith(".bak"):
            kind = "restore"
        elif dst_path.name.startswith(TMP_PREFIX):
            kind = "backup" if dst_path.name.endswith(".bak") else "tmp"
        else:
            kind = "to_dest"
        self._record(kind, src_path, dst_path)
        if self.cross_volume and src_path.parent != dst_path.parent:
            raise OSError(errno.EXDEV, "Invalid cross-device link", str(src_path))
        self._real_replace(src, dst)

    def _copyfile(self, src: Any, dst: Any, **kwargs: Any) -> Any:
        self._record("copy", Path(src), Path(dst))
        return self._real_copyfile(src, dst, **kwargs)

    def _unlink(self, path: Any, **kwargs: Any) -> None:
        if kwargs:
            self._real_unlink(path, **kwargs)
            return
        target = Path(path)
        kind = "unlink_tmp" if target.name.startswith(TMP_PREFIX) else "unlink"
        self._record(kind, target, None)
        self._real_unlink(path)


@pytest.fixture
def fs_faults(monkeypatch: pytest.MonkeyPatch) -> FsFaults:
    return FsFaults(monkeypatch)
