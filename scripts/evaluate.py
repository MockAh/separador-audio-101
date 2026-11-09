"""I2 model evaluation (specs/i2-calidad.md): candidates and one process per pair (T-17).

Usage:
    uv run python scripts/evaluate.py                 # every fragment x every candidate
    (internal) evaluate.py --pair FRAGMENT MODEL_ID --record PATH --model-dir DIR

Each (fragment, model) pair runs in its own child process, so the peak RSS is per pair,
and is recorded in eval/out/results.json. Pairs already done are skipped; failed pairs are
recorded and retried on the next run (RF-15). Not installed with the package (RF-14).
"""

import argparse
import ctypes
import importlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from stemseparator.application.service import output_gain
from stemseparator.domain.catalog import get_model
from stemseparator.domain.models import MediaInfo, ModelSpec, StemName
from stemseparator.domain.progress import JobStage, ProgressReporter

SCRIPT = Path(__file__).resolve()

CANDIDATE_ID = "vocals-melband"
CANDIDATES: tuple[ModelSpec, ...] = (
    get_model("vocals"),
    get_model("vocals-max"),
    ModelSpec(
        id=CANDIDATE_ID,
        engine_filename="vocals_mel_band_roformer.ckpt",  # SHA-256 checked in T-16
        stems=(StemName.VOCALS, StemName.INSTRUMENTAL),
        # The engine labels them "vocals" and "other" (checked with the real engine, T-17).
        engine_labels={"vocals": StemName.VOCALS, "other": StemName.INSTRUMENTAL},
        # Placeholders until measured: they cannot pass for measurements (RF-14).
        cpu_rtf=0.0,
        cpu_reference="sin medir",
        weights_license="MIT declarada en KimberleyJSN/melbandroformer",
        download_size_bytes=0,
        description="candidato de I2",
    ),
)

NOMINAL_TICK = 10000  # kernel tick with a healthy clock (T-15)
RESULTS_NAME = "results.json"
_ERROR_TAIL_CHARS = 2000

Record = dict[str, Any]
Launcher = Callable[[Sequence[str]], tuple[int, str]]


class Normalizer(Protocol):
    def normalize(self, src_path: Path, dest: Path) -> Path: ...


class Probe(Protocol):
    def probe(self, path: Path) -> MediaInfo: ...


class Engine(Protocol):
    def separate(
        self,
        audio_path: Path,
        model: ModelSpec,
        work_dir: Path,
        model_dir: Path,
        reporter: ProgressReporter | None = None,
    ) -> dict[StemName, Path]: ...


class PeakReader(Protocol):
    def peak(self, src_wav: Path) -> float: ...


# --- plan ---------------------------------------------------------------------------------


def fragments(eval_dir: Path) -> list[Path]:
    """The WAV fragments of eval/fragments, sorted by name."""
    return sorted(p for p in (eval_dir / "fragments").iterdir() if p.suffix.lower() == ".wav")


def plan(frags: Sequence[Path], models: Sequence[ModelSpec]) -> list[tuple[Path, ModelSpec]]:
    """Every pair; in fragment i the model list starts at model i mod n (RF-15)."""
    n = len(models)
    return [(f, models[(i + j) % n]) for i, f in enumerate(frags) for j in range(n)]


def pair_key(fragment: Path, model: ModelSpec) -> str:
    return f"{fragment.name}|{model.id}"


# --- one pair (child process) -------------------------------------------------------------


class _StageTimer:
    """Keeps the elapsed time the engine reports for each stage."""

    def __init__(self) -> None:
        self.elapsed: dict[JobStage, float] = {}

    def stage_started(self, stage: JobStage) -> None:
        pass

    def stage_finished(self, stage: JobStage, elapsed_s: float) -> None:
        self.elapsed[stage] = elapsed_s

    def stage_failed(self, stage: JobStage, error: BaseException) -> None:
        pass


def measure_pair(
    fragment: Path,
    model: ModelSpec,
    work_dir: Path,
    model_dir: Path,
    *,
    normalizer: Normalizer,
    probe: Probe,
    engine: Engine,
    exporter: PeakReader,
    snr: Callable[[Path, Mapping[StemName, Path]], float],
    read_tick: Callable[[], int | None],
    max_rss_kib: Callable[[], int | None],
) -> Record:
    """Separate one fragment with one model and return its record (RF-15)."""
    tick_before = read_tick()
    work_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="pair-", dir=work_dir))
    try:
        work_audio = normalizer.normalize(fragment, tmp / "work.wav")
        audio_s = probe.probe(work_audio).duration_s
        engine_dir = tmp / "engine"
        engine_dir.mkdir()
        timer = _StageTimer()
        stems = engine.separate(work_audio, model, engine_dir, model_dir, timer)
        load_s = timer.elapsed[JobStage.LOADING_MODEL]
        separate_s = timer.elapsed[JobStage.SEPARATING]
        gain = output_gain(max((exporter.peak(p) for p in stems.values()), default=0.0))
        snr_db = snr(work_audio, stems)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    tick_after = read_tick()
    torch = sys.modules.get("torch")  # loaded by the engine, if at all; never imported here
    return {
        "status": "ok",
        "fragment": fragment.name,
        "model_id": model.id,
        "engine_filename": model.engine_filename,
        "audio_s": audio_s,
        "load_s": load_s,
        "separate_s": separate_s,
        "rtf": (load_s + separate_s) / audio_s,
        "peak_rss_kib": max_rss_kib(),
        "output_gain": gain,
        "threads": {
            "torch": torch.get_num_threads() if torch is not None else None,
            "os_cpu_count": os.cpu_count(),
        },
        "snr_db": snr_db,
        "tick_before": tick_before,
        "tick_after": tick_after,
        "clock_reliable": tick_before == NOMINAL_TICK and tick_after == NOMINAL_TICK,
    }


