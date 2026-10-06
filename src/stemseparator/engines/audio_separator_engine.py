"""SeparationEngine backed by audio-separator 0.47.0 (interfaces.md §3, ADR-001, ADR-002).

The only module that imports `audio_separator`, always lazily (inside functions), together
with `onnxruntime`, `soundfile` and `numpy`: importing this module stays cheap and the unit
tests never need the real packages. All four go through importlib.import_module: none ships
types mypy can use here (numpy's stubs need Python 3.12 syntax; mypy targets 3.11).
"""

import contextlib
import importlib
import logging
import os
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, TypeVar

from stemseparator.domain.errors import EngineError
from stemseparator.domain.models import ModelSpec, StemName
from stemseparator.domain.progress import JobStage, ProgressReporter

logger = logging.getLogger(__name__)

T = TypeVar("T")

ENGINE_INPUT_HEADROOM = 0.5  # -6 dB before the engine, undone on its float stems (ADR-002)
ENGINE_RESCALE_GUARD = 0.9999  # a stem peak at or above this means the engine rescaled it

_ENGINE_INPUT_NAME = "engine_input.wav"
_FLOAT_SUBTYPE = "FLOAT"  # soundfile subtype for 32-bit float WAV: no quantization
_MODEL_DIR_OVERRIDE = "AUDIO_SEPARATOR_MODEL_DIR"  # overrides model_file_dir in Separator

_VERBOSE_HINT = "Vuelve a ejecutar con --verbose para ver el detalle del motor."
_STAGE_FAILURE = {
    JobStage.LOADING_MODEL: (
        "No se pudo cargar el modelo de separación",
        "Si es la primera vez que usas este modelo, comprueba la conexión a internet: los "
        "pesos se descargan al cargarlo. " + _VERBOSE_HINT,
    ),
    JobStage.SEPARATING: ("El motor de separación falló al procesar el audio", _VERBOSE_HINT),
}


def _import_separator_class() -> Callable[..., Any]:
    """Disable onnxruntime telemetry, then import audio_separator (RNF-12, point 1).

    The environment variable is what works; disable_telemetry_events() alone did not stop
    the connections during separate() in T-01. Both happen before audio_separator loads.
    """
    os.environ["ORT_DISABLE_TELEMETRY"] = "1"
    onnxruntime = importlib.import_module("onnxruntime")
    onnxruntime.disable_telemetry_events()
    separator_class: Callable[..., Any] = importlib.import_module(
        "audio_separator.separator"
    ).Separator
    return separator_class


@contextlib.contextmanager
def _model_dir_override_hidden(warning: str, *args: object) -> Iterator[None]:
    """Hide AUDIO_SEPARATOR_MODEL_DIR from Separator(), which would silently prefer it to
    model_file_dir (and with it, STEMSEPARATOR_MODEL_DIR, RF-12). Restored on exit."""
    value = os.environ.pop(_MODEL_DIR_OVERRIDE, None)
    if value is None:
        yield
        return
    logger.warning(warning, value, *args)
    try:
        yield
    finally:
        os.environ[_MODEL_DIR_OVERRIDE] = value


def _read_float(path: Path) -> tuple[Any, int]:
    soundfile = importlib.import_module("soundfile")
    data, sample_rate = soundfile.read(path, dtype="float32", always_2d=True)
    return data, int(sample_rate)


def _write_float(path: Path, data: Any, sample_rate: int) -> None:
    np = importlib.import_module("numpy")
    soundfile = importlib.import_module("soundfile")
    soundfile.write(path, data.astype(np.float32), sample_rate, subtype=_FLOAT_SUBTYPE)


def _peak(data: Any) -> float:
    np = importlib.import_module("numpy")
    return float(np.abs(data).max())


