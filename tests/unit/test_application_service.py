"""SeparationService: VALIDATING and PREPARING with test doubles (T-08, domain.md §4-§5).

No network, FFmpeg or engine. Every double appends to one shared call log, so the tests
check both what is called, in which order, and what is NOT called. The system temp
directory is pinned to tmp_path/"systmp" through `tempfile.tempdir`, so the temp volume
that VALIDATING measures and the work directory that run() creates are both observable.
"""

import os
import stat
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from stemseparator.application.service import SeparationService
from stemseparator.domain import catalog
from stemseparator.domain.errors import (
    FFmpegNotFoundError,
    InputNotFoundError,
    InsufficientDiskSpaceError,
    MediaReadError,
    NoAudioStreamError,
    OutputConflictError,
    OutputNotWritableError,
    StemSeparatorError,
    UnknownModelError,
    UnsupportedFormatError,
    UnsupportedStemError,
)
from stemseparator.domain.models import (
    MediaInfo,
    MediaKind,
    ModelSpec,
    OutputFormat,
    SeparationRequest,
    StemName,
)
from stemseparator.domain.progress import JobStage

Calls = list[tuple[Any, ...]]

SUPPORTED_EXTENSIONS = (".wav", ".mp3", ".flac", ".m4a", ".mp4", ".mkv")
INPUT_DURATION_S = 12.5
# domain.md §5 by hand for the default model `vocals` (2 stems) and 12.5 s:
#   12.5 × 44100 × 2 channels × 4 bytes × (2 + 1) × 2
#   = 551_250 × 8 × 3 × 2 = 4_410_000 × 6 = 26_460_000 bytes
EXPECTED_NEEDED_BYTES = 26_460_000
PLENTY = 10**12


# --- test doubles -----------------------------------------------------------------------


class RecordingReporter:
    def __init__(self, calls: Calls) -> None:
        self.calls = calls

    def stage_started(self, stage: JobStage) -> None:
        self.calls.append(("started", stage))

    def stage_finished(self, stage: JobStage, elapsed_s: float) -> None:
        self.calls.append(("finished", stage, elapsed_s))

    def stage_failed(self, stage: JobStage, error: BaseException) -> None:
        self.calls.append(("failed", stage, error))


class FakeProbe:
    def __init__(self, calls: Calls) -> None:
        self.calls = calls
        self.has_audio = True
        self.kind = MediaKind.AUDIO
        self.codec = "pcm_s16le"
        self.error: StemSeparatorError | None = None

    def probe(self, path: Path) -> MediaInfo:
        self.calls.append(("probe", path))
        if path.name == "source.wav":  # the working audio written by normalize()
            return MediaInfo(
                path=path,
                kind=MediaKind.AUDIO,
                duration_s=INPUT_DURATION_S,
                has_audio=True,
                sample_rate=44100,
                channels=2,
                codec="pcm_f32le",
            )
        if self.error is not None:
            raise self.error
        return MediaInfo(
            path=path,
            kind=self.kind,
            duration_s=INPUT_DURATION_S,
            has_audio=self.has_audio,
            sample_rate=44100 if self.has_audio else None,
            channels=2 if self.has_audio else None,
            codec=self.codec if self.has_audio else None,
        )


class FakeNormalizer:
    def __init__(self, calls: Calls) -> None:
        self.calls = calls
        self.error: StemSeparatorError | None = None

    def normalize(self, src_path: Path, dest: Path) -> Path:
        self.calls.append(("normalize", src_path, dest))
        if self.error is not None:
            raise self.error
        dest.write_bytes(b"")  # fails if the work directory does not exist
        return dest


class FakeExporter:
    def __init__(self, calls: Calls) -> None:
        self.calls = calls

    def peak(self, src_wav: Path) -> float:
        self.calls.append(("peak", src_wav))
        return 0.5

    def export(self, src_wav: Path, dest: Path, fmt: OutputFormat, gain: float) -> Path:
        self.calls.append(("export", src_wav, dest, fmt, gain))
        return dest


