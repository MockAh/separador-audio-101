"""FFmpeg normalizer and stem exporter against the real binary (T-06, RF-02, RF-06).

Covers CA-02 (part F: video normalized without intervention) and CA-10 (part F: wav,
flac and mp3 checked with ffprobe as in acceptance.md §1).
"""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from stemseparator.domain.errors import ExportError, MediaReadError, NoAudioStreamError
from stemseparator.domain.models import DURATION_TOLERANCE_S, OutputFormat
from stemseparator.media import ffmpeg

pytestmark = pytest.mark.ffmpeg

SOURCE_DURATION_S = 2.0
# 320 kbps at 44 100 Hz: 1152 samples per frame → 1044 bytes, 1045 with the padding bit.
MP3_320_FRAME_SIZES = {1044, 1045}


def _ffprobe(path: Path, *extra: str) -> dict[str, Any]:
    completed = subprocess.run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", *extra, str(path),
        ],
        check=True, capture_output=True, text=True,
    )  # fmt: skip
    data: dict[str, Any] = json.loads(completed.stdout)
    return data


def _audio_stream(path: Path) -> dict[str, Any]:
    streams = [s for s in _ffprobe(path)["streams"] if s["codec_type"] == "audio"]
    assert len(streams) == 1
    return streams[0]


def _duration(path: Path) -> float:
    return float(_ffprobe(path)["format"]["duration"])


# --- peak() ------------------------------------------------------------------------------


def test_peak_above_full_scale_is_measured_not_clipped(hot_float_wav: Path) -> None:
    assert _audio_stream(hot_float_wav)["codec_name"] == "pcm_f32le"
    assert ffmpeg.FFmpegStemExporter().peak(hot_float_wav) == pytest.approx(1.2, abs=1e-4)


def test_peak_below_full_scale(half_float_wav: Path) -> None:
    assert ffmpeg.FFmpegStemExporter().peak(half_float_wav) == pytest.approx(0.5, abs=1e-4)


def test_peak_of_unreadable_file_raises_export_error(media_dir: Path) -> None:
    garbage = media_dir / "garbage_peak.wav"
    garbage.write_bytes(bytes(range(256)) * 16)
    with pytest.raises(ExportError) as excinfo:
        ffmpeg.FFmpegStemExporter().peak(garbage)
    assert excinfo.value.exit_code == 5


# --- normalize() -------------------------------------------------------------------------


def _assert_work_audio(path: Path) -> None:
    stream = _audio_stream(path)
    assert stream["codec_name"] == "pcm_f32le"
    assert int(stream["sample_rate"]) == 44100
    assert stream["channels"] == 2


def test_normalize_mono_to_stereo(mono_wav: Path, tmp_path: Path) -> None:
    assert _audio_stream(mono_wav)["channels"] == 1
    dest = ffmpeg.FFmpegAudioNormalizer().normalize(mono_wav, tmp_path / "source.wav")
    assert dest == tmp_path / "source.wav"
    _assert_work_audio(dest)


def test_normalize_48k_to_44k1(wav_48k: Path, tmp_path: Path) -> None:
    assert int(_audio_stream(wav_48k)["sample_rate"]) == 48000
    dest = ffmpeg.FFmpegAudioNormalizer().normalize(wav_48k, tmp_path / "source.wav")
    _assert_work_audio(dest)
    assert _duration(dest) == pytest.approx(SOURCE_DURATION_S, abs=0.05)


def test_ca02_video_is_normalized_without_intervention(video_mp4: Path, tmp_path: Path) -> None:
    """CA-02 (F): MP4 with video first and mono 48 kHz AAC second → work WAV."""
    dest = ffmpeg.FFmpegAudioNormalizer().normalize(video_mp4, tmp_path / "source.wav")
    _assert_work_audio(dest)
    assert [s["codec_type"] for s in _ffprobe(dest)["streams"]] == ["audio"]
    assert _duration(dest) == pytest.approx(SOURCE_DURATION_S, abs=0.1)


def test_normalize_keeps_float_peak_above_full_scale(hot_float_wav: Path, tmp_path: Path) -> None:
    dest = ffmpeg.FFmpegAudioNormalizer().normalize(hot_float_wav, tmp_path / "source.wav")
    assert ffmpeg.FFmpegStemExporter().peak(dest) == pytest.approx(1.2, abs=1e-4)


def test_normalize_mute_mkv_raises_no_audio_stream_error(mute_mkv: Path, tmp_path: Path) -> None:
    dest = tmp_path / "source.wav"
    with pytest.raises(NoAudioStreamError) as excinfo:
        ffmpeg.FFmpegAudioNormalizer().normalize(mute_mkv, dest)
    assert excinfo.value.exit_code == 3
    assert not dest.exists()


def test_normalize_undecodable_input_raises_media_read_error(
    media_dir: Path, tmp_path: Path
) -> None:
    garbage = media_dir / "garbage_normalize.bin"
    garbage.write_bytes(bytes(range(256)) * 16)
    with pytest.raises(MediaReadError) as excinfo:
        ffmpeg.FFmpegAudioNormalizer().normalize(garbage, tmp_path / "source.wav")
    assert excinfo.value.exit_code == 3


# --- export() (CA-10, F) -----------------------------------------------------------------


@pytest.mark.parametrize("fmt", list(OutputFormat))
def test_ca10_export_parameters(fmt: OutputFormat, half_float_wav: Path, tmp_path: Path) -> None:
    dest = tmp_path / f"clip_vocals.{fmt.value}"
    assert ffmpeg.FFmpegStemExporter().export(half_float_wav, dest, fmt, 1.0) == dest
    assert dest.stat().st_size > 0

    stream = _audio_stream(dest)
    assert int(stream["sample_rate"]) == 44100
    assert stream["channels"] == 2
    if fmt is OutputFormat.WAV:
        assert stream["codec_name"] == "pcm_s24le"
    elif fmt is OutputFormat.FLAC:
        assert stream["codec_name"] == "flac"
        assert int(stream["bits_per_raw_sample"]) == 24
    else:
        assert stream["codec_name"] == "mp3"
        assert int(stream["bit_rate"]) == 320_000
        packets = _ffprobe(dest, "-show_packets", "-select_streams", "a")["packets"]
        assert {int(p["size"]) for p in packets} <= MP3_320_FRAME_SIZES  # CBR

    work_duration = _duration(half_float_wav)
    assert abs(_duration(dest) - work_duration) <= DURATION_TOLERANCE_S[fmt]


@pytest.mark.parametrize("fmt", list(OutputFormat))
def test_export_applies_gain(fmt: OutputFormat, hot_float_wav: Path, tmp_path: Path) -> None:
    exporter = ffmpeg.FFmpegStemExporter()
    dest = exporter.export(hot_float_wav, tmp_path / f"half.{fmt.value}", fmt, 0.5)
    tolerance = 1e-3 if fmt is OutputFormat.MP3 else 1e-4  # lossy codec overshoot
    assert exporter.peak(dest) == pytest.approx(0.6, abs=tolerance)


@pytest.mark.parametrize("fmt", list(OutputFormat))
def test_export_unit_gain_keeps_peak(
    fmt: OutputFormat, half_float_wav: Path, tmp_path: Path
) -> None:
    exporter = ffmpeg.FFmpegStemExporter()
    dest = exporter.export(half_float_wav, tmp_path / f"same.{fmt.value}", fmt, 1.0)
    tolerance = 1e-3 if fmt is OutputFormat.MP3 else 1e-4
    assert exporter.peak(dest) == pytest.approx(0.5, abs=tolerance)
