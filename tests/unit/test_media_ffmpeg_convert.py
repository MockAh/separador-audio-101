"""FFmpeg normalizer and stem exporter without FFmpeg (T-06, RF-02, RF-06, ADR-003).

No real subprocess: the argument builders and the result checks are pure functions, and
the classes are exercised with `subprocess.run` replaced by a recorder. The stdout and
stderr samples are real FFmpeg 6.1.1 output captured from the synthetic fixtures of
tests/conftest.py.
"""

import logging
import subprocess
from pathlib import Path
from typing import Any

import pytest

from stemseparator.domain.errors import (
    ExportError,
    FFmpegNotFoundError,
    MediaReadError,
    NoAudioStreamError,
    StemSeparatorError,
)
from stemseparator.domain.models import OutputFormat
from stemseparator.media import ffmpeg

SRC = Path("in") / "clip.mp4"
WAV = Path("work") / "vocals.wav"
DEST = Path("work") / "export" / "clip_vocals.wav"

_PREFIX = ["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-v", "error"]

NO_AUDIO_STDERR = (
    "Stream map '0:a:0' matches no streams.\n"
    "To ignore this, add a trailing '?' to the map.\n"
    "Failed to set value '0:a:0' for option 'map': Invalid argument\n"
    "Error parsing options for output file source.wav.\n"
    "Error opening output files: Invalid argument\n"
)
UNREADABLE_STDERR = (
    "[in#0 @ 0x595a6e77a0c0] Error opening input: Invalid data found when processing input\n"
    "Error opening input file junk.bin.\n"
    "Error opening input files: Invalid data found when processing input\n"
)
PEAK_STDOUT_HOT = (  # stereo sine with amplitude 1.2 (first and last frames)
    "frame:0    pts:0       pts_time:0\n"
    "lavfi.astats.Overall.Peak_level=1.583625\n"
    "frame:172  pts:88064   pts_time:1.99692\n"
    "lavfi.astats.Overall.Peak_level=1.583625\n"
)
PEAK_STDOUT_RAMP = (  # sine whose amplitude grows from 0 to 0.5: the running peak rises
    "frame:0    pts:0       pts_time:0\n"
    "lavfi.astats.Overall.Peak_level=-51.394989\n"
    "frame:1    pts:512     pts_time:0.01161\n"
    "lavfi.astats.Overall.Peak_level=-44.801221\n"
    "frame:171  pts:87552   pts_time:1.98531\n"
    "lavfi.astats.Overall.Peak_level=-6.037851\n"
    "frame:172  pts:88064   pts_time:1.99692\n"
    "lavfi.astats.Overall.Peak_level=-6.023062\n"
)
PEAK_STDOUT_SILENCE = (
    "frame:86   pts:44032   pts_time:0.998458\nlavfi.astats.Overall.Peak_level=-inf\n"
)


def _assert_user_error(err: StemSeparatorError, exit_code: int, path: Path) -> None:
    assert err.exit_code == exit_code
    assert path.name in err.message
    assert err.hint.strip()


# --- argument lists ----------------------------------------------------------------------


def test_normalize_args() -> None:
    assert ffmpeg.normalize_args(SRC, WAV) == [
        *_PREFIX, "-y", "-i", str(SRC),
        "-map", "0:a:0", "-c:a", "pcm_f32le", "-ar", "44100", "-ac", "2",
        str(WAV),
    ]  # fmt: skip


def test_peak_args_measure_in_double_precision_without_clipping() -> None:
    assert ffmpeg.peak_args(WAV) == [
        *_PREFIX, "-i", str(WAV), "-map", "0:a:0",
        "-af",
        "aformat=sample_fmts=dbl,astats=metadata=1:reset=0,"
        "ametadata=mode=print:key=lavfi.astats.Overall.Peak_level:file=-",
        "-f", "null", "-",
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("fmt", "codec_args"),
    [
        (OutputFormat.WAV, ["-c:a", "pcm_s24le"]),
        (OutputFormat.FLAC, ["-c:a", "flac", "-sample_fmt", "s32"]),
        (OutputFormat.MP3, ["-c:a", "libmp3lame", "-b:a", "320k"]),
    ],
)
def test_export_args(fmt: OutputFormat, codec_args: list[str]) -> None:
    dest = DEST.with_suffix(f".{fmt.value}")
    assert ffmpeg.export_args(WAV, dest, fmt, 0.5) == [
        *_PREFIX, "-y", "-i", str(WAV),
        "-map", "0:a:0", "-af", "volume=0.5", "-ar", "44100", "-ac", "2",
        *codec_args, str(dest),
    ]  # fmt: skip


