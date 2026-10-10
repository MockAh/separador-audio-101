"""CLI `separate` with CliRunner and doubles (T-12; interfaces.md §1 and §4, RNF-02, RNF-09).

No network, FFmpeg or engine. `cli.build_runtime` is replaced by a factory that hands the
command either the real SeparationService wired to the recording doubles of the service
tests (`World`), or a FakeService that returns a prepared result or raises. The runner
keeps stdout and stderr apart, so every test checks the stream it cares about on its own.
COLUMNS is wide so that Rich never wraps a long tmp path inside a table cell.
"""

import logging
import subprocess
import sys
from collections.abc import Callable, Iterator
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console
from typer.testing import CliRunner, Result

import stemseparator.cli as cli
from stemseparator.application.service import SeparationService
from stemseparator.domain.catalog import get_model
from stemseparator.domain.errors import (
    ExportError,
    OutputRestoreError,
)
from stemseparator.domain.models import (
    MediaInfo,
    MediaKind,
    OutputFormat,
    SeparationRequest,
    SeparationResult,
    StemFile,
    StemName,
)
from stemseparator.domain.progress import JobStage
from stemseparator.media.system import LocalSystemChecks
from tests.conftest import World

FFMPEG_LOGGER = "stemseparator.media.ffmpeg"
STAGE_LABELS = ("Validación", "Preparación", "Carga del modelo", "Separación", "Exportación")


# --- doubles and fixtures ----------------------------------------------------------------


class FakeService:
    """Stands in for SeparationService.run(): records the request, returns or raises."""

    def __init__(self) -> None:
        self.requests: list[SeparationRequest] = []
        self.result: SeparationResult | None = None
        self.error: BaseException | None = None
        self.during: Callable[[], None] | None = None
        self.factory: Factory | None = None  # the cli.build_runtime replacement serving it

    def run(self, request: SeparationRequest, reporter: Any = None) -> SeparationResult:
        self.requests.append(request)
        if self.during is not None:
            self.during()
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def make_result(
    out: Path,
    gain: float = 1.0,
    elapsed_s: float = 83.4,
    interrupted: bool = False,
) -> SeparationResult:
    def stem_file(stem: StemName, size: int) -> StemFile:
        return StemFile(
            stem=stem,
            path=out / f"tema_{stem.value}.wav",
            duration_s=12.48,
            sample_rate=44100,
            channels=2,
            size_bytes=size,
            peak=0.5,
        )

    return SeparationResult(
        input=MediaInfo(
            path=out.parent / "tema.wav",
            kind=MediaKind.AUDIO,
            duration_s=12.5,
            has_audio=True,
            sample_rate=44100,
            channels=2,
            codec="pcm_s16le",
        ),
        work_duration_s=12.48,
        model_id="vocals",
        stems=(stem_file(StemName.VOCALS, 1_234_567), stem_file(StemName.INSTRUMENTAL, 4_500_000)),
        output_gain=gain,
        elapsed_s=elapsed_s,
        interrupted_during_publish=interrupted,
    )


class Factory:
    """Replacement for cli.build_runtime: records the verbose flag it was called with."""

    def __init__(self, service: Any, model_dir: Path) -> None:
        self.service = service
        self.model_dir = model_dir
        self.verbose: list[bool] = []

    def __call__(self, verbose: bool) -> Any:
        self.verbose.append(verbose)
        return cli.Runtime(service=self.service, engine=None, model_dir=self.model_dir)


@pytest.fixture(autouse=True)
def logging_restored() -> Iterator[None]:
    """The command configures logging; put the loggers back as they were after each test."""
    root = logging.getLogger()
    ffmpeg_logger = logging.getLogger(FFMPEG_LOGGER)
    saved = (list(root.handlers), root.level, ffmpeg_logger.level)
    yield
    root.handlers[:] = saved[0]
    root.setLevel(saved[1])
    ffmpeg_logger.setLevel(saved[2])


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner(env={"COLUMNS": "250"})


@pytest.fixture
def world(world: World, monkeypatch: pytest.MonkeyPatch) -> World:
    """The shared World (tests/conftest.py), plugged into the command."""
    monkeypatch.setattr(cli, "build_runtime", Factory(world.service, world.model_dir))
    return world


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeService:
    service = FakeService()
    service.result = make_result(tmp_path / "out")
    service.factory = Factory(service, tmp_path / "models")
    monkeypatch.setattr(cli, "build_runtime", service.factory)
    return service


