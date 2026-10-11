"""Command-line interface (interfaces.md §1). `separate` in T-12; `models` and `doctor` in T-13.

The composition of the real adapters (`build_runtime`) is kept apart from the command, so
the tests replace it with doubles. Importing this module loads the engine adapter, never
the audio stack behind it: `AudioSeparatorEngine` imports audio_separator lazily.

Streams (interfaces.md §1): progress and the summary table go to stdout; error messages,
hints, tracebacks, warnings and logs go to stderr.
"""

import importlib.metadata
import importlib.util
import logging
import math
import os
import platform
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text
from typer import rich_utils
from typer.core import TyperCommand, TyperGroup

from stemseparator import __version__
from stemseparator.application.service import SeparationService
from stemseparator.domain.catalog import get_model, list_models
from stemseparator.domain.errors import (
    FFmpegNotFoundError,
    StemSeparatorError,
    UnsupportedStemError,
)
from stemseparator.domain.models import (
    ModelSpec,
    OutputFormat,
    SeparationRequest,
    SeparationResult,
    StemName,
)
from stemseparator.domain.progress import JobStage
from stemseparator.engines.audio_separator_engine import AudioSeparatorEngine
from stemseparator.media.ffmpeg import (
    FFmpegAudioNormalizer,
    FFmpegStemExporter,
    FFprobeMediaProbe,
)
from stemseparator.media.system import LocalSystemChecks, default_model_dir

FFMPEG_LOGGER = "stemseparator.media.ffmpeg"  # carries FFmpeg's stderr (ADR-003)
EXIT_INTERRUPTED = 130
EXIT_UNEXPECTED = 1
EXIT_MISSING_DEPENDENCY = FFmpegNotFoundError.exit_code  # 4, also for audio-separator

# --- Spanish help (RNF-02) ----------------------------------------------------------------
# Typer renders these at call time from module globals; usage errors stay in Typer's
# English (T-12 closing note).
rich_utils.ARGUMENTS_PANEL_TITLE = "Argumentos"
rich_utils.OPTIONS_PANEL_TITLE = "Opciones"
rich_utils.COMMANDS_PANEL_TITLE = "Comandos"
rich_utils.DEFAULT_STRING = "[por defecto: {}]"
rich_utils.REQUIRED_LONG_STRING = "[obligatorio]"
_HELP_OPTION_TEXT = "Muestra este mensaje y termina."


def _spanish_usage(usage: str) -> str:
    return usage.replace("Usage:", "Uso:", 1)


class _SpanishGroup(TyperGroup):
    def get_help_option(self, ctx: Any) -> Any:
        option = super().get_help_option(ctx)
        if option is not None:
            option.help = _HELP_OPTION_TEXT
        return option

    def get_usage(self, ctx: Any) -> str:
        return _spanish_usage(super().get_usage(ctx))


class _SpanishCommand(TyperCommand):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for param in self.params:  # an argument shows its type name in the help: "<path>"
            if param.type.name == "path":
                param.type.name = "ruta"

    def get_help_option(self, ctx: Any) -> Any:
        option = super().get_help_option(ctx)
        if option is not None:
            option.help = _HELP_OPTION_TEXT
        return option

    def get_usage(self, ctx: Any) -> str:
        return _spanish_usage(super().get_usage(ctx))


app = typer.Typer(
    name="stemseparator",
    help="Separa un archivo de audio o video en pistas (stems). Solo CPU.",
    no_args_is_help=True,
    add_completion=False,
    cls=_SpanishGroup,
    options_metavar="[OPCIONES]",
    subcommand_metavar="COMANDO [ARGUMENTOS]...",
)


# --- composition --------------------------------------------------------------------------


@dataclass(frozen=True)
class Runtime:
    """What the command needs from the composition: the service and where the weights live.

    `engine` is the same instance the service uses; doctor (T-13) asks it for the device.
    """

    service: SeparationService
    engine: AudioSeparatorEngine
    model_dir: Path


