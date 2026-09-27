"""Domain models and constants (interfaces.md §2, acceptance.md §1)."""

from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from stemseparator.domain.models import (
    DURATION_TOLERANCE_S,
    OUTPUT_PEAK_CEILING,
    MediaInfo,
    MediaKind,
    ModelSpec,
    OutputFormat,
    SeparationRequest,
    SeparationResult,
    StemFile,
    StemName,
)


def _media_info() -> MediaInfo:
    return MediaInfo(
        path=Path("cancion.mp4"),
        kind=MediaKind.VIDEO,
        duration_s=210.0,
        has_audio=True,
        sample_rate=48000,
        channels=2,
        codec="aac",
    )


def _model_spec() -> ModelSpec:
    return ModelSpec(
        id="vocals",
        engine_filename="model.onnx",
        stems=(StemName.VOCALS, StemName.INSTRUMENTAL),
        engine_labels={"Vocals": StemName.VOCALS, "Instrumental": StemName.INSTRUMENTAL},
        cpu_rtf=2.4,
        cpu_reference="equipo de referencia",
        weights_license="MIT",
        download_size_bytes=67_000_000,
        description="Voz e instrumental.",
        is_default=True,
    )


def _stem_file() -> StemFile:
    return StemFile(
        stem=StemName.VOCALS,
        path=Path("stems/cancion_vocals.wav"),
        duration_s=210.0,
        sample_rate=44100,
        channels=2,
        size_bytes=1000,
        peak=0.9,
    )


def _request() -> SeparationRequest:
    return SeparationRequest(input_path=Path("cancion.mp4"), output_dir=Path("stems"))


def _result() -> SeparationResult:
    return SeparationResult(
        input=_media_info(),
        work_duration_s=210.0,
        model_id="vocals",
        stems=(_stem_file(),),
        output_gain=0.8,
        elapsed_s=500.0,
    )


def test_enum_values() -> None:
    assert [s.value for s in StemName] == ["vocals", "instrumental"]
    assert [f.value for f in OutputFormat] == ["wav", "flac", "mp3"]
    assert [k.value for k in MediaKind] == ["audio", "video"]


def test_enums_are_str() -> None:
    assert StemName.VOCALS == "vocals"
    assert OutputFormat.MP3 == "mp3"
    assert MediaKind.AUDIO == "audio"


@pytest.mark.parametrize(
    ("factory", "field", "value"),
    [
        (_media_info, "duration_s", 1.0),
        (_model_spec, "id", "otro"),
        (_request, "overwrite", True),
        (_stem_file, "peak", 0.1),
        (_result, "output_gain", 1.0),
    ],
)
def test_models_are_immutable(factory: Any, field: str, value: object) -> None:
    instance: BaseModel = factory()

    with pytest.raises(ValidationError):
        setattr(instance, field, value)


def test_request_defaults() -> None:
    request = _request()

    assert request.model_id is None
    assert request.stems is None
    assert request.output_format is OutputFormat.WAV
    assert request.overwrite is False


def test_model_spec_is_not_default_unless_stated() -> None:
    data = _model_spec().model_dump(exclude={"is_default"})

    assert ModelSpec(**data).is_default is False


def test_output_peak_ceiling() -> None:
    assert OUTPUT_PEAK_CEILING == 0.999


def test_duration_tolerance_per_format() -> None:
    assert dict(DURATION_TOLERANCE_S) == {
        OutputFormat.WAV: 0.050,
        OutputFormat.FLAC: 0.050,
        OutputFormat.MP3: 0.100,
    }


def test_duration_tolerance_is_immutable() -> None:
    assert isinstance(DURATION_TOLERANCE_S, MappingProxyType)
    with pytest.raises(TypeError):
        DURATION_TOLERANCE_S[OutputFormat.WAV] = 1.0  # type: ignore[index]