@pytest.fixture
def factory(fake: FakeService) -> Factory:
    assert fake.factory is not None
    return fake.factory


@pytest.fixture
def song(tmp_path: Path) -> Path:
    """An existing input with a supported extension, for the FakeService tests."""
    path = tmp_path / "tema.wav"
    path.write_bytes(b"not audio: the service is a double")
    return path


def separate(runner: CliRunner, *args: str | Path) -> Result:
    return runner.invoke(cli.app, ["separate", *map(str, args)])


# --- CA-01: successful run -------------------------------------------------------------


def test_ca01_exit_code_zero_and_both_stems_in_the_output(runner: CliRunner, world: World) -> None:
    result = separate(runner, world.input, "-o", world.out)

    assert result.exit_code == 0, result.stderr
    assert (world.out / "tema_vocals.wav").is_file()
    assert (world.out / "tema_instrumental.wav").is_file()


def test_progress_shows_every_stage_in_order_on_stdout(runner: CliRunner, world: World) -> None:
    result = separate(runner, world.input, "-o", world.out)

    positions = [result.stdout.find(label) for label in STAGE_LABELS]
    assert all(pos >= 0 for pos in positions), result.stdout
    assert positions == sorted(positions)


# --- CA-04, CA-05, CA-06, CA-07: errors from VALIDATING ----------------------------------


def test_ca04_missing_ffmpeg_exits_4_with_the_three_systems(
    runner: CliRunner, world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The input EXISTS and has a supported extension (note of 2026-10-03): otherwise
    # InputNotFoundError or UnsupportedFormatError would come first and this would pass
    # for the wrong reason. The message checked below only comes from require_ffmpeg().
    assert world.input.is_file() and world.input.suffix == ".wav"
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))
    service = SeparationService(
        engine=world.engine,
        probe=world.probe,
        normalizer=world.normalizer,
        exporter=world.exporter,
        system=LocalSystemChecks(),
        model_dir=world.model_dir,
    )
    monkeypatch.setattr(cli, "build_runtime", Factory(service, world.model_dir))

    result = separate(runner, world.input, "-o", world.out)

    assert result.exit_code == 4
    assert "No se encontraron ffmpeg ni ffprobe en el PATH." in result.stderr
    for system_name in ("Windows", "macOS", "Linux"):
        assert system_name in result.stderr
    assert "Traceback" not in result.stderr


def test_ca05_missing_input_exits_3_and_shows_the_path(
    runner: CliRunner, world: World, tmp_path: Path
) -> None:
    missing = tmp_path / "no" / "existe.wav"

    result = separate(runner, missing, "-o", world.out)

    assert result.exit_code == 3
    assert str(missing) in result.stderr
    assert not world.out.exists()


def test_ca06_unsupported_format_exits_3_and_lists_the_extensions(
    runner: CliRunner, world: World
) -> None:
    document = world.make_input("documento.txt")

    result = separate(runner, document, "-o", world.out)

    assert result.exit_code == 3
    for extension in (".wav", ".mp3", ".flac", ".m4a", ".mp4", ".mkv"):
        assert extension in result.stderr


def test_ca07_unknown_model_exits_3_and_suggests_models(runner: CliRunner, world: World) -> None:
    result = separate(runner, world.input, "-o", world.out, "--model", "no-existe")

    assert result.exit_code == 3
    assert "stemseparator models" in result.stderr
    assert world.count("separate") == 0  # nothing downloaded
    assert not world.out.exists()


# --- options → SeparationRequest ---------------------------------------------------------


def test_options_build_the_request(
    runner: CliRunner, fake: FakeService, song: Path, tmp_path: Path
) -> None:
    out = tmp_path / "stems"

    result = separate(runner, song, "-o", out, "-m", "vocals-max", "-f", "flac", "--overwrite")

    assert result.exit_code == 0, result.stderr
    (request,) = fake.requests
    assert request.input_path == song
    assert request.output_dir == out
    assert request.model_id == "vocals-max"
    assert request.output_format is OutputFormat.FLAC
    assert request.overwrite is True


