"""scripts/evaluate.py, T-17: candidates and one process per pair (i2-calidad.md RF-14, RF-15).

No network, models or FFmpeg: the process launcher, the adapters and the clock reader are
doubles. The script is loaded from its path because scripts/ is not a package
(architecture.md §2, T-16).
"""

import importlib.util
import json
import math
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from stemseparator.application.service import output_gain
from stemseparator.domain.catalog import get_model, list_models
from stemseparator.domain.models import MediaInfo, MediaKind, ModelSpec, StemName
from stemseparator.domain.progress import JobStage, ProgressReporter

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "evaluate.py"

PAIR_FIELDS = {
    "status",
    "fragment",
    "model_id",
    "engine_filename",
    "audio_s",
    "load_s",
    "separate_s",
    "rtf",
    "peak_rss_kib",
    "output_gain",
    "threads",
    "snr_db",
    "tick_before",
    "tick_after",
    "clock_reliable",
}


@pytest.fixture(scope="module")
def ev() -> ModuleType:
    spec = importlib.util.spec_from_file_location("evaluate_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses and pickling need the module registered
    spec.loader.exec_module(module)
    return module


def fake_model(model_id: str) -> ModelSpec:
    return get_model("vocals").model_copy(
        update={"id": model_id, "engine_filename": f"{model_id}.onnx", "is_default": False}
    )


# --- doubles ------------------------------------------------------------------------------


class Normalizer:
    def normalize(self, src_path: Path, dest: Path) -> Path:
        dest.write_bytes(src_path.read_bytes())
        return dest


class Probe:
    def probe(self, path: Path) -> MediaInfo:
        return MediaInfo(
            path=path,
            kind=MediaKind.AUDIO,
            duration_s=30.0,
            has_audio=True,
            sample_rate=44100,
            channels=2,
            codec="pcm_f32le",
        )


class Engine:
    """Notifies both stages with fixed elapsed times and writes one file per stem."""

    def __init__(self, load_s: float = 3.0, separate_s: float = 60.0) -> None:
        self.load_s, self.separate_s = load_s, separate_s

    def separate(
        self,
        audio_path: Path,
        model: ModelSpec,
        work_dir: Path,
        model_dir: Path,
        reporter: ProgressReporter | None = None,
    ) -> dict[StemName, Path]:
        assert reporter is not None
        reporter.stage_started(JobStage.LOADING_MODEL)
        reporter.stage_finished(JobStage.LOADING_MODEL, self.load_s)
        reporter.stage_started(JobStage.SEPARATING)
        outputs = {stem: work_dir / f"{stem.value}.wav" for stem in model.stems}
        for path in outputs.values():
            path.write_bytes(b"stem")
        reporter.stage_finished(JobStage.SEPARATING, self.separate_s)
        return outputs


class Exporter:
    def __init__(self, peaks: dict[str, float]) -> None:
        self.peaks = peaks

    def peak(self, src_wav: Path) -> float:
        return self.peaks[src_wav.stem]


def ticks(*values: int | None) -> Any:
    it = iter(values)
    return lambda: next(it)


def measure(ev: ModuleType, fragment: Path, model: ModelSpec, tmp: Path, **kw: Any) -> Any:
    args: dict[str, Any] = {
        "normalizer": Normalizer(),
        "probe": Probe(),
        "engine": Engine(),
        "exporter": Exporter({"vocals": 0.5, "instrumental": 1.2}),
        "snr": lambda work, stems: 44.0,
        "read_tick": ticks(10000, 10000),
        "max_rss_kib": lambda: 3_000_000,
    }
    args.update(kw)
    return ev.measure_pair(fragment, model, tmp / "work", tmp / "models", **args)


class Launcher:
    """Simulated process launcher: runs the child function in-process with the doubles."""

    def __init__(self, ev: ModuleType, tmp: Path, models: Sequence[ModelSpec]) -> None:
        self.ev, self.tmp, self.models = ev, tmp, {m.id: m for m in models}
        self.calls: list[tuple[str, str]] = []
        self.fail: set[tuple[str, str]] = set()

    def __call__(self, argv: Sequence[str]) -> tuple[int, str]:
        args = list(argv)
        fragment, model_id = Path(args[args.index("--pair") + 1]), args[args.index("--pair") + 2]
        record = Path(args[args.index("--record") + 1])
        self.calls.append((fragment.name, model_id))
        if (fragment.name, model_id) in self.fail:
            return 1, "EngineError: simulated failure"
        result = measure(self.ev, fragment, self.models[model_id], self.tmp / model_id)
        record.write_text(json.dumps(result))
        return 0, ""


@pytest.fixture
def eval_dir(tmp_path: Path) -> Path:
    fragments = tmp_path / "eval" / "fragments"
    fragments.mkdir(parents=True)
    for name in ("a_pop.wav", "b_rock.wav"):
        (fragments / name).write_bytes(b"RIFF")
    return tmp_path / "eval"


# --- candidates (RF-14) -------------------------------------------------------------------


def test_candidates_are_the_two_catalog_models_and_the_i2_candidate(ev: ModuleType) -> None:
    by_id = {m.id: m for m in ev.CANDIDATES}
    assert len(by_id) == len(ev.CANDIDATES) == 3
    assert by_id["vocals"] == get_model("vocals")
    assert by_id["vocals-max"] == get_model("vocals-max")
    candidate = by_id[ev.CANDIDATE_ID]
    assert candidate.engine_filename == "vocals_mel_band_roformer.ckpt"
    # Labels checked against the real engine on 2026-11-08 (T-17): lower case, "other".
    assert dict(candidate.engine_labels) == {
        "vocals": StemName.VOCALS,
        "other": StemName.INSTRUMENTAL,
    }


def test_candidate_placeholders_cannot_pass_for_measurements(ev: ModuleType) -> None:
    candidate = next(m for m in ev.CANDIDATES if m.id == ev.CANDIDATE_ID)
    assert candidate.cpu_rtf == 0.0
    assert candidate.download_size_bytes == 0
    assert candidate.description == "candidato de I2"
    assert not candidate.is_default


def test_the_catalog_does_not_change(ev: ModuleType) -> None:  # CA-29, catalog part
    assert [m.id for m in list_models()] == ["vocals", "vocals-max"]


# --- plan: rotation (RF-15) ---------------------------------------------------------------


def test_model_order_rotates_by_fragment(ev: ModuleType, tmp_path: Path) -> None:
    frags = [tmp_path / f"{i}.wav" for i in range(4)]
    models = [fake_model(x) for x in ("m0", "m1", "m2")]

    order = [(f.name, m.id) for f, m in ev.plan(frags, models)]

    assert order == [
        ("0.wav", "m0"), ("0.wav", "m1"), ("0.wav", "m2"),
        ("1.wav", "m1"), ("1.wav", "m2"), ("1.wav", "m0"),
        ("2.wav", "m2"), ("2.wav", "m0"), ("2.wav", "m1"),
        ("3.wav", "m0"), ("3.wav", "m1"), ("3.wav", "m2"),
    ]  # fmt: skip


def test_fragments_are_the_sorted_wavs_of_the_fragments_dir(ev: ModuleType, eval_dir: Path) -> None:
    (eval_dir / "fragments" / "notes.txt").write_text("not audio")
    assert [f.name for f in ev.fragments(eval_dir)] == ["a_pop.wav", "b_rock.wav"]


# --- one pair, in its own process (RF-15) -------------------------------------------------


def test_measure_pair_records_every_field(ev: ModuleType, tmp_path: Path) -> None:
    fragment = tmp_path / "a_pop.wav"
    fragment.write_bytes(b"RIFF")

    record = measure(ev, fragment, fake_model("m0"), tmp_path)

    assert set(record) == PAIR_FIELDS
    assert record["status"] == "ok"
    assert (record["fragment"], record["model_id"]) == ("a_pop.wav", "m0")
    assert (record["audio_s"], record["load_s"], record["separate_s"]) == (30.0, 3.0, 60.0)
    assert record["rtf"] == pytest.approx((3.0 + 60.0) / 30.0)
    assert record["output_gain"] == output_gain(1.2)  # the max peak of the engine stems
    assert record["peak_rss_kib"] == 3_000_000
    assert record["snr_db"] == 44.0
    assert record["clock_reliable"] is True
    assert set(record["threads"]) == {"torch", "os_cpu_count"}


@pytest.mark.parametrize(
    ("before", "after", "reliable"),
    [(10000, 10000, True), (10000, 10500, False), (9374, 10000, False), (None, None, False)],
)
def test_clock_is_reliable_only_with_tick_10000_before_and_after(
    ev: ModuleType, tmp_path: Path, before: int | None, after: int | None, reliable: bool
) -> None:
    fragment = tmp_path / "a.wav"
    fragment.write_bytes(b"RIFF")

    record = measure(ev, fragment, fake_model("m0"), tmp_path, read_tick=ticks(before, after))

    assert (record["tick_before"], record["tick_after"]) == (before, after)
    assert record["clock_reliable"] is reliable


def test_measure_pair_leaves_no_work_files(ev: ModuleType, tmp_path: Path) -> None:
    fragment = tmp_path / "a.wav"
    fragment.write_bytes(b"RIFF")

    measure(ev, fragment, fake_model("m0"), tmp_path)

    assert not (tmp_path / "work").exists() or not any((tmp_path / "work").iterdir())


# --- CA-27: 2 fragments x 2 candidates, one process per pair ------------------------------


def test_ca27_four_pairs_one_process_each(ev: ModuleType, eval_dir: Path) -> None:
    models = [fake_model("m0"), fake_model("m1")]
    launcher = Launcher(ev, eval_dir.parent, models)

    ev.run(eval_dir, models, launcher, eval_dir.parent / "models")

    assert launcher.calls == [
        ("a_pop.wav", "m0"), ("a_pop.wav", "m1"), ("b_rock.wav", "m1"), ("b_rock.wav", "m0"),
    ]  # fmt: skip
    results = json.loads((eval_dir / "out" / "results.json").read_text())
    assert set(results["pairs"]) == {
        "a_pop.wav|m0", "a_pop.wav|m1", "b_rock.wav|m0", "b_rock.wav|m1",
    }  # fmt: skip
    for record in results["pairs"].values():
        assert set(record) == PAIR_FIELDS
        assert record["status"] == "ok"


def test_ca27_each_launch_is_a_python_child_of_this_script(ev: ModuleType, eval_dir: Path) -> None:
    models = [fake_model("m0")]
    seen: list[list[str]] = []

    def launcher(argv: Sequence[str]) -> tuple[int, str]:
        seen.append(list(argv))
        return 1, "not run"

    ev.run(eval_dir, models, launcher, eval_dir.parent / "models")

    assert len(seen) == 2
    for argv in seen:
        assert argv[:2] == [sys.executable, str(SCRIPT)]
        assert "--pair" in argv and "--record" in argv and "--model-dir" in argv


# --- CA-28: resume and failures -----------------------------------------------------------


def test_ca28_a_failed_pair_is_recorded_and_the_rest_run(ev: ModuleType, eval_dir: Path) -> None:
    models = [fake_model("m0"), fake_model("m1")]
    launcher = Launcher(ev, eval_dir.parent, models)
    launcher.fail = {("a_pop.wav", "m1")}

    ev.run(eval_dir, models, launcher, eval_dir.parent / "models")

    assert len(launcher.calls) == 4
    pairs = json.loads((eval_dir / "out" / "results.json").read_text())["pairs"]
    assert pairs["a_pop.wav|m1"] == {
        "status": "error",
        "fragment": "a_pop.wav",
        "model_id": "m1",
        "error": "EngineError: simulated failure",
    }
    assert all(pairs[k]["status"] == "ok" for k in pairs if k != "a_pop.wav|m1")


def test_ca28_rerun_skips_ok_pairs_and_retries_failed_ones(ev: ModuleType, eval_dir: Path) -> None:
    models = [fake_model("m0"), fake_model("m1")]
    first = Launcher(ev, eval_dir.parent, models)
    first.fail = {("b_rock.wav", "m0")}
    ev.run(eval_dir, models, first, eval_dir.parent / "models")

    second = Launcher(ev, eval_dir.parent, models)
    ev.run(eval_dir, models, second, eval_dir.parent / "models")

    assert second.calls == [("b_rock.wav", "m0")]
    pairs = json.loads((eval_dir / "out" / "results.json").read_text())["pairs"]
    assert all(record["status"] == "ok" for record in pairs.values())


def test_ca28_a_child_that_exits_0_without_a_record_is_a_failure(
    ev: ModuleType, eval_dir: Path
) -> None:
    ev.run(eval_dir, [fake_model("m0")], lambda argv: (0, ""), eval_dir.parent / "models")

    pairs = json.loads((eval_dir / "out" / "results.json").read_text())["pairs"]
    assert {r["status"] for r in pairs.values()} == {"error"}


def test_results_are_saved_after_every_pair(ev: ModuleType, eval_dir: Path) -> None:
    models = [fake_model("m0")]
    results = eval_dir / "out" / "results.json"
    seen_on_disk: list[int] = []
    inner = Launcher(ev, eval_dir.parent, models)

    def launcher(argv: Sequence[str]) -> tuple[int, str]:
        seen_on_disk.append(
            len(json.loads(results.read_text())["pairs"]) if results.exists() else 0
        )
        return inner(argv)

    ev.run(eval_dir, models, launcher, eval_dir.parent / "models")

    assert seen_on_disk == [0, 1]  # an interrupted run keeps the pairs already done


def test_everything_written_stays_under_eval(ev: ModuleType, eval_dir: Path) -> None:
    models = [fake_model("m0"), fake_model("m1")]
    before = {p for p in eval_dir.parent.rglob("*")}

    ev.run(eval_dir, models, Launcher(ev, eval_dir.parent, models), eval_dir.parent / "models")

    new = {p for p in eval_dir.parent.rglob("*")} - before
    launcher_dirs = {eval_dir.parent / "m0", eval_dir.parent / "m1"}  # the double's own
    written = {p for p in new if not any(d == p or d in p.parents for d in launcher_dirs)}
    assert written and all(eval_dir in p.parents for p in written)


# --- reconstruction SNR (CA-25 definition) ------------------------------------------------


def test_reconstruction_snr_of_known_residual(ev: ModuleType, tmp_path: Path) -> None:
    np = pytest.importorskip("numpy")
    sf = pytest.importorskip("soundfile")
    rng = np.random.default_rng(0)
    work = rng.uniform(-0.5, 0.5, size=(44100, 2)).astype("float32")
    vocals = 0.3 * work
    residual = 0.001 * rng.standard_normal(size=work.shape).astype("float32")
    instrumental = 0.7 * work + residual
    for name, data in (("work", work), ("vocals", vocals), ("instrumental", instrumental)):
        sf.write(tmp_path / f"{name}.wav", data, 44100, subtype="FLOAT")
    expected = 20 * math.log10(
        float(np.sqrt(np.mean(work.astype("float64") ** 2)))
        / float(np.sqrt(np.mean(residual.astype("float64") ** 2)))
    )

    snr = ev.reconstruction_snr_db(
        tmp_path / "work.wav",
        {
            StemName.VOCALS: tmp_path / "vocals.wav",
            StemName.INSTRUMENTAL: tmp_path / "instrumental.wav",
        },
    )

    assert snr == pytest.approx(expected, abs=0.01)