@pytest.mark.parametrize("gain", [1.0, 0.8333333333333334, 1e-05])
def test_export_args_always_apply_gain_with_full_precision(gain: float) -> None:
    args = ffmpeg.export_args(WAV, DEST, OutputFormat.WAV, gain)
    assert args[args.index("-af") + 1] == f"volume={gain!r}"
    assert float(args[args.index("-af") + 1].removeprefix("volume=")) == gain


# --- normalize() result ------------------------------------------------------------------


def test_normalize_success_raises_nothing() -> None:
    ffmpeg.check_normalize_result(SRC, returncode=0, stderr="")


def test_normalize_without_audio_stream_raises_no_audio_stream_error() -> None:
    with pytest.raises(NoAudioStreamError) as excinfo:
        ffmpeg.check_normalize_result(SRC, returncode=234, stderr=NO_AUDIO_STDERR)
    _assert_user_error(excinfo.value, 3, SRC)


@pytest.mark.parametrize(
    "stderr",
    [UNREADABLE_STDERR, "", "trunc.flac: End of file\n"],
    ids=["invalid-data", "no-stderr", "truncated"],
)
def test_normalize_other_decode_failure_raises_media_read_error(stderr: str) -> None:
    with pytest.raises(MediaReadError) as excinfo:
        ffmpeg.check_normalize_result(SRC, returncode=183, stderr=stderr)
    _assert_user_error(excinfo.value, 3, SRC)


