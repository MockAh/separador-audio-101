"""AudioSeparatorEngine with a simulated audio stack (interfaces.md §3, T-11).

`audio_separator`, `onnxruntime`, `soundfile` and `numpy` are fakes served by a meta path
finder, never the real packages: no network, no weights, no audio libraries. The finder
builds each fake module at import time, so the fakes can record WHEN they were imported
(CA-26 is about order, not final state). Every module of those packages is taken out of
sys.modules before the test and put back afterwards, so no fake outlives its test.
"""

import ast
import importlib.abc
import importlib.machinery
import logging
import math
import os
import subprocess
import sys
import types
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

import stemseparator.engines.audio_separator_engine as ase
from stemseparator.domain.catalog import get_model
from stemseparator.domain.errors import EngineError
from stemseparator.domain.models import ModelSpec, StemName
from stemseparator.domain.progress import JobStage

ENGINE_LOGGER = "stemseparator.engines.audio_separator_engine"
FAKED_PACKAGES = ("audio_separator", "onnxruntime", "soundfile", "numpy")
HEAVY_MODULES = ("audio_separator", "onnxruntime", "soundfile", "numpy", "torch")
SEPARATOR_LOGGER = "audio_separator.separator.separator"  # Separator's logger, 0.47.0
SOUNDFILE_NOTICE = "Using soundfile for writing."

Calls = list[tuple[Any, ...]]
Frames = list[tuple[float, ...]]


# ── fake numpy ──────────────────────────────────────────────────────────────────────────


class FakeArray:
    """Frames x channels of Python floats with a dtype label. No rounding: values are exact."""

    def __init__(self, frames: Sequence[Sequence[float]], dtype: str = "float32") -> None:
        self.frames: Frames = [tuple(float(v) for v in frame) for frame in frames]
        self.dtype = dtype

    def __mul__(self, factor: float) -> "FakeArray":
        return FakeArray([[v * float(factor) for v in f] for f in self.frames], self.dtype)

    __rmul__ = __mul__

    def __abs__(self) -> "FakeArray":
        return FakeArray([[abs(v) for v in f] for f in self.frames], self.dtype)

    def max(self) -> float:
        return max(v for f in self.frames for v in f)

    def astype(self, dtype: str) -> "FakeArray":
        return FakeArray(self.frames, dtype)


def _np_abs(array: FakeArray) -> FakeArray:
    return abs(array)


def _np_max(array: FakeArray) -> float:
    return array.max()


def close(actual: Sequence[float] | float, expected: Sequence[float] | float) -> bool:
    """math.isclose, element by element. pytest.approx would consult sys.modules["numpy"],
    which is the fake one inside these tests."""
    if isinstance(actual, float | int) and isinstance(expected, float | int):
        return math.isclose(actual, expected, rel_tol=1e-12)
    assert not isinstance(actual, float | int) and not isinstance(expected, float | int)
    return len(actual) == len(expected) and all(
        math.isclose(a, e, rel_tol=1e-12) for a, e in zip(actual, expected, strict=True)
    )


def constant(value: float, frames: int = 4) -> FakeArray:
    """Stereo signal whose peak is exactly `value` (first sample of the left channel)."""
    return FakeArray([(value, -0.1)] + [(0.2, 0.0)] * (frames - 1))


# ── the stack: fake soundfile store, fake onnxruntime, fake Separator ───────────────────


class FakeTorchDevice:
    def __init__(self, name: str) -> None:
        self.name = name

    def __str__(self) -> str:
        return self.name