class AudioSeparatorEngine:
    """SeparationEngine implementation; the 8 points of interfaces.md §3, in order."""

    def __init__(self, verbose: bool = False) -> None:
        self._log_level = logging.DEBUG if verbose else logging.WARNING
        self._device: str | None = None

    def separate(
        self,
        audio_path: Path,
        model: ModelSpec,
        work_dir: Path,
        model_dir: Path,
        reporter: ProgressReporter | None = None,
    ) -> dict[StemName, Path]:
        """Separate audio_path with the model, writing only inside work_dir.

        LOADING_MODEL covers points 1-4 (telemetry and imports, input headroom, Separator,
        device check, load_model); SEPARATING covers points 5-8 (separate, missing outputs,
        rescale guard, undoing the headroom). Raises EngineError on any engine failure.
        """
        separator = self._stage(
            JobStage.LOADING_MODEL,
            reporter,
            lambda: self._load(audio_path, model, work_dir, model_dir),
        )
        return self._stage(
            JobStage.SEPARATING, reporter, lambda: self._separate(separator, model, work_dir)
        )

    def inference_device(self) -> str:
        """ "cpu", "mps" or "cuda", as chosen by audio-separator; cached per instance.

        Builds a throwaway Separator over a temporary directory, without load_model() or
        separate(). Does not raise for CUDA: reporting it is doctor's job.
        """
        if self._device is None:
            separator_class = _import_separator_class()
            with (
                tempfile.TemporaryDirectory(prefix="stemseparator-device-") as tmp,
                _model_dir_override_hidden(
                    "%s=%s is ignored by stemseparator; set STEMSEPARATOR_MODEL_DIR to choose "
                    "the model directory",
                    _MODEL_DIR_OVERRIDE,
                ),
            ):
                separator = separator_class(
                    model_file_dir=tmp, output_dir=tmp, log_level=self._log_level
                )
                self._device = str(separator.torch_device)
        return self._device

    def _stage(self, stage: JobStage, reporter: ProgressReporter | None, fn: Callable[[], T]) -> T:
        """Notify one engine stage; report its failure once, as an EngineError.

        Library errors become EngineError with the original in __cause__. Anything that is
        not an Exception (Ctrl+C) is reported and propagated unchanged.
        """
        if reporter is not None:
            reporter.stage_started(stage)
        started = time.monotonic()
        try:
            result = fn()
        except EngineError as exc:
            if reporter is not None:
                reporter.stage_failed(stage, exc)
            raise
        except Exception as exc:
            message, hint = _STAGE_FAILURE[stage]
            error = EngineError(f"{message}: {exc}", hint)
            if reporter is not None:
                reporter.stage_failed(stage, error)
            raise error from exc
        except BaseException as exc:
            if reporter is not None:
                reporter.stage_failed(stage, exc)
            raise
        if reporter is not None:
            reporter.stage_finished(stage, time.monotonic() - started)
        return result

    def _load(self, audio_path: Path, model: ModelSpec, work_dir: Path, model_dir: Path) -> Any:
        """Points 1-4, inside LOADING_MODEL. Returns the Separator with the model loaded."""
        separator_class = _import_separator_class()  # 1

        data, sample_rate = _read_float(audio_path)  # 2
        _write_float(work_dir / _ENGINE_INPUT_NAME, data * ENGINE_INPUT_HEADROOM, sample_rate)

        with _model_dir_override_hidden(  # 3
            "%s=%s is ignored; models are read from and downloaded to %s",
            _MODEL_DIR_OVERRIDE,
            model_dir,
        ):
            separator = separator_class(
                model_file_dir=os.fspath(model_dir),
                output_dir=os.fspath(work_dir),
                output_format="WAV",
                sample_rate=44100,
                use_soundfile=True,
                normalization_threshold=1.0,
                log_level=self._log_level,
            )

        device = str(separator.torch_device)  # 4, between the constructor and load_model()
        logger.info("audio-separator inference device: %s", device)
        if device == "cuda":
            raise EngineError(
                "La instalación contiene paquetes GPU: el motor eligió CUDA y StemSeparator "
                "solo funciona en CPU.",
                "Reinstala las dependencias con `uv sync`, que toma torch del índice solo "
                "CPU (https://download.pytorch.org/whl/cpu).",
            )

        separator.load_model(model_filename=model.engine_filename)  # 3, downloads if missing
        return separator

    def _separate(self, separator: Any, model: ModelSpec, work_dir: Path) -> dict[StemName, Path]:
        """Points 5-8, inside SEPARATING."""
        separator.separate(  # 5
            str(work_dir / _ENGINE_INPUT_NAME),
            custom_output_names={label: stem.value for label, stem in model.engine_labels.items()},
        )

        outputs = {stem: work_dir / f"{stem.value}.wav" for stem in model.stems}
        missing = [stem.value for stem, path in outputs.items() if not path.is_file()]  # 6
        if missing:
            raise EngineError(
                f"El motor no produjo todas las pistas esperadas; falta: {', '.join(missing)}.",
                _VERBOSE_HINT,
            )

        stems = {stem: _read_float(path) for stem, path in outputs.items()}
        for stem, (data, _) in stems.items():  # 7, before undoing the headroom
            peak = _peak(data)
            if peak >= ENGINE_RESCALE_GUARD:
                raise EngineError(
                    f"El motor reescaló la pista {stem.value} (pico {peak:.4f}): la suma de "
                    "las pistas ya no reconstruiría la mezcla.",
                    "Es un fallo interno del motor de separación; avisa del problema con la "
                    "salida de --verbose.",
                )

        for stem, (data, sample_rate) in stems.items():  # 8, in place, float32
            _write_float(outputs[stem], data * (1 / ENGINE_INPUT_HEADROOM), sample_rate)
        return outputs
