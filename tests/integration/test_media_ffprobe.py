"""FFprobeMediaProbe against the real ffprobe binary (T-05, RF-02, CA-03, CA-21)."""

import os
import sys
from pathlib import Path

import pytest

from stemseparator.domain.errors import FFmpegNotFoundError, MediaReadError
from stemseparator.domain.models import MediaKind
from stemseparator.media import ffmpeg
from stemseparator.media.ffmpeg import FFprobeMediaProbe

pytestmark = pytest.mark.ffmpeg


def test_stereo_wav(stereo_wav: Path) -> None:
    info = FFprobeMediaProbe().probe(stereo_wav)
    assert info.path == stereo_wav
    assert info.kind is MediaKind.AUDIO
    assert (info.has_audio, info.sample_rate, info.channels) == (True, 44100, 2)
    assert info.duration_s == pytest.approx(2.0, abs=0.05)


def test_video_with_audio(video_mp4: Path) -> None:
    info = FFprobeMediaProbe().probe(video_mp4)
    assert info.kind is MediaKind.VIDEO
    assert (info.has_audio, info.sample_rate, info.channels, info.codec) == (
        True,
        48000,
        1,
        "aac",
    )


def test_ca03_mute_video_has_no_audio(mute_mkv: Path) -> None:
    info = FFprobeMediaProbe().probe(mute_mkv)
    assert info.kind is MediaKind.VIDEO
    assert info.has_audio is False


def test_ca21_mp3_with_cover_is_audio(cover_mp3: Path) -> None:
    info = FFprobeMediaProbe().probe(cover_mp3)
    assert info.kind is MediaKind.AUDIO
    assert info.has_audio is True
    assert info.duration_s == pytest.approx(2.0, abs=0.1)


def test_unreadable_file_raises_media_read_error(media_dir: Path) -> None:
    garbage = media_dir / "garbage.bin"
    garbage.write_bytes(bytes(range(256)) * 16)
    with pytest.raises(MediaReadError) as excinfo:
        FFprobeMediaProbe().probe(garbage)
    assert excinfo.value.exit_code == 3


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="os.mkfifo is POSIX-only; on Linux and macOS this test covers the ffprobe timeout",
)
def test_timeout_raises_media_read_error(
    media_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fifo = tmp_path / "blocked.wav"
    os.mkfifo(fifo)  # no writer: ffprobe blocks opening it until the timeout
    monkeypatch.setattr(ffmpeg, "FFPROBE_TIMEOUT_S", 0.5)
    with pytest.raises(MediaReadError):
        FFprobeMediaProbe().probe(fifo)


def test_missing_ffprobe_binary_raises_ffmpeg_not_found(
    stereo_wav: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))  # empty directory: no ffprobe on PATH
    with pytest.raises(FFmpegNotFoundError) as excinfo:
        FFprobeMediaProbe().probe(stereo_wav)
    assert excinfo.value.exit_code == 4
