"""CLI `models` and `doctor` with CliRunner and doubles (T-13; interfaces.md §1, CA-04/14/15).

No network, FFmpeg or engine. The model directory comes from STEMSEPARATOR_MODEL_DIR under
tmp_path. For `doctor`, `cli.LocalSystemChecks` and `cli.AudioSeparatorEngine` are replaced:
the checks keep the real `require_ffmpeg()` over a PATH of stub executables, so the error
text is the real one, and report the versions they are given. A missing `audio-separator`
is a `None` in sys.modules, which makes `importlib.util.find_spec` return None without
touching the installed package. What must never be imported is checked in a subprocess,
because another test may already have imported the real modules.
"""

import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

import pytest
from typer.testing import CliRunner, Result

import stemseparator.cli as cli
from stemseparator import __version__
from stemseparator.domain.catalog import list_models
from stemseparator.media.system import LocalSystemChecks

MODEL_DIR_ENV = "STEMSEPARATOR_MODEL_DIR"
HEAVY_MODULES = ("audio_separator", "onnxruntime", "soundfile", "numpy", "torch")
REFERENCE = "Intel Core i7-8565U, 4 núcleos / 8 hilos, WSL2"
DOCTOR_LABELS = (
    "Python",
    "StemSeparator",
    "audio-separator",
    "ffmpeg",
    "ffprobe",
    "Hilos de CPU",
    "Caché de modelos",
    "Dispositivo",
)
INSTALL_SYSTEMS = ("Windows", "macOS", "Linux")


def _flat(text: str) -> str:
    """Whitespace collapsed: a value split across lines still reads as one string."""
    return " ".join(text.split())


@pytest.fixture
def model_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "models"
    monkeypatch.setenv(MODEL_DIR_ENV, str(path))
    return path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner(env={"COLUMNS": "250"})


def models(runner: CliRunner) -> Result:
    return runner.invoke(cli.app, ["models"])


def _block(stdout: str, model_id: str) -> list[str]:
    """Lines of the block that starts with `model_id`, up to the next blank line."""
    lines = stdout.splitlines()
    starts = [i for i, line in enumerate(lines) if line.split(" ")[0] == model_id]
    assert len(starts) == 1, stdout
    block: list[str] = []
    for line in lines[starts[0] :]:
        if not line.strip():
            break
        block.append(line)
    return block


# --- models: content (RF-10, CA-14) -------------------------------------------------------


def test_models_exits_0_and_writes_only_to_stdout(runner: CliRunner, model_dir: Path) -> None:
    result = models(runner)

    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip()
    assert result.stderr == ""


def test_models_lists_every_model_in_catalog_order(runner: CliRunner, model_dir: Path) -> None:
    result = models(runner)

    heads = [
        line.split(" ")[0]
        for line in result.stdout.splitlines()
        if line and not line[0].isspace() and line.split(" ")[0] in {m.id for m in list_models()}
    ]
    assert heads == [m.id for m in list_models()]


def test_models_marks_only_the_default_model(runner: CliRunner, model_dir: Path) -> None:
    result = models(runner)

    assert _block(result.stdout, "vocals")[0] == "vocals (por defecto)"
    assert _block(result.stdout, "vocals-max")[0] == "vocals-max"
    assert result.stdout.count("(por defecto)") == 1


def test_models_separates_blocks_with_a_blank_line(runner: CliRunner, model_dir: Path) -> None:
    result = models(runner)

    lines = result.stdout.splitlines()
    second = lines.index("vocals-max")
    assert lines[second - 1] == ""
    assert lines[second - 2].strip()  # exactly one blank line, not two


def test_models_shows_each_description(runner: CliRunner, model_dir: Path) -> None:
    result = models(runner)

    for model in list_models():
        assert _flat(model.description) in _flat("\n".join(_block(result.stdout, model.id)))


@pytest.mark.parametrize(
    ("model_id", "fields"),
    [
        (
            "vocals",
            (
                "  Stems:        vocals, instrumental",
                "  Coste en CPU: ≈2,4× (unos 8 min)",
                "  Licencia:     MIT (UVR), con atribución",
            ),
        ),
        (
            "vocals-max",
            (
                "  Stems:        vocals, instrumental",
                "  Coste en CPU: ≈37,1× (unas 2 h 10 min)",  # ADR-002's figure
                "  Licencia:     Sin licencia declarada",
            ),
        ),
    ],
)
def test_models_field_lines(
    runner: CliRunner, model_dir: Path, model_id: str, fields: tuple[str, ...]
) -> None:
    result = models(runner)

    block = _block(result.stdout, model_id)
    for field in fields:
        assert field in block, block
    labels = [line.split(":")[0].strip() for line in block if line.startswith("  ")]
    assert labels == ["Stems", "Coste en CPU", "Licencia", "Descargado"]


