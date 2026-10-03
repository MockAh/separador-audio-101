"""Separation job orchestration (domain.md §4, architecture.md §3).

Depends only on `domain` and the ports; adapters are injected by the CLI.
"""

import math
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from stemseparator.domain.catalog import get_model
from stemseparator.domain.errors import (
    InputNotFoundError,
    InsufficientDiskSpaceError,
    NoAudioStreamError,
    OutputConflictError,
    OutputNotWritableError,
    UnsupportedFormatError,
    UnsupportedStemError,
)
from stemseparator.domain.models import (
    MediaInfo,
    ModelSpec,
    SeparationRequest,
    SeparationResult,
    StemName,
)
from stemseparator.domain.progress import JobStage, ProgressReporter
from stemseparator.engines.base import SeparationEngine
from stemseparator.media.base import AudioNormalizer, MediaProbe, StemExporter, SystemChecks

T = TypeVar("T")

SUPPORTED_EXTENSIONS = (".wav", ".mp3", ".flac", ".m4a", ".mp4", ".mkv")  # RF-01

# Disk space estimate (domain.md §5): working audio is float32 stereo at 44 100 Hz.
_WORK_SAMPLE_RATE = 44100
_WORK_CHANNELS = 2
_WORK_BYTES_PER_SAMPLE = 4

_WORK_AUDIO_NAME = "source.wav"
_WRITE_PROBE_PREFIX = ".stemseparator-tmp-"  # ADR-004 prefix, recognizable if it leaks


def estimate_needed_bytes(duration_s: float, model_stems: int) -> int:
    """Conservative bytes needed by a job (domain.md §5), rounded up."""
    per_second = _WORK_SAMPLE_RATE * _WORK_CHANNELS * _WORK_BYTES_PER_SAMPLE
    return math.ceil(duration_s * per_second * (model_stems + 1) * 2)


def _megabytes(n_bytes: int) -> str:
    """Decimal MB (10^6 bytes), one decimal, decimal comma (interfaces.md §4)."""
    return f"{n_bytes / 1_000_000:.1f}".replace(".", ",") + " MB"