def test_normalize_failure_logs_ffmpeg_stderr_for_verbose(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with (
        caplog.at_level(logging.DEBUG, logger="stemseparator.media.ffmpeg"),
        pytest.raises(MediaReadError),
    ):
        ffmpeg.check_normalize_result(SRC, returncode=183, stderr=UNREADABLE_STDERR)
    assert UNREADABLE_STDERR.strip() in caplog.text


# --- peak() result -----------------------------------------------------------------------


def test_peak_above_full_scale_is_not_clipped() -> None:
    assert ffmpeg.parse_peak_output(WAV, PEAK_STDOUT_HOT) == pytest.approx(1.2, abs=1e-6)


def test_peak_uses_last_frame_running_value() -> None:
    expected = 10 ** (-6.023062 / 20)  # ~0.4999, the last frame, not the first
    assert ffmpeg.parse_peak_output(WAV, PEAK_STDOUT_RAMP) == pytest.approx(expected, rel=1e-9)


def test_peak_of_silence_is_zero() -> None:
    assert ffmpeg.parse_peak_output(WAV, PEAK_STDOUT_SILENCE) == 0.0


@pytest.mark.parametrize(
    ("stdout", "returncode"),
    [
        (PEAK_STDOUT_HOT, 1),  # ffmpeg failed
        ("", 0),  # no frames, no measurement
        ("frame:0    pts:0       pts_time:0\nlavfi.astats.Overall.Peak_level=nan\n", 0),
        ("frame:0    pts:0       pts_time:0\nlavfi.astats.Overall.Peak_level=inf\n", 0),
        ("frame:0    pts:0       pts_time:0\nlavfi.astats.Overall.Peak_level=abc\n", 0),
    ],
    ids=["ffmpeg-failed", "no-frames", "nan", "inf", "not-a-number"],
)
def test_peak_failure_raises_export_error(stdout: str, returncode: int) -> None:
    with pytest.raises(ExportError) as excinfo:
        ffmpeg.parse_peak_output(WAV, stdout, returncode=returncode, stderr="boom")
    _assert_user_error(excinfo.value, 5, WAV)


def test_peak_failure_logs_ffmpeg_stderr_for_verbose(caplog: pytest.LogCaptureFixture) -> None:
    with (
        caplog.at_level(logging.DEBUG, logger="stemseparator.media.ffmpeg"),
        pytest.raises(ExportError),
    ):
        ffmpeg.parse_peak_output(WAV, "", returncode=1, stderr="vocals.wav: No such file")
    assert "vocals.wav: No such file" in caplog.text


# --- export() result ---------------------------------------------------------------------


def test_export_success_raises_nothing() -> None:
    ffmpeg.check_export_result(WAV, DEST, returncode=0, stderr="")


def test_export_failure_raises_export_error_and_logs_stderr(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stderr = "Error opening output file clip_vocals.wav.\n"
    with (
        caplog.at_level(logging.DEBUG, logger="stemseparator.media.ffmpeg"),
        pytest.raises(ExportError) as excinfo,
    ):
        ffmpeg.check_export_result(WAV, DEST, returncode=1, stderr=stderr)
    _assert_user_error(excinfo.value, 5, DEST)
    assert stderr.strip() in caplog.text


# --- subprocess wiring -------------------------------------------------------------------


class _Recorder:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    def __call__(self, args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, self.returncode, self.stdout, "")


def _assert_safe_call(recorder: _Recorder, expected_args: list[str]) -> None:
    assert len(recorder.calls) == 1
    args, kwargs = recorder.calls[0]
    assert args == expected_args
    assert isinstance(args, list)
    assert kwargs.get("shell", False) is False  # ADR-003
    assert kwargs.get("timeout") is None  # only ffprobe has a timeout


def test_normalizer_runs_built_args_without_shell_or_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(ffmpeg.subprocess, "run", recorder)
    assert ffmpeg.FFmpegAudioNormalizer().normalize(SRC, WAV) == WAV
    _assert_safe_call(recorder, ffmpeg.normalize_args(SRC, WAV))


def test_exporter_peak_runs_built_args_without_shell_or_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(stdout=PEAK_STDOUT_HOT)
    monkeypatch.setattr(ffmpeg.subprocess, "run", recorder)
    assert ffmpeg.FFmpegStemExporter().peak(WAV) == pytest.approx(1.2, abs=1e-6)
    _assert_safe_call(recorder, ffmpeg.peak_args(WAV))


@pytest.mark.parametrize("fmt", list(OutputFormat))
def test_exporter_export_runs_built_args_without_shell_or_timeout(
    monkeypatch: pytest.MonkeyPatch, fmt: OutputFormat
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(ffmpeg.subprocess, "run", recorder)
    dest = DEST.with_suffix(f".{fmt.value}")
    assert ffmpeg.FFmpegStemExporter().export(WAV, dest, fmt, 0.75) == dest
    _assert_safe_call(recorder, ffmpeg.export_args(WAV, dest, fmt, 0.75))


def test_normalizer_maps_failed_run_to_media_read_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ffmpeg.subprocess, "run", _Recorder(returncode=1))
    with pytest.raises(MediaReadError):
        ffmpeg.FFmpegAudioNormalizer().normalize(SRC, WAV)


def test_exporter_maps_failed_run_to_export_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ffmpeg.subprocess, "run", _Recorder(returncode=1))
    with pytest.raises(ExportError):
        ffmpeg.FFmpegStemExporter().export(WAV, DEST, OutputFormat.WAV, 1.0)
    with pytest.raises(ExportError):
        ffmpeg.FFmpegStemExporter().peak(WAV)


def _missing_binary(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    raise FileNotFoundError(2, "No such file or directory", "ffmpeg")


@pytest.mark.parametrize(
    "call",
    [
        lambda: ffmpeg.FFmpegAudioNormalizer().normalize(SRC, WAV),
        lambda: ffmpeg.FFmpegStemExporter().peak(WAV),
        lambda: ffmpeg.FFmpegStemExporter().export(WAV, DEST, OutputFormat.WAV, 1.0),
    ],
    ids=["normalize", "peak", "export"],
)
def test_missing_ffmpeg_binary_raises_ffmpeg_not_found(
    monkeypatch: pytest.MonkeyPatch, call: Any
) -> None:
    monkeypatch.setattr(ffmpeg.subprocess, "run", _missing_binary)
    with pytest.raises(FFmpegNotFoundError) as excinfo:
        call()
    assert excinfo.value.exit_code == 4
    assert excinfo.value.hint.strip()