def test_models_ends_with_the_reference_machine_line(runner: CliRunner, model_dir: Path) -> None:
    result = models(runner)

    assert result.stdout.rstrip("\n").splitlines()[-1] == (
        f"Coste medido en el equipo de referencia ({REFERENCE}, ADR-002) para una canción de 3:30."
    )


# --- models: downloaded -------------------------------------------------------------------


def test_models_downloaded_is_no_when_the_directory_does_not_exist(
    runner: CliRunner, model_dir: Path
) -> None:
    result = models(runner)

    for model in list_models():
        assert "  Descargado:   no" in _block(result.stdout, model.id)
    assert not model_dir.exists()  # models only looks; it creates nothing


def test_models_downloaded_follows_the_engine_file(runner: CliRunner, model_dir: Path) -> None:
    model_dir.mkdir()
    vocals = next(m for m in list_models() if m.id == "vocals")
    (model_dir / vocals.engine_filename).write_bytes(b"weights")

    result = models(runner)

    assert "  Descargado:   sí" in _block(result.stdout, "vocals")
    assert "  Descargado:   no" in _block(result.stdout, "vocals-max")


def test_models_downloaded_looks_only_at_the_engine_file(
    runner: CliRunner, model_dir: Path
) -> None:
    # A directory named like the weights, or other files, do not count.
    model_dir.mkdir()
    vocals = next(m for m in list_models() if m.id == "vocals")
    (model_dir / vocals.engine_filename).mkdir()
    (model_dir / "otro.onnx").write_bytes(b"weights")

    result = models(runner)

    assert "  Descargado:   no" in _block(result.stdout, "vocals")


# --- models: nothing is cut (CA-14, interfaces.md §1) -------------------------------------


def test_models_at_80_columns_cuts_nothing(model_dir: Path) -> None:
    # The motive of the format change: a Rich table cut the license ("atribuc…") at 80.
    result = models(CliRunner(env={"COLUMNS": "80"}))

    assert result.exit_code == 0, result.stderr
    assert "…" not in result.stdout
    assert "..." not in result.stdout
    assert "MIT (UVR), con atribución" in result.stdout
    assert "Sin licencia declarada" in result.stdout
    for line in result.stdout.splitlines():
        assert len(line) <= 80, line  # the width was honoured: wrapped, not overflowing


@pytest.mark.parametrize("columns", ["30", "50", "80", "250"])
def test_models_loses_no_character_at_any_width(model_dir: Path, columns: str) -> None:
    result = models(CliRunner(env={"COLUMNS": columns}))

    assert result.exit_code == 0, result.stderr
    assert "…" not in result.stdout
    flat = _flat(result.stdout)
    for model in list_models():
        for value in (model.id, model.description, model.weights_license, model.cpu_reference):
            assert _flat(value) in flat, (columns, value)


# --- models: light (interfaces.md §1) ------------------------------------------------------