def test_defaults_build_the_request(runner: CliRunner, fake: FakeService, song: Path) -> None:
    result = separate(runner, song)

    assert result.exit_code == 0, result.stderr
    (request,) = fake.requests
    assert request.output_dir.resolve() == Path.cwd().resolve()
    assert request.model_id is None
    assert request.stems is None
    assert request.output_format is OutputFormat.WAV
    assert request.overwrite is False


def test_unknown_format_is_a_typer_usage_error(
    runner: CliRunner, fake: FakeService, song: Path
) -> None:
    result = separate(runner, song, "--format", "xyz")

    assert result.exit_code == 2  # interfaces.md §4: usage errors are Typer's
    assert "xyz" in result.stderr  # rejected for the format, not for a missing command
    assert fake.requests == []


# --- --stems parsing (interfaces.md §1) ---------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("vocals", {StemName.VOCALS}),
        ("vocals,instrumental", {StemName.VOCALS, StemName.INSTRUMENTAL}),
        (" vocals , instrumental ", {StemName.VOCALS, StemName.INSTRUMENTAL}),
        ("vocals,vocals", {StemName.VOCALS}),
    ],
)
def test_stems_are_split_trimmed_and_deduplicated(
    runner: CliRunner, fake: FakeService, song: Path, value: str, expected: set[StemName]
) -> None:
    result = separate(runner, song, "--stems", value)

    assert result.exit_code == 0, result.stderr
    (request,) = fake.requests
    assert request.stems == frozenset(expected)


@pytest.mark.parametrize("value", ["drums", "Vocals", "vocals,drums"])
def test_unknown_stem_name_exits_3_not_2_and_lists_the_model_stems(
    runner: CliRunner, fake: FakeService, song: Path, value: str
) -> None:
    result = separate(runner, song, "--stems", value)

    assert result.exit_code == 3  # UnsupportedStemError, not a Typer usage error
    model = get_model(None)
    for stem in model.stems:
        assert stem.value in result.stderr
    assert fake.requests == []


@pytest.mark.parametrize("value", ["vocals,", "vocals,,instrumental", "vocals, ,instrumental"])
def test_empty_stem_item_is_an_unknown_name(
    runner: CliRunner, fake: FakeService, song: Path, value: str
) -> None:
    # interfaces.md v0.17: an empty item is not skipped; it is an unknown stem name.
    result = separate(runner, song, "--stems", value)

    assert result.exit_code == 3
    assert "instrumental" in result.stderr  # the message lists the model stems
    assert fake.requests == []


def test_unknown_stem_names_are_listed_once_in_order(
    runner: CliRunner, fake: FakeService, song: Path
) -> None:
    result = separate(runner, song, "--stems", "drums,bass,drums")

    assert result.exit_code == 3
    assert "drums, bass" in result.stderr
    assert result.stderr.count("drums") == 1


def test_unknown_stem_lists_the_stems_of_the_requested_model(
    runner: CliRunner, fake: FakeService, song: Path
) -> None:
    result = separate(runner, song, "-m", "vocals-max", "--stems", "drums")

    assert result.exit_code == 3
    assert "vocals-max" in result.stderr


def test_unknown_stem_wins_over_a_missing_input_and_notifies_no_stage(
    runner: CliRunner, world: World, tmp_path: Path
) -> None:
    # interfaces.md §1: a misspelt stem is an error of the invocation, like --format xyz.
    missing = tmp_path / "noexiste.wav"

    result = separate(runner, missing, "-o", world.out, "--stems", "drums")

    assert result.exit_code == 3
    assert "drums" in result.stderr
    assert str(missing) not in result.stderr
    assert world.stage_events() == []
    assert result.stdout == ""


def test_unknown_stem_with_unknown_model_reports_the_model(
    runner: CliRunner, fake: FakeService, song: Path
) -> None:
    result = separate(runner, song, "-m", "no-existe", "--stems", "drums")

    assert result.exit_code == 3
    assert "stemseparator models" in result.stderr
    assert fake.requests == []


# --- CA-13: summary table ----------------------------------------------------------------


