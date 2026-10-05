"""Atomic publication of ADR-004 (T-10), on real files in tmp_path.

No network, FFmpeg or engine. Faults are injected in os.replace, shutil.copyfile and
os.unlink through `fs_faults` (conftest.py), in both modes: same volume (os.replace
straight to the destination) and different volumes (os.replace raises EXDEV, so the file
is copied to a `.stemseparator-tmp-<job>-<name>` in the output directory first). Exit codes
are decided by the CLI (T-12): here the exception TYPE and its class exit_code are checked.
SIGINT is raised for real with signal.raise_signal; the autouse fixture of conftest.py
fails any test that leaves a different SIGINT handler behind.
"""

import logging
import os
import re
import signal
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from stemseparator.domain.errors import ExportError, OutputRestoreError
from tests.conftest import TMP_PREFIX, FsFaults, sigint_must_be_deferred

NAMES = ("tema_vocals.wav", "tema_instrumental.wav")
NEW = {name: f"new {name}".encode() for name in NAMES}
ORIGINAL = {name: f"original {name}".encode() for name in NAMES}
JOB_TOKEN = re.compile(r"^\.stemseparator-tmp-([0-9a-f]{8})-")

Publish = Callable[..., Any]


@pytest.fixture
def publish() -> Publish:
    """The function under test, imported per test so a missing module fails each one."""
    from stemseparator.application.publication import publish as publish_fn

    return publish_fn


@pytest.fixture
def exported(tmp_path: Path) -> list[Path]:
    """Checked files waiting in the work directory (work_dir/export)."""
    export_dir = tmp_path / "work" / "export"
    export_dir.mkdir(parents=True)
    files = []
    for name in NAMES:
        path = export_dir / name
        path.write_bytes(NEW[name])
        files.append(path)
    return files


@pytest.fixture
def out(tmp_path: Path) -> Path:
    """Output directory with an unrelated user file."""
    path = tmp_path / "out"
    path.mkdir()
    (path / "otro.txt").write_bytes(b"del usuario")
    return path


@pytest.fixture
def originals(out: Path) -> dict[str, bytes]:
    """Both destinations already exist (a run with --overwrite)."""
    for name in NAMES:
        (out / name).write_bytes(ORIGINAL[name])
    return snapshot(out)


def snapshot(directory: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir())}


def leftovers(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir() if p.name.startswith(TMP_PREFIX))


def raise_sigint() -> None:
    signal.raise_signal(signal.SIGINT)


VOLUMES = pytest.mark.parametrize("cross_volume", [False, True], ids=["same-vol", "cross-vol"])


# --- success ----------------------------------------------------------------------------


@VOLUMES
def test_publish_replaces_existing_files_and_leaves_no_tmp(
    publish: Publish,
    exported: list[Path],
    out: Path,
    originals: dict[str, bytes],
    fs_faults: FsFaults,
    cross_volume: bool,
) -> None:
    fs_faults.cross_volume = cross_volume

    result = publish(exported, out)

    assert list(result.paths) == [out / name for name in NAMES]
    assert result.interrupted is False
    assert snapshot(out) == {"otro.txt": b"del usuario", **NEW}
    assert leftovers(out) == []
    assert fs_faults.count("backup") == 2
    assert fs_faults.count("copy") == (2 if cross_volume else 0)


def test_cross_volume_copies_to_prefixed_temp_in_output_dir_then_replaces(
    publish: Publish, exported: list[Path], out: Path, fs_faults: FsFaults
) -> None:
    fs_faults.cross_volume = True

    publish(exported, out)

    copies = fs_faults.of("copy")
    assert [c[1] for c in copies] == exported
    for (_, _, tmp), name in zip(copies, NAMES, strict=True):
        assert tmp is not None
        assert tmp.parent == out
        assert JOB_TOKEN.match(tmp.name)
        assert tmp.name.endswith(f"-{name}")
    final = [(c[1], c[2]) for c in fs_faults.of("to_dest") if c[1].parent == out]
    assert final == [(c[2], out / name) for c, name in zip(copies, NAMES, strict=True)]


def test_publish_without_existing_files_makes_no_backup(
    publish: Publish, exported: list[Path], out: Path, fs_faults: FsFaults
) -> None:
    publish(exported, out)

    assert fs_faults.count("backup") == 0
    assert snapshot(out) == {"otro.txt": b"del usuario", **NEW}