class FakeSystem:
    def __init__(self, calls: Calls) -> None:
        self.calls = calls
        self.ffmpeg_present = True
        self.free: dict[Path, int] = {}

    def require_ffmpeg(self) -> None:
        self.calls.append(("require_ffmpeg",))
        if not self.ffmpeg_present:
            raise FFmpegNotFoundError("No se encontró ffmpeg en el PATH.", "Instala FFmpeg.")

    def free_bytes(self, path: Path) -> int:
        self.calls.append(("free_bytes", Path(path).resolve()))
        return self.free.get(Path(path).resolve(), PLENTY)


class FakeEngine:
    def __init__(self, calls: Calls) -> None:
        self.calls = calls

    def separate(self, *args: Any, **kwargs: Any) -> dict[StemName, Path]:
        self.calls.append(("separate", args, kwargs))
        raise AssertionError("the engine must not be called before T-09")


class World:
    """Service wired to recording doubles, plus the paths of one test."""

    def __init__(self, tmp_path: Path, systmp: Path) -> None:
        self.calls: Calls = []
        self.reporter = RecordingReporter(self.calls)
        self.probe = FakeProbe(self.calls)
        self.normalizer = FakeNormalizer(self.calls)
        self.exporter = FakeExporter(self.calls)
        self.system = FakeSystem(self.calls)
        self.engine = FakeEngine(self.calls)
        self.service = SeparationService(
            engine=self.engine,
            probe=self.probe,
            normalizer=self.normalizer,
            exporter=self.exporter,
            system=self.system,
            model_dir=tmp_path / "models",
        )
        self.systmp = systmp
        self.input_dir = tmp_path / "in"
        self.input_dir.mkdir()
        self.input = self.make_input("tema.wav")
        self.out = tmp_path / "out"

    def make_input(self, name: str) -> Path:
        path = self.input_dir / name
        path.write_bytes(b"not really audio: the probe is a double")
        return path

    def request(self, **overrides: Any) -> SeparationRequest:
        fields: dict[str, Any] = {"input_path": self.input, "output_dir": self.out}
        fields.update(overrides)
        return SeparationRequest(**fields)

    def names(self) -> list[str]:
        return [call[0] for call in self.calls]

    def count(self, name: str) -> int:
        return self.names().count(name)

    def stage_events(self) -> list[tuple[str, JobStage]]:
        return [
            (call[0], call[1])
            for call in self.calls
            if call[0] in {"started", "finished", "failed"}
        ]

    def input_probes(self) -> list[Path]:
        return [call[1] for call in self.calls if call[0] == "probe" and call[1] == self.input]


@pytest.fixture
def systmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "systmp"
    path.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(path))
    return path


@pytest.fixture
def world(tmp_path: Path, systmp: Path) -> World:
    return World(tmp_path, systmp)


@pytest.fixture
def only_vocals_model(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModelSpec]:
    """Default model replaced by one that only produces vocals.

    The real catalog has no model whose stems differ, and StemName has no DRUMS yet, so an
    unsupported stem can only be built this way at the service level (CA-08).
    """
    real = catalog.list_models()[0]
    model = real.model_copy(
        update={"stems": (StemName.VOCALS,), "engine_labels": {"Vocals": StemName.VOCALS}}
    )
    monkeypatch.setattr(catalog, "list_models", lambda: (model,))
    yield model


def _make_unreadable(path: Path) -> None:
    path.chmod(0)
    if os.access(path, os.R_OK):  # root, or a file system that ignores the mode
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        pytest.skip("cannot make a file unreadable here")


def _make_unwritable_dir(path: Path) -> None:
    path.chmod(stat.S_IRUSR | stat.S_IXUSR)
    if os.access(path, os.W_OK):
        path.chmod(stat.S_IRWXU)
        pytest.skip("cannot make a directory unwritable here")


# --- order of VALIDATING (domain.md §4; with several errors, the first one wins) --------


def test_missing_input_wins_over_unknown_model(world: World) -> None:
    request = world.request(input_path=world.input_dir / "falta.wav", model_id="no-existe")

    with pytest.raises(InputNotFoundError):
        world.service.validate(request)