def test_ca13_table_has_stem_path_duration_size_and_total_time(
    runner: CliRunner, fake: FakeService, song: Path
) -> None:
    result = separate(runner, song)

    assert result.exit_code == 0, result.stderr
    assert fake.result is not None
    for stem_file in fake.result.stems:
        assert stem_file.stem.value in result.stdout
        assert str(stem_file.path) in result.stdout
    assert "12,48" in result.stdout  # duration
    assert "1,2 MB" in result.stdout  # 1 234 567 bytes, decimal MB like the disk message
    assert "4,5 MB" in result.stdout
    assert "83,4" in result.stdout  # total time


def test_ca13_output_gain_as_factor_and_db_with_its_explanation(
    runner: CliRunner, fake: FakeService, song: Path, tmp_path: Path
) -> None:
    fake.result = make_result(tmp_path / "out", gain=0.9299)

    result = separate(runner, song)

    assert result.exit_code == 0, result.stderr
    assert "0,93" in result.stdout
    assert "−0,6 dB" in result.stdout  # 20·log10(0.9299) = −0.631
    assert "suma de los stems" in result.stdout
    assert "mezcla" in result.stdout


def test_ca13_unit_gain_is_zero_db_without_a_minus_sign(
    runner: CliRunner, fake: FakeService, song: Path
) -> None:
    result = separate(runner, song)

    assert "1,00" in result.stdout
    assert "0,0 dB" in result.stdout
    assert "−0,0" not in result.stdout
    assert "-0,0" not in result.stdout


def test_ca13_numbers_use_a_decimal_comma(
    runner: CliRunner, fake: FakeService, song: Path, tmp_path: Path
) -> None:
    fake.result = make_result(tmp_path / "out", gain=0.9299)

    result = separate(runner, song)

    assert result.exit_code == 0, result.stderr
    assert "0,93" in result.stdout  # the table is there: the dots are absent for a reason
    for dotted in ("12.48", "83.4", "0.93", "1.2 MB", "0.6 dB"):
        assert dotted not in result.stdout


# --- streams (interfaces.md §1) ------------------------------------------------------------


def test_success_writes_the_table_to_stdout_and_nothing_to_stderr(
    runner: CliRunner, fake: FakeService, song: Path
) -> None:
    result = separate(runner, song)

    assert result.exit_code == 0
    assert "tema_vocals.wav" in result.stdout
    assert "tema_vocals.wav" not in result.stderr
    assert result.stderr == ""


def test_error_message_and_hint_go_to_stderr_only(
    runner: CliRunner, fake: FakeService, song: Path
) -> None:
    fake.error = ExportError("Mensaje del fallo de exportación.", "Sugerencia para el usuario.")

    result = separate(runner, song)

    assert result.exit_code == 5
    assert "Mensaje del fallo de exportación." in result.stderr
    assert "Sugerencia para el usuario." in result.stderr
    assert "Mensaje del fallo de exportación." not in result.stdout
    assert "Sugerencia para el usuario." not in result.stdout


# --- exception → exit code (interfaces.md §4) ----------------------------------------------


def test_domain_error_exits_with_its_class_code_without_traceback(
    runner: CliRunner, fake: FakeService, song: Path
) -> None:
    try:
        raise OSError("disco lleno")
    except OSError as cause:
        error = ExportError("No se pudo exportar.", "Vuelve a intentarlo.")
        error.__cause__ = cause
    fake.error = error

    result = separate(runner, song)

    assert result.exit_code == 5
    assert "Traceback" not in result.stderr


def test_verbose_shows_the_traceback_of_a_domain_error(
    runner: CliRunner, fake: FakeService, song: Path
) -> None:
    try:
        raise OSError("disco lleno")
    except OSError as cause:
        error = ExportError("No se pudo exportar.", "Vuelve a intentarlo.")
        error.__cause__ = cause
    fake.error = error

    result = separate(runner, song, "--verbose")

    assert result.exit_code == 5
    assert "Traceback" in result.stderr
    assert "disco lleno" in result.stderr


def test_unexpected_error_exits_1_without_traceback(
    runner: CliRunner, fake: FakeService, song: Path
) -> None:
    fake.error = RuntimeError("algo inesperado")

    result = separate(runner, song)

    assert result.exit_code == 1
    assert "algo inesperado" in result.stderr
    assert "--verbose" in result.stderr
    assert "Traceback" not in result.stderr