# --- injected failure in each move (ADR-004) --------------------------------------------


@VOLUMES
@pytest.mark.parametrize("failing", [("backup", 1), ("backup", 2), ("to_dest", 2)])
def test_failed_move_restores_every_original(
    publish: Publish,
    exported: list[Path],
    out: Path,
    originals: dict[str, bytes],
    fs_faults: FsFaults,
    cross_volume: bool,
    failing: tuple[str, int],
) -> None:
    fs_faults.cross_volume = cross_volume
    if failing[0] == "to_dest" and cross_volume:
        # Across volumes the first os.replace of each stem is the EXDEV one: the second
        # stem's final replace (from the prefixed copy) is call 4.
        failing = ("to_dest", 4)
    fs_faults.faults[failing] = "fail"

    with pytest.raises(ExportError) as excinfo:
        publish(exported, out)

    assert excinfo.value.exit_code == 5
    assert isinstance(excinfo.value.__cause__, OSError)
    assert snapshot(out) == originals  # byte for byte, and nothing new
    assert leftovers(out) == []


def test_failed_first_backup_publishes_nothing_and_keeps_user_file_in_place(
    publish: Publish,
    exported: list[Path],
    out: Path,
    originals: dict[str, bytes],
    fs_faults: FsFaults,
) -> None:
    fs_faults.faults[("backup", 1)] = "fail"

    with pytest.raises(ExportError):
        publish(exported, out)

    assert fs_faults.count("to_dest") == 0
    assert fs_faults.count("restore") == 0  # it never moved: nothing to put back
    assert snapshot(out) == originals


@pytest.mark.parametrize("action", ["fail", "partial"])
def test_failed_cross_volume_copy_restores_and_removes_partial_copy(
    publish: Publish,
    exported: list[Path],
    out: Path,
    originals: dict[str, bytes],
    fs_faults: FsFaults,
    action: str,
) -> None:
    fs_faults.cross_volume = True
    fs_faults.faults[("copy", 2)] = action

    with pytest.raises(ExportError) as excinfo:
        publish(exported, out)

    assert isinstance(excinfo.value.__cause__, OSError)
    assert snapshot(out) == originals
    assert leftovers(out) == []


def test_failed_move_without_existing_files_withdraws_published(
    publish: Publish, exported: list[Path], out: Path, fs_faults: FsFaults
) -> None:
    before = snapshot(out)
    fs_faults.faults[("to_dest", 2)] = "fail"

    with pytest.raises(ExportError):
        publish(exported, out)

    assert snapshot(out) == before


# --- .bak copies: removed at the END, random job token ----------------------------------


def test_backups_are_removed_only_after_every_stem_is_published(
    publish: Publish,
    exported: list[Path],
    out: Path,
    originals: dict[str, bytes],
    fs_faults: FsFaults,
) -> None:
    seen: list[list[str]] = []
    fs_faults.faults[("to_dest", 2)] = lambda: seen.append(leftovers(out))

    publish(exported, out)

    [at_second_move] = seen
    assert len(at_second_move) == 2  # the first stem's .bak is still there
    assert any(name.endswith(f"-{NAMES[0]}.bak") for name in at_second_move)
    assert leftovers(out) == []


def test_first_backup_survives_a_failed_second_move_and_is_restored(
    publish: Publish,
    exported: list[Path],
    out: Path,
    originals: dict[str, bytes],
    fs_faults: FsFaults,
) -> None:
    fs_faults.faults[("to_dest", 2)] = "fail"

    with pytest.raises(ExportError):
        publish(exported, out)

    first_backup = fs_faults.of("backup")[0][2]
    assert first_backup is not None
    assert fs_faults.count("unlink_tmp") == 0  # never deleted: it was restored instead
    assert ("restore", first_backup, out / NAMES[0]) in fs_faults.log
    assert snapshot(out) == originals