def test_missing_input_wins_over_missing_ffmpeg(world: World) -> None:
    world.system.ffmpeg_present = False
    request = world.request(input_path=world.input_dir / "falta.wav")

    with pytest.raises(InputNotFoundError):
        world.service.validate(request)
    assert world.count("require_ffmpeg") == 0


def test_unsupported_extension_wins_over_missing_ffmpeg(world: World) -> None:
    world.system.ffmpeg_present = False
    request = world.request(input_path=world.make_input("documento.txt"))

    with pytest.raises(UnsupportedFormatError):
        world.service.validate(request)
    assert world.count("require_ffmpeg") == 0


def test_missing_ffmpeg_wins_over_unknown_model(world: World) -> None:
    world.system.ffmpeg_present = False

    with pytest.raises(FFmpegNotFoundError) as excinfo:
        world.service.validate(world.request(model_id="no-existe"))

    assert excinfo.value.exit_code == 4


def test_unknown_model_wins_over_output_conflict(world: World) -> None:
    world.out.mkdir()
    (world.out / "tema_vocals.wav").write_bytes(b"old")

    with pytest.raises(UnknownModelError):
        world.service.validate(world.request(model_id="no-existe"))


def test_missing_input_with_unsupported_extension_is_not_found(world: World) -> None:
    missing = world.input_dir / "nope.txt"

    with pytest.raises(InputNotFoundError) as excinfo:
        world.service.validate(world.request(input_path=missing))

    assert str(missing) in excinfo.value.message


# --- CA-05: missing or unreadable input -------------------------------------------------


def _assert_nothing_created(world: World) -> None:
    assert not world.out.exists()
    assert list(world.systmp.iterdir()) == []
    assert world.count("probe") == 0
    assert world.count("normalize") == 0


def test_ca05_missing_input(world: World) -> None:
    missing = world.input_dir / "falta.wav"

    with pytest.raises(InputNotFoundError) as excinfo:
        world.service.validate(world.request(input_path=missing))

    assert excinfo.value.exit_code == 3
    assert str(missing) in excinfo.value.message
    _assert_nothing_created(world)


def test_ca05_directory_as_input(world: World) -> None:
    directory = world.input_dir / "carpeta.wav"
    directory.mkdir()

    with pytest.raises(InputNotFoundError) as excinfo:
        world.service.validate(world.request(input_path=directory))

    assert excinfo.value.exit_code == 3
    assert str(directory) in excinfo.value.message
    _assert_nothing_created(world)


def test_ca05_unreadable_input(world: World) -> None:
    _make_unreadable(world.input)
    try:
        with pytest.raises(InputNotFoundError) as excinfo:
            world.service.validate(world.request())
    finally:
        world.input.chmod(stat.S_IRUSR | stat.S_IWUSR)

    assert excinfo.value.exit_code == 3
    assert str(world.input) in excinfo.value.message
    _assert_nothing_created(world)


# --- CA-06: input format (RF-01) --------------------------------------------------------


def test_ca06_unsupported_extension_lists_supported_ones(world: World) -> None:
    with pytest.raises(UnsupportedFormatError) as excinfo:
        world.service.validate(world.request(input_path=world.make_input("documento.txt")))

    assert excinfo.value.exit_code == 3
    for extension in SUPPORTED_EXTENSIONS:
        assert extension in excinfo.value.message
    _assert_nothing_created(world)


@pytest.mark.parametrize("name", ["TEMA.WAV", "Tema.Mp3", "clip.MKV"])
def test_ca06_extension_is_case_insensitive(world: World, name: str) -> None:
    world.service.validate(world.request(input_path=world.make_input(name)))


# --- CA-07: unknown model (RF-03) -------------------------------------------------------


def test_ca07_unknown_model_suggests_models_and_probes_nothing(world: World) -> None:
    with pytest.raises(UnknownModelError) as excinfo:
        world.service.validate(world.request(model_id="no-existe"))

    assert excinfo.value.exit_code == 3
    assert "stemseparator models" in excinfo.value.hint
    assert world.count("probe") == 0
    assert world.count("separate") == 0
    assert not world.out.exists()


