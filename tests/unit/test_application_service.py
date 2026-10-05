"""SeparationService with test doubles (T-08, T-09; domain.md §4-§6).

No network, FFmpeg or engine. Every double appends to one shared call log, so the tests
check both what is called, in which order, and what is NOT called. The system temp
directory is pinned to tmp_path/"systmp" through `tempfile.tempdir`, so the temp volume
that VALIDATING measures and the work directory that run() creates are both observable.
"""

import os
import shutil
import stat
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from stemseparator.application import service as service_module
from stemseparator.application.service import SeparationService
from stemseparator.domain import catalog
from stemseparator.domain.errors import (
    EngineError,
    ExportError,
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
    DURATION_TOLERANCE_S,
    MediaInfo,
    MediaKind,
    ModelSpec,
    OutputFormat,
    SeparationRequest,
    SeparationResult,
    StemName,
)
from stemseparator.domain.progress import JobStage
from tests.conftest import FakeEngine

Calls = list[tuple[Any, ...]]

SUPPORTED_EXTENSIONS = (".wav", ".mp3", ".flac", ".m4a", ".mp4", ".mkv")
INPUT_DURATION_S = 12.5
# Working audio duration, deliberately different from the input one: the stems are checked
# against this one only (domain.md §6).
WORK_DURATION_S = 12.48
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
    def __init__(self, calls: Calls, exported: dict[Path, StemName]) -> None:
        self.calls = calls
        self.has_audio = True
        self.kind = MediaKind.AUDIO
        self.codec = "pcm_s16le"
        self.error: StemSeparatorError | None = None
        self.exported = exported  # filled by FakeExporter.export()
        self.duration_offset: dict[StemName, float] = {}  # exported duration - work duration

    def probe(self, path: Path) -> MediaInfo:
        self.calls.append(("probe", path))
        if path.name == "source.wav":  # the working audio written by normalize()
            return MediaInfo(
                path=path,
                kind=MediaKind.AUDIO,
                duration_s=WORK_DURATION_S,
                has_audio=True,
                sample_rate=44100,
                channels=2,
                codec="pcm_f32le",
            )
        if path in self.exported:  # a stem written by export()
            stem = self.exported[path]
            return MediaInfo(
                path=path,
                kind=MediaKind.AUDIO,
                duration_s=WORK_DURATION_S + self.duration_offset.get(stem, 0.0),
                has_audio=True,
                sample_rate=44100,
                channels=2,
                codec="exported",
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
    """Peaks come by engine output name (`<stem>.wav`); export() writes `dest`."""

    def __init__(self, calls: Calls, exported: dict[Path, StemName]) -> None:
        self.calls = calls
        self.exported = exported
        self.peaks: dict[StemName, float] = {StemName.VOCALS: 0.5, StemName.INSTRUMENTAL: 0.5}
        self.fail_on_export: int | None = None  # 1-based number of the export() call
        self.error = ExportError("No se pudo exportar el stem.", "Vuelve a intentarlo.")

    def peak(self, src_wav: Path) -> float:
        self.calls.append(("peak", src_wav))
        return self.peaks[StemName(src_wav.stem)]

    def export(self, src_wav: Path, dest: Path, fmt: OutputFormat, gain: float) -> Path:
        self.calls.append(("export", src_wav, dest, fmt, gain))
        if self.fail_on_export == sum(1 for c in self.calls if c[0] == "export"):
            raise self.error
        dest.write_bytes(b"exported " + src_wav.name.encode())  # fails if the dir is missing
        self.exported[dest] = StemName(src_wav.stem)
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


class World:
    """Service wired to recording doubles, plus the paths of one test."""

    def __init__(self, tmp_path: Path, systmp: Path) -> None:
        self.calls: Calls = []
        self.exported: dict[Path, StemName] = {}
        self.reporter = RecordingReporter(self.calls)
        self.probe = FakeProbe(self.calls, self.exported)
        self.normalizer = FakeNormalizer(self.calls)
        self.exporter = FakeExporter(self.calls, self.exported)
        self.system = FakeSystem(self.calls)
        self.engine = FakeEngine(self.calls)
        self.model_dir = tmp_path / "models"
        self.service = SeparationService(
            engine=self.engine,
            probe=self.probe,
            normalizer=self.normalizer,
            exporter=self.exporter,
            system=self.system,
            model_dir=self.model_dir,
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

    def of(self, name: str) -> list[tuple[Any, ...]]:
        return [call for call in self.calls if call[0] == name]

    def work_audio(self) -> Path:
        """Path of the working audio passed to normalize(); its parent is work_dir."""
        dest: Path = self.of("normalize")[0][2]
        return dest

    def out_files(self) -> set[str]:
        return {p.name for p in self.out.iterdir()} if self.out.exists() else set()


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


# --- run(): VALIDATING and PREPARING inside a complete job -----------------------------


def _run(world: World, request: SeparationRequest | None = None) -> SeparationResult:
    return world.service.run(request or world.request(), world.reporter)


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

    _run(world, request)

    assert validate_calls == [request]
    assert world.count("require_ffmpeg") == 1
    assert world.input_probes() == [world.input]
    assert world.count("free_bytes") == 2


def test_run_normalizes_then_probes_working_audio(world: World) -> None:
    _run(world)

    sequence = [c for c in world.calls if c[0] in {"normalize", "probe", "separate"}]
    assert [c[0] for c in sequence[:4]] == ["probe", "normalize", "probe", "separate"]
    _, src, dest = sequence[1]
    assert src == world.input
    assert dest.name == "source.wav"
    assert dest.parent.parent == world.systmp  # work_dir is a fresh dir in the system temp
    assert sequence[2] == ("probe", dest)
    assert sequence[3][1] == dest  # the engine separates the working audio, not the input


def test_run_removes_work_dir_on_success(world: World) -> None:
    _run(world)

    assert not world.work_audio().parent.exists()
    assert list(world.systmp.iterdir()) == []


def test_stage_times_use_monotonic_clock(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = iter(range(0, 1000, 2))
    monkeypatch.setattr(time, "monotonic", lambda: float(next(ticks)))
    monkeypatch.setattr(time, "time", lambda: 0.0)  # would give 0.0 if used instead

    world.service.validate(world.request(), world.reporter)

    finished = [c for c in world.calls if c[0] == "finished"]
    assert finished == [("finished", JobStage.VALIDATING, 2.0)]


def test_elapsed_uses_monotonic_clock(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = iter(range(0, 1000, 2))
    monkeypatch.setattr(time, "monotonic", lambda: float(next(ticks)))
    monkeypatch.setattr(time, "time", lambda: 0.0)  # would give 0.0 if used instead

    result = _run(world)

    assert result.elapsed_s > 0
    assert result.elapsed_s == int(result.elapsed_s)  # a difference of the fake ticks


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


# === T-09: separation, export and progress (domain.md §4 EXPORTING, §6) ================

STEM_ORDER = (StemName.VOCALS, StemName.INSTRUMENTAL)  # model order of `vocals`
SUCCESS_EVENTS = [
    (event, stage)
    for stage in (
        JobStage.VALIDATING,
        JobStage.PREPARING,
        JobStage.LOADING_MODEL,
        JobStage.SEPARATING,
        JobStage.EXPORTING,
    )
    for event in ("started", "finished")
]


def _engine_dir(world: World) -> Path:
    work_dir: Path = world.of("separate")[0][3]
    return work_dir


def _assert_clean_failure(world: World, before: set[str]) -> None:
    """CA-12: no new file in the output directory and the work directory removed."""
    assert world.out_files() == before
    assert list(world.systmp.iterdir()) == []


def _seconds(value: float) -> str:
    return f"{value:.3f}".replace(".", ",")


# --- engine call -------------------------------------------------------------------------


def test_engine_gets_working_audio_model_work_dir_model_dir_and_reporter(world: World) -> None:
    _run(world)

    [(_, audio_path, model, work_dir, model_dir, reporter)] = world.of("separate")
    assert audio_path == world.work_audio()
    assert model == catalog.get_model(None)
    assert work_dir == world.work_audio().parent / "engine"  # architecture.md §3
    assert model_dir == world.model_dir
    assert reporter is world.reporter


# --- CA-01 (U) / CA-02 (U): complete job (RF-04, RF-06, RF-13) --------------------------


def test_ca01_valid_request_returns_both_stems_in_output_dir(world: World) -> None:
    result = _run(world)

    assert result.model_id == "vocals"
    assert result.input.path == world.input  # VALIDATING's MediaInfo, not the working audio
    assert result.input.duration_s == INPUT_DURATION_S
    assert result.work_duration_s == WORK_DURATION_S  # PREPARING probe, not the input one
    assert result.elapsed_s > 0
    assert [f.stem for f in result.stems] == list(STEM_ORDER)
    for stem_file in result.stems:
        assert stem_file.path == world.out / f"tema_{stem_file.stem.value}.wav"
        assert stem_file.path.is_file()
        assert stem_file.duration_s == WORK_DURATION_S  # probe of the exported file
        assert stem_file.sample_rate == 44100
        assert stem_file.channels == 2
        assert stem_file.size_bytes == stem_file.path.stat().st_size > 0
    assert world.out_files() == {"tema_vocals.wav", "tema_instrumental.wav"}
    assert list(world.systmp.iterdir()) == []


def test_ca02_video_input_publishes_stems_and_leaves_no_temp(world: World) -> None:
    world.probe.kind = MediaKind.VIDEO
    request = world.request(input_path=world.make_input("clip.mp4"))

    result = _run(world, request)

    assert result.input.kind is MediaKind.VIDEO
    assert result.work_duration_s == WORK_DURATION_S
    assert world.out_files() == {"clip_vocals.wav", "clip_instrumental.wav"}
    assert list(world.systmp.iterdir()) == []


def test_exports_engine_stems_into_work_dir_export(world: World) -> None:
    _run(world)

    engine_dir = _engine_dir(world)
    export_dir = world.work_audio().parent / "export"  # architecture.md §3
    exports = world.of("export")
    assert [(c[1], c[2]) for c in exports] == [
        (engine_dir / "vocals.wav", export_dir / "tema_vocals.wav"),
        (engine_dir / "instrumental.wav", export_dir / "tema_instrumental.wav"),
    ]


# --- CA-09: partial selection (RF-04) ----------------------------------------------------


def test_ca09_only_requested_stem_is_exported_and_published(world: World) -> None:
    result = _run(world, world.request(stems=frozenset({StemName.VOCALS})))

    assert [c[2].name for c in world.of("export")] == ["tema_vocals.wav"]
    assert [f.stem for f in result.stems] == [StemName.VOCALS]
    assert world.out_files() == {"tema_vocals.wav"}


# --- CA-10 (U): output format (RF-06) ----------------------------------------------------


@pytest.mark.parametrize("fmt", [OutputFormat.FLAC, OutputFormat.MP3])
def test_ca10_format_reaches_exporter_and_extension(world: World, fmt: OutputFormat) -> None:
    result = _run(world, world.request(output_format=fmt))

    assert [c[3] for c in world.of("export")] == [fmt, fmt]
    assert world.out_files() == {f"tema_vocals.{fmt.value}", f"tema_instrumental.{fmt.value}"}
    assert all(f.path.suffix == f".{fmt.value}" for f in result.stems)


# --- output names: one function for publication and conflicts ----------------------------


def test_published_names_are_the_ones_conflict_detection_looks_for(world: World) -> None:
    request = world.request(input_path=world.make_input("mi.tema.v2.WAV"))
    first = _run(world, request)
    published = sorted(f.path.name for f in first.stems)
    assert published == ["mi.tema.v2_instrumental.wav", "mi.tema.v2_vocals.wav"]

    with pytest.raises(OutputConflictError) as excinfo:
        world.service.run(request)

    for name in published:
        assert name in excinfo.value.message


def test_publication_and_conflicts_share_one_naming_function(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    # If either site computed `<input>_<stem>.<ext>` on its own, it would not see the patch.
    def fake_name(input_path: Path, stem: StemName, fmt: OutputFormat) -> str:
        return f"zz-{stem.value}.{fmt.value}"

    monkeypatch.setattr(service_module, "output_filename", fake_name)
    _run(world)
    assert world.out_files() == {"zz-vocals.wav", "zz-instrumental.wav"}

    with pytest.raises(OutputConflictError) as excinfo:
        world.service.run(world.request())

    assert "zz-vocals.wav" in excinfo.value.message
    assert "zz-instrumental.wav" in excinfo.value.message


# --- CA-25 (U, service part): common output gain (RF-06, RF-08) --------------------------


def test_ca25_gain_from_highest_peak_is_passed_to_every_export(world: World) -> None:
    world.exporter.peaks = {StemName.VOCALS: 1.2, StemName.INSTRUMENTAL: 0.5}
    expected = 0.999 / 1.2  # by hand: OUTPUT_PEAK_CEILING / highest engine peak

    result = _run(world)

    gains = [c[4] for c in world.of("export")]
    assert len(gains) == 2
    assert gains == [result.output_gain, result.output_gain]  # the SAME gain for each stem
    assert result.output_gain == pytest.approx(expected, rel=1e-12)


def test_ca25_gain_ignores_stems_selection(world: World) -> None:
    world.exporter.peaks = {StemName.VOCALS: 0.5, StemName.INSTRUMENTAL: 1.2}

    result = _run(world, world.request(stems=frozenset({StemName.VOCALS})))

    engine_dir = _engine_dir(world)
    assert sorted(c[1] for c in world.of("peak")) == sorted(
        [engine_dir / "vocals.wav", engine_dir / "instrumental.wav"]
    )
    assert [c[4] for c in world.of("export")] == [result.output_gain]
    assert result.output_gain == pytest.approx(0.999 / 1.2, rel=1e-12)


@pytest.mark.parametrize("peaks", [(0.999, 0.3), (0.5, 0.5)], ids=["at-ceiling", "below-ceiling"])
def test_ca25_peaks_at_or_below_ceiling_give_unity_gain(
    world: World, peaks: tuple[float, float]
) -> None:
    world.exporter.peaks = dict(zip(STEM_ORDER, peaks, strict=True))

    result = _run(world)

    assert result.output_gain == 1.0
    assert [c[4] for c in world.of("export")] == [1.0, 1.0]


def test_ca25_silent_stems_give_unity_gain(world: World) -> None:
    world.exporter.peaks = {StemName.VOCALS: 0.0, StemName.INSTRUMENTAL: 0.0}

    result = _run(world)  # no ZeroDivisionError (interfaces.md v0.12)

    assert result.output_gain == 1.0
    assert [c[4] for c in world.of("export")] == [1.0, 1.0]
    assert [f.peak for f in result.stems] == [0.0, 0.0]


def test_ca25_stem_peak_is_computed_not_measured_on_exported_files(world: World) -> None:
    world.exporter.peaks = {StemName.VOCALS: 1.2, StemName.INSTRUMENTAL: 0.5}

    result = _run(world)

    peaks = {f.stem: f.peak for f in result.stems}
    assert peaks[StemName.VOCALS] == pytest.approx(1.2 * result.output_gain, rel=1e-12)
    assert peaks[StemName.INSTRUMENTAL] == pytest.approx(0.5 * result.output_gain, rel=1e-12)
    measured = [c[1] for c in world.of("peak")]
    assert len(measured) == 2
    assert all(path.parent == _engine_dir(world) for path in measured)
    assert not set(measured) & set(world.exported)  # never on an exported file


# --- duration check at run time (RF-06, acceptance.md §1) --------------------------------


@pytest.mark.parametrize("sign", [1, -1], ids=["longer", "shorter"])
@pytest.mark.parametrize("fmt", list(OutputFormat))
def test_duration_one_ms_inside_tolerance_passes(
    world: World, fmt: OutputFormat, sign: int
) -> None:
    world.probe.duration_offset[StemName.INSTRUMENTAL] = sign * (DURATION_TOLERANCE_S[fmt] - 0.001)

    result = _run(world, world.request(output_format=fmt))

    assert len(result.stems) == 2


@pytest.mark.parametrize("sign", [1, -1], ids=["longer", "shorter"])
@pytest.mark.parametrize("fmt", list(OutputFormat))
def test_duration_one_ms_outside_tolerance_fails(
    world: World, fmt: OutputFormat, sign: int
) -> None:
    tolerance = DURATION_TOLERANCE_S[fmt]
    measured = WORK_DURATION_S + sign * (tolerance + 0.001)
    world.probe.duration_offset[StemName.INSTRUMENTAL] = sign * (tolerance + 0.001)

    with pytest.raises(ExportError) as excinfo:
        _run(world, world.request(output_format=fmt))

    error = excinfo.value
    assert error.exit_code == 5
    assert "instrumental" in error.message
    assert _seconds(measured) in error.message
    assert _seconds(WORK_DURATION_S) in error.message
    assert _seconds(tolerance) in error.message


# --- CA-12 without --overwrite (RF-06, RF-13, ADR-004) -----------------------------------


@pytest.fixture
def out_before(world: World) -> set[str]:
    """Output directory with an unrelated file, so "no new file" is checked, not "empty"."""
    world.out.mkdir()
    (world.out / "otro.txt").write_bytes(b"del usuario")
    return world.out_files()


def test_ca12_engine_fails_while_separating(world: World, out_before: set[str]) -> None:
    world.engine.fail_at = JobStage.SEPARATING

    with pytest.raises(EngineError) as excinfo:
        _run(world)

    assert excinfo.value.exit_code == 5
    assert world.count("export") == 0
    _assert_clean_failure(world, out_before)


def test_ca12_exporter_fails_on_second_stem(world: World, out_before: set[str]) -> None:
    world.exporter.fail_on_export = 2

    with pytest.raises(ExportError) as excinfo:
        _run(world)

    assert excinfo.value.exit_code == 5
    assert world.count("export") == 2
    _assert_clean_failure(world, out_before)
    assert (world.out / "otro.txt").read_bytes() == b"del usuario"


def test_ca12_stem_out_of_tolerance_publishes_nothing(world: World, out_before: set[str]) -> None:
    # The SECOND stem is out of tolerance: the first, already checked, must not be published.
    world.probe.duration_offset[StemName.INSTRUMENTAL] = 1.0

    with pytest.raises(ExportError) as excinfo:
        _run(world)

    assert excinfo.value.exit_code == 5
    _assert_clean_failure(world, out_before)


def test_ca12_failed_second_move_withdraws_the_first(
    world: World, out_before: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    real_move = shutil.move
    moves: list[Path] = []

    def flaky_move(src: Any, dst: Any, *args: Any, **kwargs: Any) -> Any:
        moves.append(Path(dst))
        if len(moves) == 2:
            raise OSError("simulated failure while moving the second stem")
        return real_move(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "move", flaky_move)

    with pytest.raises(ExportError) as excinfo:
        _run(world)

    assert len(moves) == 2  # the first stem WAS published before the failure
    assert excinfo.value.exit_code == 5
    assert isinstance(excinfo.value.__cause__, OSError)
    _assert_clean_failure(world, out_before)


def test_ca12_partial_file_left_by_a_failed_move_is_withdrawn(
    world: World, out_before: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A move across volumes copies then deletes: a failed copy can leave a partial dest.
    def partial_move(src: Any, dst: Any, *args: Any, **kwargs: Any) -> Any:
        Path(dst).write_bytes(b"partial")
        raise OSError("simulated failure in the middle of the copy")

    monkeypatch.setattr(shutil, "move", partial_move)

    with pytest.raises(ExportError):
        _run(world)

    _assert_clean_failure(world, out_before)


# --- CA-13 (U): exact reporter sequence (RF-08) ------------------------------------------


def test_ca13_success_sequence(world: World) -> None:
    _run(world)

    events = world.stage_events()
    assert events == SUCCESS_EVENTS
    assert events[-1] == ("finished", JobStage.EXPORTING)
    stages = {call[1] for call in world.calls if call[0] in {"started", "finished", "failed"}}
    assert JobStage.DONE not in stages
    assert JobStage.FAILED not in stages
    assert world.count("separate") == 1
    assert world.count("peak") == 2
    assert world.count("export") == 2


def _break_stage(world: World, stage: JobStage) -> None:
    if stage is JobStage.VALIDATING:
        world.system.ffmpeg_present = False
    elif stage is JobStage.PREPARING:
        world.normalizer.error = MediaReadError("No se pudo leer tema.wav.", "Revísalo.")
    elif stage in (JobStage.LOADING_MODEL, JobStage.SEPARATING):
        world.engine.fail_at = stage
    else:
        world.exporter.fail_on_export = 1


@pytest.mark.parametrize(
    "stage",
    [
        JobStage.VALIDATING,
        JobStage.PREPARING,
        JobStage.LOADING_MODEL,
        JobStage.SEPARATING,
        JobStage.EXPORTING,
    ],
)
def test_ca13_failure_reported_once_by_its_stage(world: World, stage: JobStage) -> None:
    _break_stage(world, stage)

    with pytest.raises(StemSeparatorError) as excinfo:
        _run(world)

    started = SUCCESS_EVENTS.index(("started", stage))
    assert world.stage_events() == [*SUCCESS_EVENTS[: started + 1], ("failed", stage)]
    assert _failed(world) == [("failed", stage, excinfo.value)]  # once, with that error
    assert list(world.systmp.iterdir()) == []


@pytest.mark.parametrize(
    "kind", ["duration", "move"], ids=["duration-out-of-tolerance", "move-fails"]
)
def test_ca13_late_export_failures_are_reported_as_exporting(
    world: World, kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    if kind == "duration":
        world.probe.duration_offset[StemName.VOCALS] = 1.0
    else:

        def failing_move(src: Any, dst: Any, *args: Any, **kwargs: Any) -> Any:
            raise OSError("simulated move failure")

        monkeypatch.setattr(shutil, "move", failing_move)

    with pytest.raises(ExportError) as excinfo:
        _run(world)

    assert world.stage_events()[-2:] == [
        ("started", JobStage.EXPORTING),
        ("failed", JobStage.EXPORTING),
    ]
    assert _failed(world) == [("failed", JobStage.EXPORTING, excinfo.value)]