def build_runtime(verbose: bool) -> Runtime:
    """Compose the real adapters. Cheap: builds objects, imports no audio stack."""
    engine = AudioSeparatorEngine(verbose=verbose)
    model_dir = default_model_dir()
    service = SeparationService(
        engine=engine,
        probe=FFprobeMediaProbe(),
        normalizer=FFmpegAudioNormalizer(),
        exporter=FFmpegStemExporter(),
        system=LocalSystemChecks(),
        model_dir=model_dir,
    )
    return Runtime(service=service, engine=engine, model_dir=model_dir)


# --- formatting (decimal comma, as every user-facing number) ------------------------------


def _decimal(value: float, digits: int) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def _megabytes(size_bytes: int) -> str:
    return f"{_decimal(size_bytes / 1_000_000, 1)} MB"


def _gain(gain: float) -> str:
    """Factor and dB, e.g. "0,93 (−0,6 dB)"; a gain of 1 is "1,00 (0,0 dB)", never −0,0."""
    db = round(20 * math.log10(gain), 1) if gain > 0 else -math.inf
    sign = "−" if db < 0 else ""
    return f"{_decimal(gain, 2)} ({sign}{_decimal(abs(db), 1)} dB)"


# --- models (interfaces.md §1) ------------------------------------------------------------

SONG_S = 210  # the 3:30 song every cost is translated to (RF-10)
_FIELD_WIDTH = len("Coste en CPU: ")


def cpu_cost(cpu_rtf: float) -> str:
    """Real-time factor and time for a 3:30 song, e.g. "≈2,4× (unos 8 min)".

    Minutes rounded to the nearest (,5 up); hours and minutes from 60 min.
    """
    minutes = math.floor(cpu_rtf * SONG_S / 60 + 0.5)
    if minutes == 0:
        estimate = "menos de 1 min"
    elif minutes < 60:
        estimate = f"unos {minutes} min"
    else:
        hours, rest = divmod(minutes, 60)
        estimate = f"unas {hours} h" + (f" {rest} min" if rest else "")
    return f"≈{_decimal(cpu_rtf, 1)}× ({estimate})"


def _model_block(model: ModelSpec, model_dir: Path) -> list[str]:
    downloaded = (model_dir / model.engine_filename).is_file()
    fields = (
        ("Stems:", ", ".join(model.stems)),
        ("Coste en CPU:", cpu_cost(model.cpu_rtf)),
        ("Licencia:", model.weights_license),
        ("Descargado:", "sí" if downloaded else "no"),
    )
    head = model.id + (" (por defecto)" if model.is_default else "")
    return [head, model.description, *(f"  {k:<{_FIELD_WIDTH}}{v}" for k, v in fields)]


# --- progress -----------------------------------------------------------------------------

_STAGE_LABELS = {
    JobStage.VALIDATING: "Validación",
    JobStage.PREPARING: "Preparación",
    JobStage.LOADING_MODEL: "Carga del modelo",
    JobStage.SEPARATING: "Separación",
    JobStage.EXPORTING: "Exportación",
}
_DOWNLOADING = "descargando los pesos del modelo; la primera vez puede tardar varios minutos"