class SeparationService:
    def __init__(
        self,
        engine: SeparationEngine,
        probe: MediaProbe,
        normalizer: AudioNormalizer,
        exporter: StemExporter,
        system: SystemChecks,
        model_dir: Path,
    ) -> None:
        self._engine = engine
        self._probe = probe
        self._normalizer = normalizer
        self._exporter = exporter
        self._system = system
        self._model_dir = model_dir

    def validate(
        self, request: SeparationRequest, reporter: ProgressReporter | None = None
    ) -> MediaInfo:
        """Run the whole VALIDATING stage and return the MediaInfo of the input probe.

        Writes nothing but the output directory. Reports its own stage, failure included,
        also when run() is the caller.
        """
        return self._stage(JobStage.VALIDATING, reporter, lambda: self._validate(request))

    def run(
        self, request: SeparationRequest, reporter: ProgressReporter | None = None
    ) -> SeparationResult:
        """Run the job. For now it stops after PREPARING (T-09 continues)."""
        self.validate(request, reporter)
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            self._stage(JobStage.PREPARING, reporter, lambda: self._prepare(request, work_dir))
            # T-09 continues here: separation, export and publication. Raised outside any
            # stage and outside any except, so it propagates as is.
            raise NotImplementedError("SeparationService.run() stops after PREPARING (T-09)")

    @staticmethod
    def _stage(stage: JobStage, reporter: ProgressReporter | None, action: Callable[[], T]) -> T:
        """Notify start, then finish or failure, of one stage this service owns."""
        if reporter is not None:
            reporter.stage_started(stage)
        started = time.monotonic()
        try:
            result = action()
        except BaseException as exc:  # Ctrl+C included (ProgressReporter contract)
            if reporter is not None:
                reporter.stage_failed(stage, exc)
            raise
        if reporter is not None:
            reporter.stage_finished(stage, time.monotonic() - started)
        return result

    # --- VALIDATING (domain.md §4) -------------------------------------------------------

    def _validate(self, request: SeparationRequest) -> MediaInfo:
        self._check_input(request.input_path)
        self._system.require_ffmpeg()
        model = get_model(request.model_id)
        stems = self._requested_stems(request, model)
        self._check_conflicts(request, stems)
        self._prepare_output_dir(request.output_dir)

        info = self._probe.probe(request.input_path)
        if not info.has_audio:
            raise NoAudioStreamError(
                f"El archivo {request.input_path.name} no contiene ninguna pista de audio.",
                "Comprueba que sea el archivo correcto: si es un video, tiene que llevar audio.",
            )
        self._check_disk_space(info.duration_s, model, request.output_dir)
        return info

    @staticmethod
    def _check_input(path: Path) -> None:
        if not path.exists():
            raise InputNotFoundError(
                f"No existe el archivo de entrada: {path}",
                "Comprueba que la ruta esté bien escrita.",
            )
        if not path.is_file():
            raise InputNotFoundError(
                f"La entrada no es un archivo: {path}",
                "Indica la ruta de un archivo de audio o video, no de un directorio.",
            )
        try:
            with path.open("rb"):
                pass
        except OSError as exc:
            raise InputNotFoundError(
                f"No se puede leer el archivo de entrada: {path}",
                "Comprueba que tengas permiso de lectura sobre el archivo.",
            ) from exc
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise UnsupportedFormatError(
                f"Formato no admitido: {path.name}. "
                f"Extensiones admitidas: {', '.join(SUPPORTED_EXTENSIONS)}.",
                "Convierte el archivo a uno de esos formatos, por ejemplo con FFmpeg.",
            )

    @staticmethod
    def _requested_stems(request: SeparationRequest, model: ModelSpec) -> tuple[StemName, ...]:
        """Requested stems in the model's order; all of them when none were requested."""
        if request.stems is None:
            return model.stems
        unsupported = sorted(request.stems - set(model.stems))
        if unsupported:
            raise UnsupportedStemError(
                f"El modelo '{model.id}' no produce: {', '.join(unsupported)}. "
                f"Stems disponibles: {', '.join(model.stems)}.",
                "Elige stems de esa lista con --stems, o ejecuta `stemseparator models` "
                "para ver otros modelos.",
            )
        return tuple(stem for stem in model.stems if stem in request.stems)

    @staticmethod
    def _check_conflicts(request: SeparationRequest, stems: tuple[StemName, ...]) -> None:
        # Conflicts only exist in an existing directory (architecture.md §3).
        if request.overwrite or not request.output_dir.is_dir():
            return
        base = request.input_path.stem
        ext = request.output_format.value
        existing = [
            name
            for name in (f"{base}_{stem.value}.{ext}" for stem in stems)
            if (request.output_dir / name).exists()
        ]
        if existing:
            raise OutputConflictError(
                f"Ya existen archivos de salida en {request.output_dir}: {', '.join(existing)}.",
                "Usa --overwrite para reemplazarlos o elige otro directorio con --output.",
            )

    @staticmethod
    def _prepare_output_dir(output_dir: Path) -> None:
        """Create the output directory if missing (RF-05) and check it is writable."""
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=output_dir, prefix=_WRITE_PROBE_PREFIX):
                pass
        except OSError as exc:
            raise OutputNotWritableError(
                f"No se puede escribir en el directorio de salida: {output_dir}",
                "Comprueba los permisos o elige otro directorio con --output.",
            ) from exc

    def _check_disk_space(self, duration_s: float, model: ModelSpec, output_dir: Path) -> None:
        # The estimate counts every stem the model produces, not only the requested ones.
        needed = estimate_needed_bytes(duration_s, len(model.stems))
        volumes = (
            # The work directory does not exist yet: measure the system temp dir (domain.md §5).
            (
                Path(tempfile.gettempdir()),
                "Libera espacio en ese volumen o apunta el directorio temporal a otro "
                "(TMPDIR en Linux y macOS, TEMP en Windows).",
            ),
            (output_dir, "Libera espacio en ese volumen o elige otro directorio con --output."),
        )
        for volume, hint in volumes:
            free = self._system.free_bytes(volume)
            if free < needed:
                raise InsufficientDiskSpaceError(
                    f"Espacio insuficiente en {volume}: se necesitan {_megabytes(needed)} "
                    f"y hay {_megabytes(free)} disponibles.",
                    hint,
                )

    # --- PREPARING (domain.md §4) --------------------------------------------------------

    def _prepare(self, request: SeparationRequest, work_dir: Path) -> MediaInfo:
        """Normalize the input to the working audio and probe it (reference duration)."""
        work_audio = self._normalizer.normalize(request.input_path, work_dir / _WORK_AUDIO_NAME)
        return self._probe.probe(work_audio)