# --- CA-08: stem the model does not produce (RF-04) -------------------------------------


def test_ca08_unsupported_stem_lists_model_stems(
    world: World, only_vocals_model: ModelSpec
) -> None:
    with pytest.raises(UnsupportedStemError) as excinfo:
        world.service.validate(world.request(stems=frozenset({StemName.INSTRUMENTAL})))

    assert excinfo.value.exit_code == 3
    assert "vocals" in excinfo.value.message
    assert world.count("probe") == 0
    assert not world.out.exists()


# --- CA-11 without --overwrite (RF-07) --------------------------------------------------


def test_ca11_existing_output_is_a_conflict_naming_the_file(world: World) -> None:
    world.out.mkdir()
    existing = world.out / "tema_vocals.wav"
    existing.write_bytes(b"old")

    with pytest.raises(OutputConflictError) as excinfo:
        world.service.validate(world.request())

    assert excinfo.value.exit_code == 3
    assert "tema_vocals.wav" in excinfo.value.message
    assert existing.read_bytes() == b"old"
    assert world.count("probe") == 0


def test_ca11_overwrite_skips_the_conflict(world: World) -> None:
    world.out.mkdir()
    (world.out / "tema_vocals.wav").write_bytes(b"old")

    world.service.validate(world.request(overwrite=True))


@pytest.mark.parametrize(
    "overrides",
    [
        {"stems": frozenset({StemName.INSTRUMENTAL})},  # vocals not requested
        {"output_format": OutputFormat.FLAC},  # .wav is not the requested extension
    ],
    ids=["other-stem", "other-format"],
)
def test_ca11_conflicts_use_requested_stems_and_format(
    world: World, overrides: dict[str, Any]
) -> None:
    world.out.mkdir()
    (world.out / "tema_vocals.wav").write_bytes(b"old")

    world.service.validate(world.request(**overrides))


def test_ca11_conflict_name_uses_requested_format(world: World) -> None:
    world.out.mkdir()
    (world.out / "tema_instrumental.flac").write_bytes(b"old")

    with pytest.raises(OutputConflictError) as excinfo:
        world.service.validate(world.request(output_format=OutputFormat.FLAC))

    assert "tema_instrumental.flac" in excinfo.value.message


# --- CA-22: output directory (RF-05, RF-09) ---------------------------------------------


def test_ca22_valid_request_creates_missing_output_dir(world: World) -> None:
    assert not world.out.exists()

    world.service.validate(world.request())

    assert world.out.is_dir()


@pytest.mark.parametrize(
    "overrides",
    [{"model_id": "no-existe"}, {"stems": frozenset({StemName.INSTRUMENTAL})}],
    ids=["unknown-model", "unsupported-stem"],
)
def test_ca22_earlier_error_does_not_create_output_dir(
    world: World, only_vocals_model: ModelSpec, overrides: dict[str, Any]
) -> None:
    with pytest.raises(StemSeparatorError):
        world.service.validate(world.request(**overrides))

    assert not world.out.exists()


def test_ca22_missing_output_dir_has_no_conflicts(world: World) -> None:
    # "out missing + conflict" cannot happen by construction: conflicts are only looked up
    # in an existing directory (architecture.md §3). If the order of VALIDATING is ever
    # changed so that out is created before the conflict check, this must still hold.
    assert not world.out.exists()

    world.service.validate(world.request())  # no OutputConflictError

    assert world.out.is_dir()
    assert list(world.out.iterdir()) == []


def test_ca22_unwritable_output_dir_fails_before_probing(world: World) -> None:
    world.out.mkdir()
    _make_unwritable_dir(world.out)
    try:
        with pytest.raises(OutputNotWritableError) as excinfo:
            world.service.validate(world.request())
    finally:
        world.out.chmod(stat.S_IRWXU)

    assert excinfo.value.exit_code == 3
    assert str(world.out) in excinfo.value.message
    assert world.count("probe") == 0


# --- CA-03 (U): no audio stream (RF-02) -------------------------------------------------