class RichProgressReporter:
    """ProgressReporter: one stage at a time, with an activity indicator and elapsed time.

    The indicator is transient; each finished stage leaves one line with its time. Rich does
    not take over sys.stdout/sys.stderr while it is shown, so logs keep going to stderr.
    Redirected (not a terminal) there is no indicator, which would leave a blank line per
    stage; the download notice, which has no indicator to ride on, becomes a plain line.
    """

    def __init__(self, console: Console, model_dir: Path, model_id: str | None) -> None:
        self._console = console
        self._model_dir = model_dir
        self._model_id = model_id
        self._progress: Progress | None = None

    def stage_started(self, stage: JobStage) -> None:
        self.close()
        description = _STAGE_LABELS[stage]
        if stage is JobStage.LOADING_MODEL and self._weights_missing():
            description += f" ({_DOWNLOADING})"
            if not self._console.is_terminal:
                self._console.print(f"… {description}", markup=False)
        if not self._console.is_terminal:
            return
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}", markup=False),
            TimeElapsedColumn(),
            console=self._console,
            transient=True,
            redirect_stdout=False,
            redirect_stderr=False,
        )
        self._progress.add_task(description, total=None)
        self._progress.start()

    def stage_finished(self, stage: JobStage, elapsed_s: float) -> None:
        self.close()
        self._console.print(f"✓ {_STAGE_LABELS[stage]} ({_decimal(elapsed_s, 1)} s)", markup=False)

    def stage_failed(self, stage: JobStage, error: BaseException) -> None:
        self.close()
        self._console.print(f"✗ {_STAGE_LABELS[stage]}", markup=False)

    def close(self) -> None:
        """Stop the indicator if it is still shown (e.g. Ctrl+C between two stages)."""
        if self._progress is not None:
            self._progress.stop()
            self._progress = None

    def _weights_missing(self) -> bool:
        # VALIDATING already resolved this id: get_model cannot raise here.
        return not (self._model_dir / get_model(self._model_id).engine_filename).is_file()


# --- --stems (interfaces.md §1) -----------------------------------------------------------


def parse_stems(value: str | None, model_id: str | None) -> frozenset[StemName] | None:
    """Split by commas, trim, collapse duplicates keeping the order, map to StemName.

    Exact, case-sensitive match. An unknown or empty name raises UnsupportedStemError
    listing the stems of the model (UnknownModelError first, if the model does not exist).
    """
    if value is None:
        return None
    names = list(dict.fromkeys(part.strip() for part in value.split(",")))
    valid = {stem.value for stem in StemName}
    unknown = [name for name in names if name not in valid]
    if unknown:
        model = get_model(model_id)
        shown = ", ".join(name or "(elemento vacío)" for name in unknown)
        raise UnsupportedStemError(
            f"El modelo '{model.id}' no produce: {shown}. "
            f"Stems disponibles: {', '.join(model.stems)}.",
            "Elige stems de esa lista con --stems, separados por comas (p. ej. "
            "--stems vocals,instrumental), o ejecuta `stemseparator models` para ver otros "
            "modelos.",
        )
    return frozenset(StemName(name) for name in names)


# --- logging (--verbose, interfaces.md §1) ------------------------------------------------


class _StderrHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Writes to whatever sys.stderr is when the record is emitted."""

    def __init__(self) -> None:
        super().__init__(sys.stderr)

    def emit(self, record: logging.LogRecord) -> None:
        self.setStream(sys.stderr)
        super().emit(record)


def _configure_logging(verbose: bool) -> tuple[logging.Handler, int]:
    """One stderr handler on the root logger, and FFmpeg's stderr at DEBUG with --verbose.

    With a handler on an ancestor, audio-separator does not add its own StreamHandler, so
    the engine's records come out with this format too. Returns what _restore_logging needs.
    """
    handler = _StderrHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    ffmpeg_logger = logging.getLogger(FFMPEG_LOGGER)
    previous_level = ffmpeg_logger.level
    if verbose:
        ffmpeg_logger.setLevel(logging.DEBUG)
    return handler, previous_level


def _restore_logging(handler: logging.Handler, ffmpeg_level: int) -> None:
    logging.getLogger().removeHandler(handler)
    logging.getLogger(FFMPEG_LOGGER).setLevel(ffmpeg_level)


# --- output -------------------------------------------------------------------------------


def _print_summary(console: Console, result: SeparationResult) -> None:
    table = Table()
    table.add_column("Stem")
    table.add_column("Ruta")
    table.add_column("Duración", justify="right")
    table.add_column("Tamaño", justify="right")
    for stem_file in result.stems:
        table.add_row(
            Text(stem_file.stem.value),
            Text(str(stem_file.path)),
            Text(f"{_decimal(stem_file.duration_s, 2)} s"),
            Text(_megabytes(stem_file.size_bytes)),
        )
    console.print(table)
    console.print(f"Tiempo total: {_decimal(result.elapsed_s, 1)} s", markup=False)
    console.print(f"Ganancia de salida: {_gain(result.output_gain)}", markup=False)
    console.print(
        "La suma de los stems es la mezcla multiplicada por la ganancia de salida.",
        markup=False,
    )


def _print_error(console: Console, message: str, hint: str) -> None:
    console.print(f"Error: {message}", markup=False)
    console.print(hint, markup=False)


def _print_traceback(console: Console, error: BaseException) -> None:
    console.print("".join(traceback.format_exception(error)).rstrip(), markup=False)


# --- commands -----------------------------------------------------------------------------


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"stemseparator {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Muestra la versión y termina.",
            callback=_print_version,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Separa un archivo de audio o video en pistas (stems). Solo CPU."""