def test_models_does_not_build_the_engine_or_the_runtime(
    runner: CliRunner, model_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("models must not build the engine")

    monkeypatch.setattr(cli, "AudioSeparatorEngine", forbidden)
    monkeypatch.setattr(cli, "build_runtime", forbidden)

    result = models(runner)

    assert result.exit_code == 0, result.stderr
    assert "vocals" in result.stdout  # it really ran


class Invocation(NamedTuple):
    """What a command did in a fresh interpreter, each part asserted on its own."""

    exit_code: int
    output: str  # stdout and stderr, for the failure message
    error: str  # repr of the exception CliRunner caught, if any
    stdout: str
    heavy: list[str]  # heavy modules in sys.modules after the command


def _invoke_in_subprocess(command: str, setup: str, cwd: Path) -> Invocation:
    """Run `stemseparator <command>` with CliRunner in a new interpreter after `setup`.

    The interpreter itself must not fail, and reports everything as one JSON line, so a
    command that crashes is told apart from one that finishes having imported something.
    """
    code = (
        "import json, sys\n"
        "from typer.testing import CliRunner\n"
        "import stemseparator.cli as cli\n"
        f"{setup}"
        f"result = CliRunner().invoke(cli.app, [{command!r}])\n"
        f"heavy = {HEAVY_MODULES!r}\n"
        "print(json.dumps({\n"
        "    'exit_code': result.exit_code,\n"
        "    'output': result.output,\n"
        "    'error': repr(result.exception) if result.exception else '',\n"
        "    'stdout': result.stdout,\n"
        "    'heavy': sorted(m for m in sys.modules if m.split('.')[0] in heavy),\n"
        "}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
        env={**os.environ, MODEL_DIR_ENV: str(cwd / "models")},
    )
    assert completed.returncode == 0, f"the interpreter itself failed:\n{completed.stderr}"
    invocation = Invocation(**json.loads(completed.stdout.splitlines()[-1]))
    assert invocation.exit_code == 0, (
        f"{command} did not finish (exit {invocation.exit_code}, {invocation.error}): "
        "the import check below could not run\n" + invocation.output
    )
    return invocation


def test_models_imports_nothing_heavy(tmp_path: Path) -> None:
    invocation = _invoke_in_subprocess("models", "", tmp_path)

    assert "vocals-max" in invocation.stdout, invocation.stdout  # it really ran
    assert invocation.heavy == [], f"models imported heavy modules: {invocation.heavy}"
    assert not (tmp_path / ":memory:.ses").exists(), "onnxruntime telemetry file written"


# --- cpu cost (interfaces.md §1) -----------------------------------------------------------


@pytest.mark.parametrize(
    ("cpu_rtf", "expected"),
    [
        (2.4, "≈2,4× (unos 8 min)"),  # 504 s → 8,4 min
        (37.1, "≈37,1× (unas 2 h 10 min)"),  # 7 791 s → 129,85 min
        (3.0, "≈3,0× (unos 11 min)"),  # 10,5 min: ,5 rounds up (not to even)
        (16.9, "≈16,9× (unos 59 min)"),  # 59,15 min
        (17.0, "≈17,0× (unas 1 h)"),  # 59,5 → 60 min: hours from 60, no " 0 min"
        (34.0, "≈34,0× (unas 1 h 59 min)"),  # 119 min
        (0.1, "≈0,1× (menos de 1 min)"),  # 0,35 min → 0
    ],
)
def test_cpu_cost(cpu_rtf: float, expected: str) -> None:
    assert cli.cpu_cost(cpu_rtf) == expected


# --- doctor: doubles ----------------------------------------------------------------------


class Checks(LocalSystemChecks):
    """Real require_ffmpeg() over the stub PATH; ffmpeg_versions() as configured."""

    versions: Mapping[str, str | None] = {}

    def ffmpeg_versions(self) -> Mapping[str, str | None]:
        return dict(self.versions)


class Engine:
    """Stands in for AudioSeparatorEngine: counts constructions and device queries."""

    device = "cpu"
    built = 0
    queried = 0

    def __init__(self, verbose: bool = False) -> None:
        type(self).built += 1

    def inference_device(self) -> str:
        type(self).queried += 1
        return type(self).device


class Doctor:
    """Configures what doctor sees; `run()` invokes it."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._tmp_path = tmp_path
        self._monkeypatch = monkeypatch
        self.model_dir = tmp_path / "cache" / "models"
        monkeypatch.setenv(MODEL_DIR_ENV, str(self.model_dir))
        monkeypatch.setattr(os, "cpu_count", lambda: 8)

        class DoctorEngine(Engine):  # fresh counters for this test
            built = 0
            queried = 0

        class DoctorChecks(Checks):
            pass

        self.engine: type[Engine] = DoctorEngine
        self.checks: type[Checks] = DoctorChecks
        monkeypatch.setattr(cli, "AudioSeparatorEngine", DoctorEngine)
        monkeypatch.setattr(cli, "LocalSystemChecks", DoctorChecks)
        self.ffmpeg(ffmpeg="6.1.1-3ubuntu5", ffprobe="6.1.1-3ubuntu5")

    def ffmpeg(self, *, ffmpeg: str | None, ffprobe: str | None) -> None:
        """PATH holds exactly the binaries that have a version; the checks report them."""
        bin_dir = self._tmp_path / f"bin-{ffmpeg is not None}-{ffprobe is not None}"
        bin_dir.mkdir(exist_ok=True)
        for name, version in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)):
            if version is not None:
                for file_name in (name, f"{name}.exe"):
                    stub = bin_dir / file_name
                    stub.write_text("")
                    stub.chmod(0o755)
        self._monkeypatch.setenv("PATH", str(bin_dir))
        self._monkeypatch.setenv("PATHEXT", ".EXE")
        self.checks.versions = {"ffmpeg": ffmpeg, "ffprobe": ffprobe}

    def without_audio_separator(self) -> None:
        self._monkeypatch.setitem(sys.modules, "audio_separator", None)

    def run(self) -> Result:
        return CliRunner(env={"COLUMNS": "250"}).invoke(cli.app, ["doctor"])


@pytest.fixture
def doctor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Doctor:
    return Doctor(tmp_path, monkeypatch)


def _line(stdout: str, label: str) -> str:
    """The value on the report line for `label` (label, optional colon, spaces, value)."""
    matches = [
        line
        for line in stdout.splitlines()
        if line.startswith(label) and line[len(label) :][:1] in (":", " ")
    ]
    assert len(matches) == 1, (label, stdout)
    return matches[0][len(label) :].lstrip(":").strip()


AUDIO_SEPARATOR_VERSION = importlib.metadata.version("audio-separator")


# --- doctor: everything present (RF-11, CA-15) ---------------------------------------------


def test_doctor_all_present_exits_0_and_writes_only_to_stdout(doctor: Doctor) -> None:
    result = doctor.run()

    assert result.exit_code == 0, result.stderr
    assert result.stderr == ""


def test_doctor_reports_every_line_in_order(doctor: Doctor) -> None:
    result = doctor.run()

    lines = result.stdout.splitlines()
    starts = [
        next(i for i, line in enumerate(lines) if line.startswith(lb)) for lb in DOCTOR_LABELS
    ]
    assert starts == sorted(starts)
    assert len(set(starts)) == len(DOCTOR_LABELS)


def test_doctor_reports_the_values(doctor: Doctor) -> None:
    result = doctor.run()

    assert _line(result.stdout, "Python") == platform.python_version()
    assert _line(result.stdout, "StemSeparator") == __version__
    assert _line(result.stdout, "audio-separator") == AUDIO_SEPARATOR_VERSION
    assert _line(result.stdout, "ffmpeg") == "6.1.1-3ubuntu5"
    assert _line(result.stdout, "ffprobe") == "6.1.1-3ubuntu5"
    assert _line(result.stdout, "Hilos de CPU") == "8"
    assert _line(result.stdout, "Caché de modelos") == str(doctor.model_dir)
    assert _line(result.stdout, "Dispositivo") == "cpu"


def test_doctor_asks_the_engine_for_the_device_once(doctor: Doctor) -> None:
    doctor.engine.device = "mps"

    result = doctor.run()

    assert _line(result.stdout, "Dispositivo") == "mps"
    assert doctor.engine.queried == 1


def test_doctor_shows_the_model_cache_even_if_it_does_not_exist(doctor: Doctor) -> None:
    result = doctor.run()

    assert _line(result.stdout, "Caché de modelos") == str(doctor.model_dir)
    assert not doctor.model_dir.exists()


def test_doctor_unknown_cpu_count(doctor: Doctor, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: None)

    result = doctor.run()

    assert result.exit_code == 0, result.stderr
    assert _line(result.stdout, "Hilos de CPU") == "desconocido"


def test_doctor_unknown_ffmpeg_version_is_shown_and_still_present(doctor: Doctor) -> None:
    doctor.ffmpeg(ffmpeg="desconocida", ffprobe="6.1.1-3ubuntu5")

    result = doctor.run()

    assert result.exit_code == 0, result.stderr
    assert _line(result.stdout, "ffmpeg") == "desconocida"
    assert _line(result.stdout, "Dispositivo") == "cpu"


def test_doctor_cuda_is_reported_with_a_warning_and_exit_0(doctor: Doctor) -> None:
    doctor.engine.device = "cuda"

    result = doctor.run()

    assert result.exit_code == 0, result.stderr
    device = _line(result.stdout, "Dispositivo")
    assert device.startswith("cuda")
    assert "separate" in device
    assert "rechaz" in device


# --- doctor: something missing (RF-11, CA-04) ----------------------------------------------


@pytest.mark.parametrize(
    ("ffmpeg", "ffprobe", "missing"),
    [
        (None, "6.1.1", ("ffmpeg",)),
        ("6.1.1", None, ("ffprobe",)),
        (None, None, ("ffmpeg", "ffprobe")),
    ],
)
def test_doctor_missing_ffmpeg_exits_4_with_the_full_report(
    doctor: Doctor, ffmpeg: str | None, ffprobe: str | None, missing: tuple[str, ...]
) -> None:
    doctor.ffmpeg(ffmpeg=ffmpeg, ffprobe=ffprobe)

    result = doctor.run()

    assert result.exit_code == 4
    for label in DOCTOR_LABELS:  # the report is complete even when something is missing
        _line(result.stdout, label)
    for name in missing:
        assert _line(result.stdout, name) == "no encontrado"
    device = _line(result.stdout, "Dispositivo")
    assert device.startswith("no se pudo determinar (falta ")
    for name in missing:
        assert name in device
    assert "audio-separator" not in device
    assert doctor.engine.built == 0  # Separator would call check_ffmpeg_installed()
    assert doctor.engine.queried == 0


def test_ca04_doctor_missing_ffmpeg_shows_the_require_ffmpeg_error(doctor: Doctor) -> None:
    doctor.ffmpeg(ffmpeg=None, ffprobe="6.1.1")

    result = doctor.run()

    assert result.exit_code == 4
    assert "No se encontró ffmpeg en el PATH." in result.stderr
    for system_name in INSTALL_SYSTEMS:
        assert system_name in result.stderr
    assert "ffmpeg -version" in result.stderr
    assert "No se encontró ffmpeg" not in result.stdout  # the error goes to stderr only
    assert "Traceback" not in result.stderr


def test_doctor_missing_audio_separator_exits_4(doctor: Doctor) -> None:
    doctor.without_audio_separator()

    result = doctor.run()

    assert result.exit_code == 4
    for label in DOCTOR_LABELS:
        _line(result.stdout, label)
    assert _line(result.stdout, "audio-separator") == "no encontrado"
    assert _line(result.stdout, "Dispositivo") == "no se pudo determinar (falta audio-separator)"
    assert "No se encontró audio-separator." in result.stderr
    assert "Reinstala las dependencias de StemSeparator (`uv sync`)." in result.stderr
    assert "No se encontró ffmpeg" not in result.stderr  # FFmpeg is there
    assert doctor.engine.built == 0
    assert doctor.engine.queried == 0


def test_doctor_reports_ffmpeg_before_audio_separator(doctor: Doctor) -> None:
    doctor.ffmpeg(ffmpeg=None, ffprobe=None)
    doctor.without_audio_separator()

    result = doctor.run()

    assert result.exit_code == 4
    ffmpeg_at = result.stderr.find("No se encontraron ffmpeg ni ffprobe en el PATH.")
    audio_separator_at = result.stderr.find("No se encontró audio-separator.")
    assert 0 <= ffmpeg_at < audio_separator_at, result.stderr
    device = _line(result.stdout, "Dispositivo")
    for name in ("ffmpeg", "ffprobe", "audio-separator"):
        assert name in device


# --- doctor: audio-separator is never imported to check it (interfaces.md §1) -------------


def test_doctor_finds_audio_separator_without_importing_it(tmp_path: Path) -> None:
    # Everything present, so the presence check is the only thing that could import it:
    # the engine is a stub, and the checks report both binaries without running them.
    setup = (
        "class Checks:\n"
        "    def require_ffmpeg(self): pass\n"
        "    def free_bytes(self, path): return 0\n"
        "    def ffmpeg_versions(self): return {'ffmpeg': '6.1.1', 'ffprobe': '6.1.1'}\n"
        "class Engine:\n"
        "    def __init__(self, verbose=False): pass\n"
        "    def inference_device(self): return 'cpu'\n"
        "cli.LocalSystemChecks = Checks\n"
        "cli.AudioSeparatorEngine = Engine\n"
    )
    invocation = _invoke_in_subprocess("doctor", setup, tmp_path)

    assert _line(invocation.stdout, "audio-separator") == AUDIO_SEPARATOR_VERSION  # it ran
    assert invocation.heavy == [], f"doctor imported heavy modules: {invocation.heavy}"
    assert not (tmp_path / ":memory:.ses").exists(), "onnxruntime telemetry file written"