def test_ca03_no_audio_fails_on_probe_without_normalizing_or_measuring(world: World) -> None:
    world.probe.has_audio = False

    with pytest.raises(NoAudioStreamError) as excinfo:
        world.service.run(world.request(input_path=world.make_input("mudo.mkv")))

    assert excinfo.value.exit_code == 3
    assert world.count("normalize") == 0
    assert world.count("free_bytes") == 0
    assert world.out.is_dir()  # accepted exception to P8: created, left empty
    assert list(world.out.iterdir()) == []


# --- CA-21 (U): cover art in an audio file (RF-02) --------------------------------------


def test_ca21_mp3_with_cover_art_is_audio_and_validates(world: World) -> None:
    world.probe.codec = "mp3"
    world.probe.kind = MediaKind.AUDIO  # what the probe returns for attached_pic == 1
    request = world.request(input_path=world.make_input("portada.mp3"))

    info = world.service.validate(request)

    assert info.kind is MediaKind.AUDIO
    assert info.path == request.input_path


def test_validate_returns_the_probe_media_info(world: World) -> None:
    info = world.service.validate(world.request())

    assert info.duration_s == INPUT_DURATION_S
    assert world.input_probes() == [world.input]


# --- CA-17: disk space (RF-09, domain.md §5) --------------------------------------------


def test_ca17_temp_volume_short_of_space(world: World) -> None:
    world.system.free[world.systmp.resolve()] = 10_000_000

    with pytest.raises(InsufficientDiskSpaceError) as excinfo:
        world.service.validate(world.request())

    error = excinfo.value
    assert error.exit_code == 4
    assert "26,5 MB" in error.message  # needed: 26_460_000 bytes
    assert "10,0 MB" in error.message  # available
    assert str(world.systmp) in error.message


def test_ca17_output_volume_short_of_space(world: World) -> None:
    world.system.free[world.out.resolve()] = 10_000_000

    with pytest.raises(InsufficientDiskSpaceError) as excinfo:
        world.service.validate(world.request())

    error = excinfo.value
    assert error.exit_code == 4
    assert "26,5 MB" in error.message
    assert "10,0 MB" in error.message
    assert str(world.out) in error.message


def test_space_is_measured_on_system_temp_and_output_volumes(world: World) -> None:
    world.service.validate(world.request())

    measured = {call[1] for call in world.calls if call[0] == "free_bytes"}
    assert measured == {world.systmp.resolve(), world.out.resolve()}


@pytest.mark.parametrize("stems", [None, frozenset({StemName.VOCALS})], ids=["all", "one"])
def test_space_formula_exact_value(world: World, stems: frozenset[StemName] | None) -> None:
    # The estimate counts the model's stems, not the requested ones (domain.md §5).
    world.system.free[world.systmp.resolve()] = EXPECTED_NEEDED_BYTES
    world.system.free[world.out.resolve()] = EXPECTED_NEEDED_BYTES
    world.service.validate(world.request(stems=stems))  # exactly enough: passes

    world.system.free[world.systmp.resolve()] = EXPECTED_NEEDED_BYTES - 1
    with pytest.raises(InsufficientDiskSpaceError):
        world.service.validate(world.request(stems=stems))


# --- run() up to the end of PREPARING ---------------------------------------------------


def _run_until_not_implemented(world: World, request: SeparationRequest) -> None:
    with pytest.raises(NotImplementedError) as excinfo:
        world.service.run(request, world.reporter)
    assert type(excinfo.value) is NotImplementedError  # propagated as is, not wrapped