def test_unexpected_error_with_verbose_shows_the_traceback(
    runner: CliRunner, fake: FakeService, song: Path
) -> None:
    fake.error = RuntimeError("algo inesperado")

    result = separate(runner, song, "-v")

    assert result.exit_code == 1
    assert "Traceback" in result.stderr
    assert "RuntimeError" in result.stderr


# --- CA-19: Ctrl+C ---------------------------------------------------------------------------


def test_ca19_keyboard_interrupt_exits_130_without_traceback(
    runner: CliRunner, fake: FakeService, song: Path
) -> None:
    fake.error = KeyboardInterrupt()

    result = separate(runner, song)

    assert result.exit_code == 130
    assert "Traceback" not in result.stderr
    assert "nterrump" in result.stderr  # "Interrumpido…" / "interrumpida…"


def test_ca19_interrupt_during_publish_exits_0_with_an_explicit_notice(
    runner: CliRunner, fake: FakeService, song: Path, tmp_path: Path
) -> None:
    fake.result = make_result(tmp_path / "out", interrupted=True)

    result = separate(runner, song)

    assert result.exit_code == 0
    assert "tema_vocals.wav" in result.stdout  # the job completed: the table is there
    notice = result.stderr.lower()
    assert "interrupción" in notice
    assert "publicación" in notice
    assert "complet" in notice


def test_ca19_no_notice_without_an_interrupt(
    runner: CliRunner, fake: FakeService, song: Path
) -> None:
    result = separate(runner, song)

    assert result.exit_code == 0, result.stderr
    assert "tema_vocals.wav" in result.stdout
    assert "interrupción" not in (result.stdout + result.stderr).lower()


# --- CA-24: restore failure ------------------------------------------------------------------

RESTORE_LINES = (
    "  /out/.stemseparator-tmp-ab12cd34-tema_vocals.wav.bak → tema_vocals.wav",
    "  /out/.stemseparator-tmp-ab12cd34-tema_instrumental.wav.bak → tema_instrumental.wav",
)


def _restore_error() -> OutputRestoreError:
    return OutputRestoreError(
        "No se pudieron restaurar tus archivos originales en /out. Están a salvo en copias "
        ".bak; renómbralas así:\n" + "\n".join(RESTORE_LINES),
        "Renombra cada copia .bak al nombre indicado antes de volver a ejecutar el comando.",
    )


def test_ca24_restore_failure_exits_6_with_every_file(
    runner: CliRunner, fake: FakeService, song: Path
) -> None:
    fake.error = _restore_error()

    result = separate(runner, song)

    assert result.exit_code == 6
    for line in RESTORE_LINES:
        assert line.strip() in result.stderr
    assert "Traceback" not in result.stderr


def test_ca24_restore_failure_after_an_interrupt_still_exits_6(
    runner: CliRunner, fake: FakeService, song: Path
) -> None:
    # Precedence 6 > 130 > 5 is decided in application/publication.py; the CLI only maps
    # the type of what it receives, even when a KeyboardInterrupt is its cause.
    error = _restore_error()
    error.__cause__ = KeyboardInterrupt()
    fake.error = error

    result = separate(runner, song)

    assert result.exit_code == 6


# --- --verbose: its three effects (interfaces.md §1) ---------------------------------------


def test_verbose_is_passed_to_the_runtime(runner: CliRunner, factory: Factory, song: Path) -> None:
    separate(runner, song)
    separate(runner, song, "--verbose")

    assert factory.verbose == [False, True]


@pytest.mark.parametrize("verbose", [False, True])
def test_build_runtime_builds_the_engine_with_verbose(
    monkeypatch: pytest.MonkeyPatch, verbose: bool
) -> None:
    built: list[bool] = []

    class RecordingEngine:
        def __init__(self, verbose: bool = False) -> None:
            built.append(verbose)

    monkeypatch.setattr(cli, "AudioSeparatorEngine", RecordingEngine)

    runtime = cli.build_runtime(verbose=verbose)

    assert built == [verbose]
    assert isinstance(runtime.engine, RecordingEngine)
    assert isinstance(runtime.service, SeparationService)