def test_job_token_is_random_hex_and_differs_between_publications(
    publish: Publish, tmp_path: Path, out: Path, fs_faults: FsFaults
) -> None:
    tokens: list[str] = []
    for run in range(2):
        export_dir = tmp_path / f"work{run}"
        export_dir.mkdir()
        src = export_dir / NAMES[0]
        src.write_bytes(b"run %d" % run)
        (out / NAMES[0]).write_bytes(b"previous")
        before = fs_faults.count("backup")

        publish([src], out)

        [(_, _, backup)] = fs_faults.of("backup")[before:]
        assert backup is not None
        match = JOB_TOKEN.match(backup.name)
        assert match is not None
        tokens.append(match.group(1))
    assert tokens[0] != tokens[1]
    assert str(os.getpid()) not in tokens
    assert format(os.getpid(), "x") not in tokens


# --- CA-24: os.replace(.bak, dest) fails ------------------------------------------------


def test_ca24_restore_failure_is_output_restore_error_listing_each_backup(
    publish: Publish,
    exported: list[Path],
    out: Path,
    originals: dict[str, bytes],
    fs_faults: FsFaults,
) -> None:
    fs_faults.faults[("to_dest", 2)] = "fail"
    fs_faults.faults[("restore", 1)] = "fail"
    fs_faults.faults[("restore", 2)] = "fail"

    with pytest.raises(OutputRestoreError) as excinfo:
        publish(exported, out)

    error = excinfo.value
    assert error.exit_code == 6
    backups = {dst: src for _, src, dst in fs_faults.of("backup")}  # bak -> dest
    assert len(backups) == 2
    lines = error.message.splitlines()
    for bak, dest in backups.items():
        assert bak is not None
        assert bak.read_bytes() == ORIGINAL[dest.name]  # the user's file is in the .bak
        assert any(str(bak) in line and dest.name in line for line in lines)
    # The ONLY case that leaves prefixed files: exactly the two .bak copies.
    assert leftovers(out) == sorted(b.name for b in backups if b is not None)


def test_ca24_one_restore_failing_still_restores_the_other(
    publish: Publish,
    exported: list[Path],
    out: Path,
    originals: dict[str, bytes],
    fs_faults: FsFaults,
) -> None:
    fs_faults.faults[("to_dest", 2)] = "fail"
    fs_faults.faults[("restore", 1)] = "fail"

    with pytest.raises(OutputRestoreError):
        publish(exported, out)

    assert fs_faults.count("restore") == 2  # it went on after the first failure
    assert len(leftovers(out)) == 1


# --- withdrawal of a published stem without .bak fails (ADR-004 v0.3) --------------------


