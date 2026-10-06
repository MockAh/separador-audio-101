"""AudioSeparatorEngine with the real audio-separator and real weights (T-11).

Marked `slow`: downloads the weights on the first run (network allowed in CLAUDE.md) and
runs the model on CPU. The whole pipeline runs through SeparationService with the real
FFmpeg adapters, because CA-01, CA-02 and CA-25 talk about the exported stems. The clip is
10 s, synthetic (lavfi), unless STEMSEPARATOR_SAMPLE_AUDIO points at a real one.
"""

import math
import os
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import stemseparator.engines.audio_separator_engine as ase
from stemseparator.application.service import SeparationService
from stemseparator.domain.catalog import get_model
from stemseparator.domain.models import (
    DURATION_TOLERANCE_S,
    OutputFormat,
    SeparationRequest,
    SeparationResult,
    StemName,
)
from stemseparator.media.ffmpeg import FFmpegAudioNormalizer, FFmpegStemExporter, FFprobeMediaProbe
from stemseparator.media.system import LocalSystemChecks

pytestmark = pytest.mark.slow

MIN_RECONSTRUCTION_SNR_DB = 30.0  # acceptance.md §1

# 10 s "song": bass and chord pad, a kick every half second and a vibrato "voice" with a
# slow swell, slightly different per channel. Peak below 1.
_LEFT = (
    "0.20*sin(2*PI*110*t)+0.10*sin(2*PI*165*t)+0.08*sin(2*PI*220*t)*(0.5+0.5*sin(2*PI*2*t))"
    "+0.25*exp(-30*mod(t,0.5))*sin(2*PI*60*t)"
    "+0.25*sin(2*PI*(440+30*sin(2*PI*5*t))*t)*(0.6+0.4*sin(2*PI*0.5*t))"
)
_RIGHT = _LEFT.replace("0.10*sin(2*PI*165*t)", "0.12*sin(2*PI*247*t)")


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="session")
def song_clip(media_dir: Path) -> Path:
    sample = os.environ.get("STEMSEPARATOR_SAMPLE_AUDIO")
    if sample:
        return Path(sample)
    dest = media_dir / "song.wav"
    _ffmpeg(
        "-f", "lavfi", "-i", f"aevalsrc=exprs='{_LEFT}|{_RIGHT}':s=44100:d=10",
        "-c:a", "pcm_f32le", str(dest),
    )  # fmt: skip
    return dest


@pytest.fixture(scope="session")
def video_clip(media_dir: Path, song_clip: Path) -> Path:
    dest = media_dir / "song.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc=duration=10:size=160x120:rate=25",
        "-i", str(song_clip), "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        str(dest),
    )  # fmt: skip
    return dest


@pytest.fixture(scope="session")
def model_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Empty on purpose: the first run must download the weights into it (CA-16)."""
    return tmp_path_factory.mktemp("models")


def _service(model_dir: Path) -> SeparationService:
    return SeparationService(
        ase.AudioSeparatorEngine(),
        FFprobeMediaProbe(),
        FFmpegAudioNormalizer(),
        FFmpegStemExporter(),
        LocalSystemChecks(),
        model_dir,
    )


def _separate(model_dir: Path, audio: Path, out: Path, model_id: str) -> SeparationResult:
    request = SeparationRequest(input_path=audio, output_dir=out, model_id=model_id)
    return _service(model_dir).run(request)


def _snapshot(directory: Path) -> dict[str, tuple[int, int]]:
    """Name -> (size, mtime_ns) of every file, recursively."""
    return {
        str(p.relative_to(directory)): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


@dataclass(frozen=True)
class FirstRun:
    before: dict[str, tuple[int, int]]
    after: dict[str, tuple[int, int]]
    result: SeparationResult


@pytest.fixture(scope="session")
def first_run(
    model_dir: Path, song_clip: Path, tmp_path_factory: pytest.TempPathFactory
) -> FirstRun:
    """The session's first separation with `vocals`: the one that downloads the weights."""
    before = _snapshot(model_dir)
    result = _separate(model_dir, song_clip, tmp_path_factory.mktemp("out"), "vocals")
    return FirstRun(before, _snapshot(model_dir), result)