def _log_ffmpeg_stderr() -> None:
    ffmpeg_logger = logging.getLogger(FFMPEG_LOGGER)
    assert ffmpeg_logger.getEffectiveLevel() == logging.DEBUG
    ffmpeg_logger.debug("ffmpeg stderr: marcador-de-prueba")


def test_verbose_raises_the_ffmpeg_logger_to_debug_and_shows_it(
    runner: CliRunner, fake: FakeService, song: Path
) -> None:
    fake.during = _log_ffmpeg_stderr

    result = separate(runner, song, "--verbose")

    assert result.exit_code == 0, result.stderr
    assert "marcador-de-prueba" in result.stderr


def test_without_verbose_ffmpeg_debug_is_not_shown(
    runner: CliRunner, fake: FakeService, song: Path
) -> None:
    def log() -> None:
        logging.getLogger(FFMPEG_LOGGER).debug("ffmpeg stderr: marcador-de-prueba")

    fake.during = log

    result = separate(runner, song)

    assert result.exit_code == 0, result.stderr
    assert "marcador-de-prueba" not in result.stdout + result.stderr


# --- progress reporter: weights download notice (interfaces.md §1) ------------------------


def _reporter_output(model_dir: Path, terminal: bool = True) -> str:
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=terminal, width=120)
    reporter = cli.RichProgressReporter(console, model_dir=model_dir, model_id=None)
    reporter.stage_started(JobStage.LOADING_MODEL)
    reporter.stage_finished(JobStage.LOADING_MODEL, 1.5)
    return buffer.getvalue()


@pytest.mark.parametrize("terminal", [True, False], ids=["terminal", "redirected"])
def test_loading_model_says_the_weights_are_downloading_when_missing(
    tmp_path: Path, terminal: bool
) -> None:
    # Redirected there is no indicator to carry the notice: it becomes a plain line.
    output = _reporter_output(tmp_path / "models", terminal)

    assert "descarg" in output.lower()
    assert "%" not in output  # no invented percentage


@pytest.mark.parametrize("terminal", [True, False], ids=["terminal", "redirected"])
def test_loading_model_does_not_mention_a_download_when_present(
    tmp_path: Path, terminal: bool
) -> None:
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / get_model(None).engine_filename).write_bytes(b"weights")

    output = _reporter_output(model_dir, terminal)

    assert "descarg" not in output.lower()


def test_redirected_progress_is_one_line_per_stage_without_blank_lines(tmp_path: Path) -> None:
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / get_model(None).engine_filename).write_bytes(b"weights")

    output = _reporter_output(model_dir, terminal=False)

    assert output == "✓ Carga del modelo (1,5 s)\n"


def test_redirected_run_has_no_blank_lines_before_the_table(
    runner: CliRunner, world: World
) -> None:
    result = separate(runner, world.input, "-o", world.out)

    progress = result.stdout.split("┏")[0]
    assert progress.splitlines() == [line for line in progress.splitlines() if line.strip()]


# --- RNF-02: help in Spanish, including what Typer generates ------------------------------

ENGLISH_HELP = (
    "Usage",
    "Options",
    "Arguments",
    "Commands",
    "Show this message",
    "[default",
    "[required]",
    "[OPTIONS]",
    "COMMAND",
    "[ARGS]",
    "<path>",
    "<str>",
)


@pytest.mark.parametrize("args", [["--help"], ["separate", "--help"]], ids=["app", "separate"])
def test_help_is_in_spanish(runner: CliRunner, args: list[str]) -> None:
    result = runner.invoke(cli.app, args)

    assert result.exit_code == 0
    assert "Uso:" in result.stdout
    assert "Muestra este mensaje y termina." in result.stdout
    for english in ENGLISH_HELP:
        assert english not in result.stdout, english


def test_separate_help_documents_every_option(runner: CliRunner) -> None:
    result = runner.invoke(cli.app, ["separate", "--help"])

    for option in ("--output", "--model", "--stems", "--format", "--overwrite", "--verbose"):
        assert option in result.stdout
    assert "INPUT" in result.stdout


# --- __main__ ---------------------------------------------------------------------------------


def test_python_dash_m_runs_the_cli() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "stemseparator", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith("stemseparator ")