def test_failed_withdrawal_keeps_original_error_and_names_the_file(
    publish: Publish,
    exported: list[Path],
    out: Path,
    fs_faults: FsFaults,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fs_faults.faults[("to_dest", 2)] = "fail"
    fs_faults.faults[("unlink", 1)] = "fail"

    with (
        caplog.at_level(logging.WARNING, logger="stemseparator"),
        pytest.raises(ExportError) as excinfo,
    ):
        publish(exported, out)

    assert type(excinfo.value) is ExportError  # not OutputRestoreError
    assert excinfo.value.exit_code == 5
    assert NAMES[0] in excinfo.value.message
    assert str(out / NAMES[0]) in caplog.text
    assert (out / NAMES[0]).exists()  # the leftover: one file too many, none missing


def test_failed_withdrawal_during_interrupt_is_only_logged(
    publish: Publish,
    exported: list[Path],
    out: Path,
    fs_faults: FsFaults,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fs_faults.faults[("to_dest", 1)] = raise_sigint
    fs_faults.faults[("to_dest", 2)] = "fail"
    fs_faults.faults[("unlink", 1)] = "fail"

    with (
        caplog.at_level(logging.WARNING, logger="stemseparator"),
        pytest.raises(KeyboardInterrupt),
    ):
        publish(exported, out)

    assert str(out / NAMES[0]) in caplog.text


# --- CA-19: SIGINT inside the critical section ------------------------------------------


@VOLUMES
def test_ca19_sigint_between_moves_completes_the_publication(
    publish: Publish,
    exported: list[Path],
    out: Path,
    originals: dict[str, bytes],
    fs_faults: FsFaults,
    cross_volume: bool,
) -> None:
    fs_faults.cross_volume = cross_volume
    fs_faults.faults[("backup", 2)] = raise_sigint  # after the first stem was moved

    with sigint_must_be_deferred():
        result = publish(exported, out)

    assert result.interrupted is True
    assert snapshot(out) == {"otro.txt": b"del usuario", **NEW}
    assert leftovers(out) == []


def test_ca19_second_sigint_does_not_break_the_section_either(
    publish: Publish, exported: list[Path], out: Path, fs_faults: FsFaults
) -> None:
    fs_faults.faults[("to_dest", 1)] = raise_sigint
    fs_faults.faults[("to_dest", 2)] = raise_sigint

    with sigint_must_be_deferred():
        result = publish(exported, out)

    assert result.interrupted is True
    assert snapshot(out) == {"otro.txt": b"del usuario", **NEW}


def test_ca19_sigint_during_restoration_completes_it_then_propagates(
    publish: Publish,
    exported: list[Path],
    out: Path,
    originals: dict[str, bytes],
    fs_faults: FsFaults,
) -> None:
    fs_faults.faults[("to_dest", 2)] = "fail"
    fs_faults.faults[("restore", 1)] = raise_sigint

    with pytest.raises(KeyboardInterrupt):
        publish(exported, out)

    assert fs_faults.count("restore") == 2  # both restored despite the signal
    assert snapshot(out) == originals
    assert leftovers(out) == []


def test_interrupt_wins_over_export_error(
    publish: Publish,
    exported: list[Path],
    out: Path,
    originals: dict[str, bytes],
    fs_faults: FsFaults,
) -> None:
    # 130 > 5: SIGINT during a publication that then fails → KeyboardInterrupt.
    fs_faults.faults[("to_dest", 1)] = raise_sigint
    fs_faults.faults[("to_dest", 2)] = "fail"

    with pytest.raises(KeyboardInterrupt):
        publish(exported, out)

    assert snapshot(out) == originals


def test_restore_failure_wins_over_interrupt(
    publish: Publish,
    exported: list[Path],
    out: Path,
    originals: dict[str, bytes],
    fs_faults: FsFaults,
) -> None:
    # 6 > 130: a failed os.replace(.bak, dest) during an interruption → OutputRestoreError.
    fs_faults.faults[("to_dest", 1)] = raise_sigint
    fs_faults.faults[("to_dest", 2)] = "fail"
    fs_faults.faults[("restore", 1)] = "fail"

    with sigint_must_be_deferred(), pytest.raises(OutputRestoreError) as excinfo:
        publish(exported, out)

    assert excinfo.value.exit_code == 6


# --- SIGINT handler: installed inside, previous one restored on every exit ---------------


def _previous_handler(signum: int, frame: object) -> None:  # pragma: no cover - not called
    raise AssertionError("the deferred handler must be the one active during publication")


@pytest.mark.parametrize("outcome", ["success", "export-error", "restore-error"])
def test_previous_sigint_handler_is_restored_on_every_exit(
    publish: Publish,
    exported: list[Path],
    out: Path,
    originals: dict[str, bytes],
    fs_faults: FsFaults,
    outcome: str,
) -> None:
    during: list[object] = []
    fs_faults.faults[("to_dest", 1)] = lambda: during.append(signal.getsignal(signal.SIGINT))
    if outcome != "success":
        fs_faults.faults[("to_dest", 2)] = "fail"
    if outcome == "restore-error":
        fs_faults.faults[("restore", 1)] = "fail"

    original = signal.signal(signal.SIGINT, _previous_handler)
    try:
        try:
            publish(exported, out)
        except (ExportError, OutputRestoreError):
            assert outcome != "success"
        after = signal.getsignal(signal.SIGINT)
    finally:
        signal.signal(signal.SIGINT, original)

    assert during and during[0] is not _previous_handler  # deferral installed inside
    assert after is _previous_handler


# --- degradation: no signal handling outside the main thread ----------------------------


def test_publication_outside_main_thread_works_without_deferring(
    publish: Publish,
    exported: list[Path],
    out: Path,
    originals: dict[str, bytes],
    fs_faults: FsFaults,
) -> None:
    # signal.signal raises ValueError outside the main thread (e.g. a web worker, I8).
    outcome: dict[str, Any] = {}

    def worker() -> None:
        try:
            outcome["result"] = publish(exported, out)
        except BaseException as exc:  # reported to the main thread below
            outcome["error"] = exc

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=10)

    assert "error" not in outcome, outcome.get("error")
    assert outcome["result"].interrupted is False
    assert snapshot(out) == {"otro.txt": b"del usuario", **NEW}
    assert leftovers(out) == []