@app.command(cls=_SpanishCommand, options_metavar="[OPCIONES]")
def separate(
    input_path: Annotated[
        Path,
        typer.Argument(
            metavar="INPUT", help="Ruta al archivo de audio o video.", show_default=False
        ),
    ],
    output: Annotated[
        Path,
        typer.Option(
            "-o",
            "--output",
            metavar="DIR",
            help="Directorio de salida. Por defecto: el directorio actual.",
            show_default=False,
        ),
    ] = Path("."),
    model: Annotated[
        str | None,
        typer.Option(
            "-m",
            "--model",
            metavar="ID",
            help="Identificador del catálogo. Por defecto: el modelo predeterminado.",
            show_default=False,
        ),
    ] = None,
    stems: Annotated[
        str | None,
        typer.Option(
            "-s",
            "--stems",
            metavar="LISTA",
            help="Stems separados por comas, p. ej. vocals,instrumental. "
            "Por defecto: todos los del modelo.",
            show_default=False,
        ),
    ] = None,
    output_format: Annotated[
        OutputFormat,
        typer.Option(
            "-f", "--format", help="Formato de salida. Por defecto: wav.", show_default=False
        ),
    ] = OutputFormat.WAV,
    overwrite: Annotated[
        bool,
        typer.Option(
            "--overwrite",
            help="Sobrescribe los archivos existentes. Durante el traslado se guardan copias "
            ".stemseparator-tmp-*.bak que se borran al terminar.",
            show_default=False,
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option(
            "-v",
            "--verbose",
            help="Muestra los logs detallados (motor y FFmpeg) y el traceback de los errores.",
            show_default=False,
        ),
    ] = False,
) -> None:
    """Separa un archivo de audio o video en pistas (stems)."""
    stdout = Console(highlight=False)
    stderr = Console(stderr=True, highlight=False, soft_wrap=True)
    handler, ffmpeg_level = _configure_logging(verbose)
    reporter: RichProgressReporter | None = None
    try:
        try:
            request = SeparationRequest(
                input_path=input_path,
                output_dir=output,
                model_id=model,
                stems=parse_stems(stems, model),
                output_format=output_format,
                overwrite=overwrite,
            )
            runtime = build_runtime(verbose)
            reporter = RichProgressReporter(stdout, runtime.model_dir, model)
            result = runtime.service.run(request, reporter)
        finally:
            if reporter is not None:
                reporter.close()
    except StemSeparatorError as error:
        if verbose:
            _print_traceback(stderr, error)
        _print_error(stderr, error.message, error.hint)
        raise typer.Exit(error.exit_code) from None
    except KeyboardInterrupt:
        # ADR-004 already cleaned up (or raised OutputRestoreError, code 6, handled above).
        stderr.print("Separación interrumpida por el usuario (Ctrl+C).", markup=False)
        raise typer.Exit(EXIT_INTERRUPTED) from None
    except Exception as error:
        if verbose:
            _print_traceback(stderr, error)
        _print_error(
            stderr,
            f"Error inesperado: {error}",
            "Vuelve a ejecutar con --verbose para ver el detalle y comunica el error.",
        )
        raise typer.Exit(EXIT_UNEXPECTED) from None
    finally:
        _restore_logging(handler, ffmpeg_level)

    _print_summary(stdout, result)
    if result.interrupted_during_publish:
        stderr.print(
            "Aviso: la interrupción (Ctrl+C) llegó durante la publicación de los archivos. "
            "Se esperó a que terminara y el trabajo se completó: los stems de la tabla están "
            "en su sitio.",
            markup=False,
        )


@app.command(cls=_SpanishCommand, options_metavar="[OPCIONES]")
def models() -> None:
    """Lista los modelos del catálogo, su coste en CPU y si ya están descargados."""
    # Light (interfaces.md §1): reads the catalog and looks at the model directory; it
    # never builds the engine. Rich wraps long lines at the terminal width, never cuts them.
    stdout = Console(highlight=False)
    model_dir = default_model_dir()
    catalog = list_models()
    for index, model in enumerate(catalog):
        if index:
            stdout.print()
        for line in _model_block(model, model_dir):
            stdout.print(line, markup=False)
    references = dict.fromkeys(model.cpu_reference for model in catalog)
    stdout.print()
    stdout.print(
        f"Coste medido en el equipo de referencia ({', '.join(references)}, ADR-002) "
        f"para una canción de {SONG_S // 60}:{SONG_S % 60:02d}.",
        markup=False,
    )


_NOT_FOUND = "no encontrado"
_AUDIO_SEPARATOR = "audio-separator"


def _audio_separator_version() -> str | None:
    """Installed version, or None if absent. Neither call imports the package (RNF-12)."""
    if importlib.util.find_spec("audio_separator") is None:
        return None
    try:
        return importlib.metadata.version(_AUDIO_SEPARATOR)
    except importlib.metadata.PackageNotFoundError:
        return "desconocida"


@app.command(cls=_SpanishCommand, options_metavar="[OPCIONES]")
def doctor() -> None:
    """Comprueba las dependencias y muestra el dispositivo de inferencia."""
    stdout = Console(highlight=False, soft_wrap=True)
    stderr = Console(stderr=True, highlight=False, soft_wrap=True)
    checks = LocalSystemChecks()
    versions = checks.ffmpeg_versions()
    audio_separator = _audio_separator_version()
    missing = [name for name in ("ffmpeg", "ffprobe") if versions[name] is None]
    if audio_separator is None:
        missing.append(_AUDIO_SEPARATOR)

    if missing:
        # Separator() calls check_ffmpeg_installed(): checked here, not caught (note 2026-10-06).
        device = f"no se pudo determinar (falta {', '.join(missing)})"
    else:
        device = AudioSeparatorEngine().inference_device()
        if device == "cuda":
            device += " (separate lo rechazará: StemSeparator solo usa la CPU, RNF-05)"
    cpu_count = os.cpu_count()
    report = (
        ("Python", platform.python_version()),
        ("StemSeparator", __version__),
        (_AUDIO_SEPARATOR, audio_separator or _NOT_FOUND),
        ("ffmpeg", versions["ffmpeg"] or _NOT_FOUND),
        ("ffprobe", versions["ffprobe"] or _NOT_FOUND),
        ("Hilos de CPU", str(cpu_count) if cpu_count is not None else "desconocido"),
        ("Caché de modelos", str(default_model_dir())),
        ("Dispositivo", device),
    )
    width = max(len(label) for label, _ in report) + 2
    for label, value in report:
        stdout.print(f"{label + ':':<{width}}{value}", markup=False)

    if not missing:
        return
    if "ffmpeg" in missing or "ffprobe" in missing:
        try:
            checks.require_ffmpeg()
        except FFmpegNotFoundError as error:
            _print_error(stderr, error.message, error.hint)
    if _AUDIO_SEPARATOR in missing:
        _print_error(
            stderr,
            "No se encontró audio-separator.",
            "Reinstala las dependencias de StemSeparator (`uv sync`).",
        )
    raise typer.Exit(EXIT_MISSING_DEPENDENCY)
