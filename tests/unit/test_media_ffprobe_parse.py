"""Parsing of ffprobe JSON into MediaInfo (T-05, RF-02, CA-03, CA-21, interfaces.md §3).

No subprocess, no FFmpeg: the inputs are real ffprobe outputs stored in data/ffprobe/,
captured once from the synthetic fixtures of tests/conftest.py with
`ffprobe -v error -print_format json -show_format -show_streams <file>`, except:
- audio_wav_no_format_duration.json: same command with `-show_entries
  format=filename,nb_streams,format_name,size,bit_rate` instead of `-show_format`;
- audio_wav_piped_no_duration.json: the WAV fed through stdin (`-i pipe:0 < tone.wav`);
- unreadable.json: 4 KiB of non-media bytes (ffprobe exit code 1).
"""

import json
import logging
from pathlib import Path

import pytest

from stemseparator.domain.errors import MediaReadError
from stemseparator.domain.models import MediaInfo, MediaKind
from stemseparator.media.ffmpeg import FFPROBE_TIMEOUT_S, parse_ffprobe_output

DATA = Path(__file__).parent / "data" / "ffprobe"


def _captured(name: str) -> str:
    return (DATA / f"{name}.json").read_text(encoding="utf-8")


def _parse(name: str, path: Path = Path("input")) -> MediaInfo:
    return parse_ffprobe_output(path, _captured(name))


def test_stereo_wav_is_audio_with_first_audio_stream_fields() -> None:
    info = _parse("audio_wav")
    assert info.kind is MediaKind.AUDIO
    assert info.has_audio is True
    assert (info.sample_rate, info.channels, info.codec) == (44100, 2, "pcm_s16le")
    assert info.duration_s == pytest.approx(2.0)


def test_mp4_with_video_and_audio_is_video_and_reads_audio_stream_not_first_stream() -> None:
    info = _parse("video_mp4")  # stream 0 is h264 video, stream 1 is mono 48 kHz AAC
    assert info.kind is MediaKind.VIDEO
    assert info.has_audio is True
    assert (info.sample_rate, info.channels, info.codec) == (48000, 1, "aac")
    assert info.duration_s == pytest.approx(2.0)


def test_mute_mkv_has_no_audio_and_no_audio_fields() -> None:
    """CA-03 (parsing part): a video without an audio stream gives has_audio False."""
    info = _parse("video_mute_mkv")
    assert info.kind is MediaKind.VIDEO
    assert info.has_audio is False
    assert (info.sample_rate, info.channels, info.codec) == (None, None, None)
    assert info.duration_s == pytest.approx(2.0)


def test_mp3_with_cover_art_is_audio() -> None:
    """CA-21 (parsing part): a video stream with attached_pic == 1 is a cover, not video."""
    info = _parse("audio_mp3_cover")
    assert info.kind is MediaKind.AUDIO
    assert info.has_audio is True
    assert (info.sample_rate, info.channels, info.codec) == (44100, 2, "mp3")
    assert info.duration_s == pytest.approx(2.037551)


def test_video_stream_without_disposition_is_video() -> None:
    data = json.loads(_captured("video_mute_mkv"))
    del data["streams"][0]["disposition"]
    info = parse_ffprobe_output(Path("input"), json.dumps(data))
    assert info.kind is MediaKind.VIDEO


def test_path_is_kept_as_received() -> None:
    path = Path("relative") / ".." / "tone.wav"
    assert _parse("audio_wav", path).path == path


def test_duration_falls_back_to_first_audio_stream_without_format_duration() -> None:
    info = _parse("audio_wav_no_format_duration")
    assert info.duration_s == pytest.approx(2.0)


def _assert_media_read_error(err: MediaReadError, path: Path) -> None:
    assert err.exit_code == 3
    assert path.name in err.message
    assert err.hint.strip()


def test_no_duration_anywhere_raises_media_read_error() -> None:
    path = Path("tone.wav")
    with pytest.raises(MediaReadError) as excinfo:
        _parse("audio_wav_piped_no_duration", path)
    _assert_media_read_error(excinfo.value, path)


def test_duration_not_convertible_to_float_raises_media_read_error() -> None:
    data = json.loads(_captured("audio_wav"))
    data["format"]["duration"] = "N/A"
    path = Path("tone.wav")
    with pytest.raises(MediaReadError) as excinfo:
        parse_ffprobe_output(path, json.dumps(data))
    _assert_media_read_error(excinfo.value, path)


def test_invalid_json_raises_media_read_error() -> None:
    text = _captured("audio_wav")
    path = Path("tone.wav")
    with pytest.raises(MediaReadError) as excinfo:
        parse_ffprobe_output(path, text[: len(text) // 2])  # truncated real output
    _assert_media_read_error(excinfo.value, path)


def test_nonzero_exit_code_raises_media_read_error() -> None:
    path = Path("garbage.bin")
    with pytest.raises(MediaReadError) as excinfo:
        parse_ffprobe_output(path, _captured("unreadable"), returncode=1)
    _assert_media_read_error(excinfo.value, path)


def test_nonzero_exit_code_logs_ffprobe_stderr_for_verbose(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stderr = "garbage.bin: Invalid data found when processing input"
    with (
        caplog.at_level(logging.DEBUG, logger="stemseparator.media.ffmpeg"),
        pytest.raises(MediaReadError),
    ):
        parse_ffprobe_output(
            Path("garbage.bin"), _captured("unreadable"), returncode=1, stderr=stderr
        )
    assert stderr in caplog.text


def test_ffprobe_timeout_is_thirty_seconds() -> None:
    assert FFPROBE_TIMEOUT_S == 30