class FakeStack:
    """State shared by the fake modules of one test, plus knobs to make them misbehave."""

    def __init__(self) -> None:
        self.events: Calls = []  # imports and telemetry calls, in order
        self.tqdm_env_at_import: list[str | None] = []  # TQDM_DISABLE when audio_separator loads
        self.files: dict[Path, tuple[FakeArray, int, str]] = {}  # soundfile "disk"
        self.reads: Calls = []
        self.writes: Calls = []
        self.separators: list[FakeSeparator] = []
        self.device = "cpu"
        self.fail_at: str | None = None  # import, read_input, constructor, load_model, separate
        self.fail_with: BaseException = RuntimeError("engine exploded")
        self.missing: set[str] = set()  # engine labels the fake Separator does not write
        # Stem written per engine label, from what the engine received (input x headroom).
        self.outputs: dict[str, Callable[[FakeArray], FakeArray]] = {
            "Vocals": lambda received: received * 0.3,
            "Instrumental": lambda received: received * 0.7,
        }

    def put(self, path: Path, array: FakeArray, sample_rate: int = 44100) -> Path:
        """Store an audio file in the fake soundfile, with a placeholder on the real disk."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake wav")
        self.files[path] = (array, sample_rate, "FLOAT")
        return path

    def telemetry_disabled(self) -> bool:
        return any(e[0] == "disable_telemetry_events" for e in self.events)

    # soundfile API used by the adapter
    def sf_read(self, file: Any, dtype: str = "float64", always_2d: bool = False) -> Any:
        path = Path(file)
        self.reads.append((path, dtype, always_2d))
        if self.fail_at == "read_input" and path.name == "source.wav":
            raise self.fail_with
        if path not in self.files:
            raise RuntimeError(f"Error opening {str(path)!r}: System error.")
        array, sample_rate, _ = self.files[path]
        return array.astype(dtype), sample_rate

    def sf_write(
        self, file: Any, data: FakeArray, samplerate: int, subtype: str | None = None
    ) -> None:
        path = Path(file)
        self.writes.append((path, data, samplerate, subtype))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake wav")
        self.files[path] = (data, samplerate, subtype or "PCM_16")


class FakeSeparator:
    """Stands in for audio_separator.separator.Separator (0.47.0 API subset)."""

    stack: FakeStack  # set on the class built for each test

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self.calls: Calls = []
        self.dirs_existed = {
            key: Path(kwargs[key]).is_dir()
            for key in ("model_file_dir", "output_dir")
            if key in kwargs
        }
        self.received: FakeArray | None = None
        self.env_model_dir = os.environ.get("AUDIO_SEPARATOR_MODEL_DIR")
        self.stack.separators.append(self)
        if self.stack.fail_at == "constructor":
            raise self.stack.fail_with
        self.torch_device = FakeTorchDevice(self.stack.device)

    def load_model(self, model_filename: str = "default.ckpt") -> None:
        self.calls.append(("load_model", model_filename))
        if self.stack.fail_at == "load_model":
            raise self.stack.fail_with

    def separate(
        self, audio_file_path: str, custom_output_names: dict[str, str] | None = None
    ) -> list[str]:
        self.calls.append(("separate", audio_file_path, custom_output_names))
        if self.stack.fail_at == "separate":
            raise self.stack.fail_with
        received, _, _ = self.stack.files[Path(audio_file_path)]
        self.received = received
        names = {k.lower(): v for k, v in (custom_output_names or {}).items()}
        written = []
        for label, make in self.stack.outputs.items():
            if label in self.stack.missing:
                continue
            name = f"{names.get(label.lower(), label)}.wav"
            self.stack.sf_write(Path(self.kwargs["output_dir"]) / name, make(received), 44100)
            written.append(name)
        return written


class _FakeLoader(importlib.abc.Loader):
    def __init__(self, stack: FakeStack) -> None:
        self.stack = stack

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> types.ModuleType | None:
        return None

    def exec_module(self, module: types.ModuleType) -> None:
        stack = self.stack
        name = module.__name__
        env = os.environ.get("ORT_DISABLE_TELEMETRY")
        if name == "onnxruntime":
            stack.events.append(("import onnxruntime", env))

            def disable_telemetry_events() -> None:
                stack.events.append(
                    ("disable_telemetry_events", os.environ.get("ORT_DISABLE_TELEMETRY"))
                )

            module.disable_telemetry_events = disable_telemetry_events  # type: ignore[attr-defined]
        elif name == "audio_separator":
            stack.events.append(("import audio_separator", stack.telemetry_disabled(), env))
            stack.tqdm_env_at_import.append(os.environ.get("TQDM_DISABLE"))
            if stack.fail_at == "import":
                raise ImportError("No module named 'audio_separator'")
        elif name == "audio_separator.separator":
            separator_class = type("Separator", (FakeSeparator,), {"stack": stack})
            module.Separator = separator_class  # type: ignore[attr-defined]
        elif name == "soundfile":
            module.read = stack.sf_read  # type: ignore[attr-defined]
            module.write = stack.sf_write  # type: ignore[attr-defined]
        elif name == "numpy":
            module.float32 = "float32"  # type: ignore[attr-defined]
            module.abs = _np_abs  # type: ignore[attr-defined]
            module.max = _np_max  # type: ignore[attr-defined]
            module.ndarray = FakeArray  # type: ignore[attr-defined]


class _FakeFinder(importlib.abc.MetaPathFinder):
    def __init__(self, stack: FakeStack) -> None:
        self.loader = _FakeLoader(stack)

    def find_spec(
        self, fullname: str, path: Sequence[str] | None, target: types.ModuleType | None = None
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname.split(".")[0] not in FAKED_PACKAGES:
            return None
        is_package = fullname == "audio_separator"
        return importlib.machinery.ModuleSpec(fullname, self.loader, is_package=is_package)


def _faked(name: str) -> bool:
    return name.split(".")[0] in FAKED_PACKAGES


@pytest.fixture
def stack(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeStack]:
    """Install the fake stack; restore sys.modules and sys.meta_path exactly afterwards."""
    fake = FakeStack()
    saved = {name: mod for name, mod in sys.modules.items() if _faked(name)}
    for name in saved:
        del sys.modules[name]
    finder = _FakeFinder(fake)
    sys.meta_path.insert(0, finder)
    # Pre-existing "0": the adapter must SET the variable, not rely on the environment.
    monkeypatch.setenv("ORT_DISABLE_TELEMETRY", "0")
    monkeypatch.setenv("TQDM_DISABLE", "0")  # same: the adapter must set it itself
    engine_logger = logging.getLogger(SEPARATOR_LOGGER)
    saved_filters = list(engine_logger.filters)
    monkeypatch.delenv("AUDIO_SEPARATOR_MODEL_DIR", raising=False)
    try:
        yield fake
    finally:
        sys.meta_path.remove(finder)
        engine_logger.filters[:] = saved_filters
        for name in [n for n in sys.modules if _faked(n)]:
            del sys.modules[name]
        sys.modules.update(saved)


def test_fake_stack_serves_fake_modules(stack: FakeStack) -> None:
    import numpy  # noqa: F401  (served by the fake finder)

    assert isinstance(sys.modules["numpy"].abs, types.FunctionType)


def test_fake_stack_was_removed() -> None:
    """Runs after the previous test: no fake module and no fake finder are left behind."""
    assert not any(isinstance(f, _FakeFinder) for f in sys.meta_path)
    for name, module in sys.modules.items():
        if _faked(name):
            assert not isinstance(getattr(module, "__loader__", None), _FakeLoader), name


# ── helpers ─────────────────────────────────────────────────────────────────────────────


class RecordingReporter:
    def __init__(self) -> None:
        self.calls: Calls = []

    def stage_started(self, stage: JobStage) -> None:
        self.calls.append(("started", stage))

    def stage_finished(self, stage: JobStage, elapsed_s: float) -> None:
        self.calls.append(("finished", stage))

    def stage_failed(self, stage: JobStage, error: BaseException) -> None:
        self.calls.append(("failed", stage, error))


class Job:
    """Directories and input of one separate() call. work_dir exists, as the service makes it."""

    def __init__(self, tmp_path: Path, stack: FakeStack) -> None:
        self.stack = stack
        self.work_dir = tmp_path / "work" / "engine"
        self.work_dir.mkdir(parents=True)
        self.model_dir = tmp_path / "models"
        self.input = FakeArray([(0.8, -0.4), (-0.6, 0.2), (0.1, 0.0)])
        self.audio_path = stack.put(tmp_path / "work" / "source.wav", self.input)
        self.reporter = RecordingReporter()

    def run(
        self,
        model: ModelSpec | None = None,
        engine: Any = None,
        reporter: Any = "default",
    ) -> dict[StemName, Path]:
        engine = engine if engine is not None else ase.AudioSeparatorEngine()
        result: dict[StemName, Path] = engine.separate(
            self.audio_path,
            model or get_model("vocals"),
            self.work_dir,
            self.model_dir,
            self.reporter if reporter == "default" else reporter,
        )
        return result

    @property
    def separator(self) -> FakeSeparator:
        assert len(self.stack.separators) == 1
        return self.stack.separators[0]


@pytest.fixture
def job(tmp_path: Path, stack: FakeStack) -> Job:
    return Job(tmp_path, stack)


# ── CA-26: telemetry disabled before importing the engine ───────────────────────────────


def test_telemetry_env_is_set_before_onnxruntime_is_imported(job: Job) -> None:
    job.run()

    imports = [e for e in job.stack.events if e[0] == "import onnxruntime"]
    assert imports == [("import onnxruntime", "1")]


def test_disable_telemetry_events_is_called_with_the_env_already_set(job: Job) -> None:
    job.run()

    calls = [e for e in job.stack.events if e[0] == "disable_telemetry_events"]
    assert calls == [("disable_telemetry_events", "1")]


def test_audio_separator_is_imported_after_both_telemetry_switches(job: Job) -> None:
    job.run()

    names = [e[0] for e in job.stack.events]
    assert names.index("disable_telemetry_events") < names.index("import audio_separator")
    first_import = next(e for e in job.stack.events if e[0] == "import audio_separator")
    assert first_import == ("import audio_separator", True, "1")


def test_telemetry_stays_disabled_after_separating(job: Job) -> None:
    job.run()

    assert os.environ["ORT_DISABLE_TELEMETRY"] == "1"


def test_inference_device_also_disables_telemetry_before_importing(stack: FakeStack) -> None:
    ase.AudioSeparatorEngine().inference_device()

    assert stack.events[0] == ("import onnxruntime", "1")
    assert ("import audio_separator", True, "1") in stack.events


# ── lazy import (subprocess: another test may already have imported the real modules) ───


def _modules_after_import(module: str) -> list[str]:
    code = (
        f"import sys, {module}\n"
        f"heavy = {HEAVY_MODULES!r}\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] in heavy))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], check=True, capture_output=True, text=True
    ).stdout
    loaded: list[str] = ast.literal_eval(out)
    return loaded


def test_importing_the_engine_module_does_not_import_the_audio_stack() -> None:
    assert _modules_after_import("stemseparator.engines.audio_separator_engine") == []


def test_importing_the_cli_does_not_import_the_audio_stack() -> None:
    # The CLI composes the real engine (T-12): its adapter module may be loaded, the stack
    # behind it may not, not even after building the runtime the command would use.
    code = (
        "import sys\n"
        "import stemseparator.cli as cli\n"
        "from stemseparator.engines.audio_separator_engine import AudioSeparatorEngine\n"
        "runtime = cli.build_runtime(verbose=False)\n"
        "assert isinstance(runtime.engine, AudioSeparatorEngine), type(runtime.engine)\n"
        f"heavy = {HEAVY_MODULES!r}\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] in heavy))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], check=True, capture_output=True, text=True
    ).stdout
    assert ast.literal_eval(out) == []


# ── engine console silenced (interfaces.md §3, point 1; T-12) ───────────────────────────


def test_tqdm_is_disabled_before_audio_separator_is_imported(job: Job) -> None:
    job.run()

    assert job.stack.tqdm_env_at_import == ["1"]


def test_inference_device_also_disables_tqdm_before_importing(stack: FakeStack) -> None:
    ase.AudioSeparatorEngine().inference_device()

    assert stack.tqdm_env_at_import == ["1"]


def _engine_record(message: str, level: int = logging.WARNING) -> logging.LogRecord:
    return logging.getLogger(SEPARATOR_LOGGER).makeRecord(
        SEPARATOR_LOGGER, level, "common_separator.py", 331, message, None, None
    )


def test_soundfile_notice_is_dropped_from_the_engine_logger(job: Job) -> None:
    job.run()

    assert not logging.getLogger(SEPARATOR_LOGGER).filter(_engine_record(SOUNDFILE_NOTICE))


@pytest.mark.parametrize(
    "message",
    ["Using soundfile for writing", "Model file not found", "Using soundfile for writing. x"],
)
def test_other_engine_records_go_through(job: Job, message: str) -> None:
    job.run()

    separator_logger = logging.getLogger(SEPARATOR_LOGGER)
    assert not separator_logger.filter(_engine_record(SOUNDFILE_NOTICE))  # filter installed
    assert separator_logger.filter(_engine_record(message))


def test_soundfile_filter_is_added_once_for_several_engines(job: Job) -> None:
    before = len(logging.getLogger(SEPARATOR_LOGGER).filters)
    job.run()
    job.run(engine=ase.AudioSeparatorEngine(verbose=True))
    ase.AudioSeparatorEngine().inference_device()

    assert len(logging.getLogger(SEPARATOR_LOGGER).filters) == before + 1


# ── constructor parameters (interfaces.md §3, point 3) ──────────────────────────────────


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("output_format", "WAV"),
        ("sample_rate", 44100),
        ("use_soundfile", True),
        ("normalization_threshold", 1.0),
    ],
)
def test_separator_constant_parameters(job: Job, key: str, expected: object) -> None:
    job.run()

    value = job.separator.kwargs[key]
    assert value == expected
    assert type(value) is type(expected)


def test_separator_model_file_dir_is_model_dir(job: Job) -> None:
    job.run()

    assert os.fspath(job.separator.kwargs["model_file_dir"]) == os.fspath(job.model_dir)


def test_separator_output_dir_is_work_dir(job: Job) -> None:
    job.run()

    assert os.fspath(job.separator.kwargs["output_dir"]) == os.fspath(job.work_dir)


@pytest.mark.parametrize(("verbose", "level"), [(False, logging.WARNING), (True, logging.DEBUG)])
def test_separator_log_level_follows_verbose(job: Job, verbose: bool, level: int) -> None:
    job.run(engine=ase.AudioSeparatorEngine(verbose=verbose))

    assert job.separator.kwargs["log_level"] == level


def test_separator_gets_exactly_the_contract_parameters(job: Job) -> None:
    job.run()

    assert job.separator.args == ()
    assert set(job.separator.kwargs) == {
        "model_file_dir",
        "output_dir",
        "output_format",
        "sample_rate",
        "use_soundfile",
        "normalization_threshold",
        "log_level",
    }


def test_load_model_gets_the_engine_filename(job: Job) -> None:
    job.run()

    assert job.separator.calls[0] == ("load_model", "UVR-MDX-NET-Voc_FT.onnx")


# ── device (CA-23, RNF-05) ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("device", ["cpu", "mps"])
def test_cpu_and_mps_are_accepted_and_logged(
    job: Job, device: str, caplog: pytest.LogCaptureFixture
) -> None:
    job.stack.device = device
    with caplog.at_level(logging.DEBUG, logger=ENGINE_LOGGER):
        stems = job.run()

    assert set(stems) == {StemName.VOCALS, StemName.INSTRUMENTAL}
    assert any(device in r.getMessage() for r in caplog.records if r.name == ENGINE_LOGGER)


def test_cuda_raises_engine_error_before_loading_the_model(
    job: Job, caplog: pytest.LogCaptureFixture
) -> None:
    job.stack.device = "cuda"
    with caplog.at_level(logging.DEBUG, logger=ENGINE_LOGGER), pytest.raises(EngineError) as exc:
        job.run()

    assert job.separator.calls == []  # neither load_model nor separate
    assert "GPU" in str(exc.value)
    assert exc.value.exit_code == 5
    assert any("cuda" in r.getMessage() for r in caplog.records if r.name == ENGINE_LOGGER)


# ── AUDIO_SEPARATOR_MODEL_DIR (interfaces.md §3, point 3, v0.15) ────────────────────────

OTHER_DIR = "/somewhere/else/models"


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == ENGINE_LOGGER and r.levelno == logging.WARNING
    ]


def test_separate_hides_audio_separator_model_dir_from_the_constructor(
    job: Job, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUDIO_SEPARATOR_MODEL_DIR", OTHER_DIR)

    job.run()

    assert job.separator.env_model_dir is None
    assert os.environ["AUDIO_SEPARATOR_MODEL_DIR"] == OTHER_DIR


def test_separate_warns_that_the_variable_is_ignored_and_names_model_dir(
    job: Job, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("AUDIO_SEPARATOR_MODEL_DIR", OTHER_DIR)

    with caplog.at_level(logging.DEBUG, logger=ENGINE_LOGGER):
        job.run()

    warnings = [w for w in _warnings(caplog) if "AUDIO_SEPARATOR_MODEL_DIR" in w]
    assert len(warnings) == 1
    assert str(job.model_dir) in warnings[0]


def test_separate_restores_the_variable_when_the_constructor_fails(
    job: Job, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUDIO_SEPARATOR_MODEL_DIR", OTHER_DIR)
    job.stack.fail_at = "constructor"

    with pytest.raises(EngineError):
        job.run()

    assert job.separator.env_model_dir is None
    assert os.environ["AUDIO_SEPARATOR_MODEL_DIR"] == OTHER_DIR


def test_without_the_variable_there_is_no_warning_and_it_stays_unset(
    job: Job, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG, logger=ENGINE_LOGGER):
        job.run()
        ase.AudioSeparatorEngine().inference_device()

    assert not [w for w in _warnings(caplog) if "AUDIO_SEPARATOR_MODEL_DIR" in w]
    assert "AUDIO_SEPARATOR_MODEL_DIR" not in os.environ


def test_inference_device_hides_the_variable_and_warns_pointing_to_ours(
    stack: FakeStack, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("AUDIO_SEPARATOR_MODEL_DIR", OTHER_DIR)

    with caplog.at_level(logging.DEBUG, logger=ENGINE_LOGGER):
        ase.AudioSeparatorEngine().inference_device()

    assert stack.separators[0].env_model_dir is None
    assert os.environ["AUDIO_SEPARATOR_MODEL_DIR"] == OTHER_DIR
    warnings = [w for w in _warnings(caplog) if "AUDIO_SEPARATOR_MODEL_DIR" in w]
    assert len(warnings) == 1
    assert "STEMSEPARATOR_MODEL_DIR" in warnings[0]


def test_inference_device_restores_the_variable_when_the_constructor_fails(
    stack: FakeStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUDIO_SEPARATOR_MODEL_DIR", OTHER_DIR)
    stack.fail_at = "constructor"

    with pytest.raises((RuntimeError, EngineError)):
        ase.AudioSeparatorEngine().inference_device()

    assert os.environ["AUDIO_SEPARATOR_MODEL_DIR"] == OTHER_DIR


# ── input headroom, rescale guard and undoing the headroom (CA-25, adapter part) ────────


def test_engine_receives_engine_input_wav(job: Job) -> None:
    job.run()

    _, path, _ = job.separator.calls[1]
    assert Path(path) == job.work_dir / "engine_input.wav"


def test_engine_input_is_the_work_audio_times_half_in_float32(job: Job) -> None:
    job.run()

    received = job.separator.received
    assert received is not None
    assert received.frames == [(0.4, -0.2), (-0.3, 0.1), (0.05, 0.0)]
    assert received.dtype == "float32"
    _, sample_rate, subtype = job.stack.files[job.work_dir / "engine_input.wav"]
    assert (sample_rate, subtype) == (44100, "FLOAT")


@pytest.mark.parametrize("peak", [0.9999, 1.0, -0.9999, 1.3])
def test_stem_at_or_above_the_guard_raises_engine_error(job: Job, peak: float) -> None:
    job.stack.outputs["Instrumental"] = lambda received: constant(peak)

    with pytest.raises(EngineError):
        job.run()


def test_stem_just_below_the_guard_passes(job: Job) -> None:
    job.stack.outputs["Instrumental"] = lambda received: constant(0.9998)

    stems = job.run()

    array, _, _ = job.stack.files[stems[StemName.INSTRUMENTAL]]
    assert close(array.frames[0][0], 1.9996)


def test_guard_is_checked_before_undoing_the_headroom(job: Job) -> None:
    # 0.6 x 2 = 1.2: a guard applied after undoing the headroom would wrongly fire.
    job.stack.outputs["Vocals"] = lambda received: constant(0.6)

    stems = job.run()

    array, _, _ = job.stack.files[stems[StemName.VOCALS]]
    assert close(array.frames[0][0], 1.2)


def test_stems_are_doubled_in_place_in_float32(job: Job) -> None:
    stems = job.run()

    assert stems == {
        StemName.VOCALS: job.work_dir / "vocals.wav",
        StemName.INSTRUMENTAL: job.work_dir / "instrumental.wav",
    }
    vocals, rate, subtype = job.stack.files[stems[StemName.VOCALS]]
    instrumental, _, _ = job.stack.files[stems[StemName.INSTRUMENTAL]]
    # engine stems are 0.3 and 0.7 of input x 0.5; doubled they sum back to the input
    assert close(vocals.frames[0], (0.24, -0.12))
    assert close(instrumental.frames[0], (0.56, -0.28))
    assert (vocals.dtype, rate, subtype) == ("float32", 44100, "FLOAT")


def test_doubled_stems_may_exceed_one_without_clipping(job: Job) -> None:
    job.stack.outputs["Instrumental"] = lambda received: constant(0.9)

    stems = job.run()

    array, _, subtype = job.stack.files[stems[StemName.INSTRUMENTAL]]
    assert close(array.frames[0][0], 1.8)
    assert subtype == "FLOAT"


# ── custom_output_names and missing outputs ─────────────────────────────────────────────


def test_custom_output_names_come_from_engine_labels(job: Job) -> None:
    model = get_model("vocals").model_copy(
        update={"engine_labels": {"Voz": StemName.VOCALS, "Resto": StemName.INSTRUMENTAL}}
    )
    job.stack.outputs = {
        "Voz": lambda received: received * 0.3,
        "Resto": lambda received: received * 0.7,
    }

    stems = job.run(model=model)

    _, _, names = job.separator.calls[1]
    assert names == {"Voz": "vocals", "Resto": "instrumental"}
    assert set(stems) == {StemName.VOCALS, StemName.INSTRUMENTAL}


def test_missing_output_raises_engine_error_naming_the_stem(job: Job) -> None:
    job.stack.missing = {"Instrumental"}

    with pytest.raises(EngineError) as exc:
        job.run()

    assert "instrumental" in str(exc.value)


# ── stages ──────────────────────────────────────────────────────────────────────────────


def test_success_notifies_loading_model_then_separating(job: Job) -> None:
    job.run()

    assert job.reporter.calls == [
        ("started", JobStage.LOADING_MODEL),
        ("finished", JobStage.LOADING_MODEL),
        ("started", JobStage.SEPARATING),
        ("finished", JobStage.SEPARATING),
    ]


def test_runs_without_a_reporter(job: Job) -> None:
    assert set(job.run(reporter=None)) == {StemName.VOCALS, StemName.INSTRUMENTAL}


def _break(job: Job, where: str) -> None:
    if where == "cuda":
        job.stack.device = "cuda"
    elif where == "missing_output":
        job.stack.missing = {"Vocals"}
    elif where == "guard":
        job.stack.outputs["Vocals"] = lambda received: constant(1.0)
    else:
        job.stack.fail_at = where


@pytest.mark.parametrize(
    ("where", "stage"),
    [
        ("import", JobStage.LOADING_MODEL),
        ("read_input", JobStage.LOADING_MODEL),
        ("constructor", JobStage.LOADING_MODEL),
        ("cuda", JobStage.LOADING_MODEL),
        ("load_model", JobStage.LOADING_MODEL),
        ("separate", JobStage.SEPARATING),
        ("missing_output", JobStage.SEPARATING),
        ("guard", JobStage.SEPARATING),
    ],
)
def test_failure_reports_stage_failed_once_with_the_raised_error(
    job: Job, where: str, stage: JobStage
) -> None:
    _break(job, where)

    with pytest.raises(EngineError) as exc:
        job.run()

    failed = [c for c in job.reporter.calls if c[0] == "failed"]
    assert failed == [("failed", stage, exc.value)]
    assert job.reporter.calls[-1] == ("failed", stage, exc.value)
    assert ("finished", stage) not in job.reporter.calls


@pytest.mark.parametrize("where", ["import", "constructor", "load_model", "separate"])
def test_library_errors_become_engine_errors_with_the_cause(job: Job, where: str) -> None:
    _break(job, where)

    with pytest.raises(EngineError) as exc:
        job.run()

    assert exc.value.__cause__ is not None
    assert exc.value.hint


def test_keyboard_interrupt_is_reported_and_propagated_unchanged(job: Job) -> None:
    interrupt = KeyboardInterrupt()
    job.stack.fail_at = "separate"
    job.stack.fail_with = interrupt

    with pytest.raises(KeyboardInterrupt) as exc:
        job.run()

    assert exc.value is interrupt
    assert job.reporter.calls[-1] == ("failed", JobStage.SEPARATING, interrupt)


# ── inference_device() (interfaces.md §3, v0.14) ────────────────────────────────────────


@pytest.mark.parametrize("device", ["cpu", "mps", "cuda"])
def test_inference_device_returns_what_the_engine_chose(stack: FakeStack, device: str) -> None:
    stack.device = device

    assert ase.AudioSeparatorEngine().inference_device() == device  # cuda does not raise


def test_inference_device_does_not_load_or_separate(stack: FakeStack) -> None:
    ase.AudioSeparatorEngine().inference_device()

    assert len(stack.separators) == 1
    assert stack.separators[0].calls == []
    assert stack.reads == [] and stack.writes == []


def test_inference_device_uses_a_temporary_directory_and_removes_it(stack: FakeStack) -> None:
    ase.AudioSeparatorEngine().inference_device()

    kwargs = stack.separators[0].kwargs
    model_dir = Path(kwargs["model_file_dir"])
    assert Path(kwargs["output_dir"]) == model_dir
    assert stack.separators[0].dirs_existed == {"model_file_dir": True, "output_dir": True}
    assert not model_dir.exists()


def test_inference_device_is_cached_in_the_instance(stack: FakeStack) -> None:
    engine = ase.AudioSeparatorEngine()

    first = engine.inference_device()
    stack.device = "mps"  # a second Separator would now answer differently
    second = engine.inference_device()

    assert (first, second) == ("cpu", "cpu")
    assert len(stack.separators) == 1


def test_engine_constants_match_the_contract() -> None:
    assert ase.ENGINE_INPUT_HEADROOM == 0.5
    assert ase.ENGINE_RESCALE_GUARD == 0.9999
