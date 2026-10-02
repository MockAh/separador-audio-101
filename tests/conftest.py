"""Shared test fixtures: synthetic audio/video generated with FFmpeg `lavfi`.

The media fixtures skip the test when FFmpeg/FFprobe are not installed. FakeEngine is
added in a later task (T-09).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

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