def reconstruction_snr_db(work_audio: Path, stems: Mapping[StemName, Path]) -> float:
    """SNR of the sum of the engine stems against the working audio (CA-25 definition).

    The engine stems are not scaled by output_gain yet, so no division is needed.
    """
    np: Any = importlib.import_module("numpy")  # no usable stubs with mypy's 3.11 target
    sf: Any = importlib.import_module("soundfile")
    work = sf.read(work_audio, dtype="float64", always_2d=True)[0]
    parts = [sf.read(path, dtype="float64", always_2d=True)[0] for path in stems.values()]
    n = min(len(work), *(len(p) for p in parts))
    residual = work[:n] - sum(p[:n] for p in parts)

    def rms(x: Any) -> float:
        return float(np.sqrt(np.mean(np.square(x))))

    return 20 * float(np.log10(rms(work[:n]) / max(rms(residual), 1e-12)))


def read_kernel_tick() -> int | None:
    """`tick` from adjtimex(2) on Linux (x86_64 struct timex); None elsewhere or on error."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        buf = ctypes.create_string_buffer(256)
        ctypes.CDLL(None).adjtimex(buf)
        return int(struct.unpack_from("l", buf, 88)[0])
    except (OSError, AttributeError):
        return None


def max_rss_kib() -> int | None:
    """Peak RSS of this process in KiB (ru_maxrss: KiB on Linux, bytes on macOS)."""
    try:
        resource = importlib.import_module("resource")
    except ImportError:  # Windows
        return None
    peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return peak // 1024 if sys.platform == "darwin" else peak


# --- all pairs (parent process) -----------------------------------------------------------


def _load_results(path: Path) -> Record:
    if path.is_file():
        loaded: Record = json.loads(path.read_text(encoding="utf-8"))
        return loaded
    return {"pairs": {}}


def _save_results(path: Path, results: Record) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)  # an interrupted write never leaves a truncated results.json


def run(eval_dir: Path, models: Sequence[ModelSpec], launcher: Launcher, model_dir: Path) -> Record:
    """Run every pending pair, one child process each, saving after every pair (RF-15)."""
    out = eval_dir / "out"
    records = out / "records"
    records.mkdir(parents=True, exist_ok=True)
    results_path = out / RESULTS_NAME
    results = _load_results(results_path)
    pending = [
        (f, m)
        for f, m in plan(fragments(eval_dir), models)
        if results["pairs"].get(pair_key(f, m), {}).get("status") != "ok"
    ]
    for number, (fragment, model) in enumerate(pending, start=1):
        print(f"[{number}/{len(pending)}] {fragment.name} · {model.id}", flush=True)
        record_path = records / f"pair-{number}.json"  # no model id in any file name
        record_path.unlink(missing_ok=True)
        argv = [
            sys.executable, str(SCRIPT),
            "--pair", str(fragment), model.id,
            "--record", str(record_path),
            "--model-dir", str(model_dir),
        ]  # fmt: skip
        code, stderr = launcher(argv)
        if code == 0 and record_path.is_file():
            record: Record = json.loads(record_path.read_text(encoding="utf-8"))
        else:
            reason = stderr.strip()[-_ERROR_TAIL_CHARS:] or f"exit code {code}, no record"
            record = {
                "status": "error",
                "fragment": fragment.name,
                "model_id": model.id,
                "error": reason,
            }
        record_path.unlink(missing_ok=True)
        results["pairs"][pair_key(fragment, model)] = record
        _save_results(results_path, results)
    return results


def subprocess_launcher(argv: Sequence[str]) -> tuple[int, str]:
    proc = subprocess.run(list(argv), capture_output=True, text=True, check=False)
    return proc.returncode, proc.stderr


# --- entry point --------------------------------------------------------------------------


def _child(fragment: Path, model_id: str, record: Path, model_dir: Path) -> None:
    from stemseparator.engines.audio_separator_engine import AudioSeparatorEngine
    from stemseparator.media.ffmpeg import (
        FFmpegAudioNormalizer,
        FFmpegStemExporter,
        FFprobeMediaProbe,
    )

    model = next(m for m in CANDIDATES if m.id == model_id)
    result = measure_pair(
        fragment,
        model,
        record.parent / "work",
        model_dir,
        normalizer=FFmpegAudioNormalizer(),
        probe=FFprobeMediaProbe(),
        engine=AudioSeparatorEngine(),
        exporter=FFmpegStemExporter(),
        snr=reconstruction_snr_db,
        read_tick=read_kernel_tick,
        max_rss_kib=max_rss_kib,
    )
    record.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluación de modelos de I2.")
    parser.add_argument("--eval-dir", type=Path, default=Path("eval"))
    parser.add_argument("--pair", nargs=2, metavar=("FRAGMENT", "MODEL_ID"))
    parser.add_argument("--record", type=Path)
    parser.add_argument("--model-dir", type=Path)
    args = parser.parse_args(argv)

    if args.pair is not None:
        fragment, model_id = args.pair
        _child(Path(fragment), model_id, args.record, args.model_dir)
        return 0

    from stemseparator.media.system import default_model_dir

    results = run(args.eval_dir, CANDIDATES, subprocess_launcher, default_model_dir())
    failed = [k for k, r in results["pairs"].items() if r["status"] != "ok"]
    unreliable = [k for k, r in results["pairs"].items() if r.get("clock_reliable") is False]
    total = len(results["pairs"])
    print(f"Pares: {total}; fallidos: {len(failed)}; reloj no fiable: {len(unreliable)}")
    for key in failed:
        print(f"  fallido: {key}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