def _assert_valid_stems(result: SeparationResult, out: Path, stem_prefix: str) -> None:
    """Both stems, valid per acceptance.md §1 (WAV: 44 100 Hz, 2 channels, pcm_s24le)."""
    assert {s.stem for s in result.stems} == {StemName.VOCALS, StemName.INSTRUMENTAL}
    probe = FFprobeMediaProbe()
    for stem in result.stems:
        assert stem.path == out / f"{stem_prefix}_{stem.stem.value}.wav"
        assert stem.path.stat().st_size > 0
        info = probe.probe(stem.path)
        assert (info.sample_rate, info.channels, info.codec) == (44100, 2, "pcm_s24le")
        tolerance = DURATION_TOLERANCE_S[OutputFormat.WAV]
        assert abs(info.duration_s - result.work_duration_s) <= tolerance


# ── CA-16: model cache ──────────────────────────────────────────────────────────────────


def test_ca16_first_run_downloads_the_weights_into_model_dir(first_run: FirstRun) -> None:
    model = get_model("vocals")

    assert first_run.before == {}
    assert first_run.after[model.engine_filename][0] == model.download_size_bytes


def test_ca16_second_run_works_offline_and_downloads_nothing(
    first_run: FirstRun,
    model_dir: Path,
    song_clip: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[Any] = []

    def no_network(*args: Any, **kwargs: Any) -> Any:
        attempts.append(args)
        raise OSError("network disabled by the CA-16 test")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)

    result = _separate(model_dir, song_clip, tmp_path, "vocals")

    assert attempts == []
    assert _snapshot(model_dir) == first_run.after
    _assert_valid_stems(result, tmp_path, song_clip.stem)


# ── CA-01 and CA-02 (S): both stems produced ────────────────────────────────────────────


def test_ca01_wav_input_produces_both_valid_stems(first_run: FirstRun, song_clip: Path) -> None:
    out = first_run.result.stems[0].path.parent

    _assert_valid_stems(first_run.result, out, song_clip.stem)


def test_ca02_video_input_produces_both_valid_stems(
    first_run: FirstRun, model_dir: Path, video_clip: Path, tmp_path: Path
) -> None:
    result = _separate(model_dir, video_clip, tmp_path, "vocals")

    _assert_valid_stems(result, tmp_path, video_clip.stem)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        f"{video_clip.stem}_instrumental.wav",
        f"{video_clip.stem}_vocals.wav",
    ]


# ── CA-25 (S): the stems add back up to the working audio ───────────────────────────────


def _reconstruction_snr_db(work_audio: Path, result: SeparationResult) -> float:
    import numpy as np
    import soundfile as sf

    work, _ = sf.read(work_audio, dtype="float64", always_2d=True)
    stems = {s.stem: sf.read(s.path, dtype="float64", always_2d=True)[0] for s in result.stems}
    total = (stems[StemName.VOCALS] + stems[StemName.INSTRUMENTAL]) / result.output_gain
    n = min(len(work), len(total))  # durations already checked within tolerance
    residual = work[:n] - total[:n]
    return 20 * math.log10(
        float(np.sqrt(np.mean(work[:n] ** 2))) / float(np.sqrt(np.mean(residual**2)))
    )


@pytest.mark.parametrize("model_id", ["vocals", "vocals-max"])
def test_ca25_stems_reconstruct_the_working_audio(
    first_run: FirstRun,
    model_dir: Path,
    song_clip: Path,
    tmp_path: Path,
    model_id: str,
) -> None:
    if model_id == "vocals":
        result = first_run.result
    else:
        result = _separate(model_dir, song_clip, tmp_path / "out", model_id)
    # Same normalizer, same input: the same working audio the service separated.
    work_audio = FFmpegAudioNormalizer().normalize(song_clip, tmp_path / "work.wav")

    snr = _reconstruction_snr_db(work_audio, result)

    print(f"{model_id}: SNR {snr:.1f} dB, output_gain {result.output_gain:.4f}")
    assert snr >= MIN_RECONSTRUCTION_SNR_DB