def test_run_calls_validate_once_and_repeats_no_check(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    validate_calls: list[SeparationRequest] = []
    original = world.service.validate

    def spy(request: SeparationRequest, reporter: Any = None) -> MediaInfo:
        validate_calls.append(request)
        return original(request, reporter)

    monkeypatch.setattr(world.service, "validate", spy)
    request = world.request()

    _run_until_not_implemented(world, request)

    assert validate_calls == [request]
    assert world.count("require_ffmpeg") == 1
    assert world.input_probes() == [world.input]
    assert world.count("free_bytes") == 2


def test_run_normalizes_then_probes_working_audio(world: World) -> None:
    _run_until_not_implemented(world, world.request())

    sequence = [c for c in world.calls if c[0] in {"normalize", "probe"}]
    assert [c[0] for c in sequence] == ["probe", "normalize", "probe"]
    _, src, dest = sequence[1]
    assert src == world.input
    assert dest.name == "source.wav"
    assert dest.parent.parent == world.systmp  # work_dir is a fresh dir in the system temp
    assert sequence[2] == ("probe", dest)


def test_run_removes_work_dir_even_on_not_implemented(world: World) -> None:
    _run_until_not_implemented(world, world.request())

    dest = next(c[2] for c in world.calls if c[0] == "normalize")
    assert not dest.parent.exists()
    assert list(world.systmp.iterdir()) == []


def test_run_notifies_validating_then_preparing(world: World) -> None:
    _run_until_not_implemented(world, world.request())

    assert world.stage_events() == [
        ("started", JobStage.VALIDATING),
        ("finished", JobStage.VALIDATING),
        ("started", JobStage.PREPARING),
        ("finished", JobStage.PREPARING),
    ]
    assert world.count("separate") == 0
    assert world.count("peak") == 0
    assert world.count("export") == 0


def test_stage_times_use_monotonic_clock(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = iter(range(0, 1000, 2))
    monkeypatch.setattr(time, "monotonic", lambda: float(next(ticks)))
    monkeypatch.setattr(time, "time", lambda: 0.0)  # would give 0.0 if used instead

    world.service.validate(world.request(), world.reporter)

    finished = [c for c in world.calls if c[0] == "finished"]
    assert finished == [("finished", JobStage.VALIDATING, 2.0)]


# --- stage_failed exactly once ----------------------------------------------------------


def _failed(world: World) -> list[tuple[Any, ...]]:
    return [c for c in world.calls if c[0] == "failed"]


@pytest.mark.parametrize("failure", ["ffmpeg", "probe"])
def test_run_reports_validating_failure_once(world: World, failure: str) -> None:
    expected: type[StemSeparatorError]
    if failure == "ffmpeg":
        world.system.ffmpeg_present = False
        expected = FFmpegNotFoundError
    else:
        world.probe.error = MediaReadError("No se pudo leer tema.wav.", "Comprueba el archivo.")
        expected = MediaReadError

    with pytest.raises(expected) as excinfo:
        world.service.run(world.request(), world.reporter)

    assert _failed(world) == [("failed", JobStage.VALIDATING, excinfo.value)]
    assert world.stage_events() == [
        ("started", JobStage.VALIDATING),
        ("failed", JobStage.VALIDATING),
    ]
    assert world.count("normalize") == 0


def test_run_reports_preparing_failure_once_and_stops(world: World) -> None:
    error = MediaReadError("No se pudo leer el archivo tema.wav.", "Comprueba el archivo.")
    world.normalizer.error = error

    with pytest.raises(MediaReadError) as excinfo:
        world.service.run(world.request(), world.reporter)

    assert excinfo.value is error
    assert world.stage_events() == [
        ("started", JobStage.VALIDATING),
        ("finished", JobStage.VALIDATING),
        ("started", JobStage.PREPARING),
        ("failed", JobStage.PREPARING),
    ]
    assert _failed(world) == [("failed", JobStage.PREPARING, error)]
    assert world.names()[-1] == "failed"  # nothing after the failure report
    assert world.input_probes() == [world.input]  # the working audio was never probed
    assert list(world.systmp.iterdir()) == []


def test_validate_alone_notifies_its_stage(world: World) -> None:
    world.service.validate(world.request(), world.reporter)

    assert world.stage_events() == [
        ("started", JobStage.VALIDATING),
        ("finished", JobStage.VALIDATING),
    ]


def test_validate_alone_reports_its_failure(world: World) -> None:
    world.system.ffmpeg_present = False

    with pytest.raises(FFmpegNotFoundError) as excinfo:
        world.service.validate(world.request(), world.reporter)

    assert _failed(world) == [("failed", JobStage.VALIDATING, excinfo.value)]


def test_validate_without_reporter(world: World) -> None:
    world.service.validate(world.request())

    assert world.stage_events() == []
