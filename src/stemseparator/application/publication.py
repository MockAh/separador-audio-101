"""Atomic publication of the exported stems into the output directory (ADR-004).

Standard library only. Per stem: an existing destination is renamed to a
`.stemseparator-tmp-<job>-<name>.bak` in the output directory; then the file is moved with
os.replace, or, across volumes (EXDEV), copied to `.stemseparator-tmp-<job>-<name>` in the
output directory and renamed onto the destination. The .bak copies are removed only when
every stem is published. On failure every touched stem is undone: restored from its .bak,
or withdrawn if it had none. Moves, .bak removal and restoration form a critical section
in which SIGINT is deferred.
"""

import errno
import logging
import os
import secrets
import shutil
import signal
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import FrameType, TracebackType
from typing import Any

from stemseparator.domain.errors import ExportError, OutputRestoreError

logger = logging.getLogger(__name__)

TMP_PREFIX = ".stemseparator-tmp-"  # every auxiliary file in the output directory (ADR-004)


@dataclass(frozen=True)
class Publication:
    paths: tuple[Path, ...]  # final paths, in the order of the published files
    interrupted: bool  # SIGINT arrived during a publication that finished well (CA-19)


@dataclass
class _Step:
    """What one stem's move did so far, so it can be undone."""

    dest: Path
    backup: Path | None = None  # the user's previous file, renamed
    staged: Path | None = None  # cross-volume copy not yet renamed onto dest
    published: bool = False


class _DeferredSigint:
    """Critical section: SIGINT is recorded instead of raised, and the previous handler
    is put back on exit, also on exceptions. Outside the main thread signal handlers
    cannot be installed; the section then runs without deferring instead of failing."""

    def __init__(self) -> None:
        self.received = False
        self._previous: Any = None
        self._installed = False

    def _handler(self, signum: int, frame: FrameType | None) -> None:
        self.received = True  # a second SIGINT does not break the section either

    def __enter__(self) -> "_DeferredSigint":
        try:
            self._previous = signal.signal(signal.SIGINT, self._handler)
        except ValueError:
            logger.debug("SIGINT cannot be deferred outside the main thread")
        else:
            self._installed = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._installed:
            # None means a handler not installed from Python: fall back to the default.
            previous = self._previous if self._previous is not None else signal.SIG_DFL
            signal.signal(signal.SIGINT, previous)


def publish(files: Sequence[Path], output_dir: Path) -> Publication:
    """Move the checked files into output_dir atomically (ADR-004).

    Raises ExportError (the output directory is as before), KeyboardInterrupt if SIGINT
    arrived during a publication that failed, or OutputRestoreError if a .bak could not be
    put back (precedence 6 > 130 > 5).
    """
    job = secrets.token_hex(4)  # never the PID: concurrent runs must not share names
    steps: list[_Step] = []
    failure: BaseException | None = None
    not_removed: list[Path] = []
    not_restored: list[tuple[Path, Path]] = []
    with _DeferredSigint() as sigint:
        try:
            for src in files:
                _move(src, output_dir, job, steps)
        except BaseException as exc:  # undo first, decide what to raise afterwards
            failure = exc
            not_removed, not_restored = _undo(steps)
        else:
            _remove_backups(steps)

    if failure is None:
        return Publication(paths=tuple(s.dest for s in steps), interrupted=sigint.received)
    if not_restored:
        raise OutputRestoreError(
            "No se pudieron restaurar tus archivos originales en "
            f"{output_dir}. Están a salvo en copias .bak; renómbralas así:\n"
            + "\n".join(f"  {bak} → {dest.name}" for bak, dest in not_restored),
            "Renombra cada copia .bak al nombre indicado antes de volver a ejecutar el "
            "comando: contiene tu archivo original.",
        ) from failure
    if sigint.received and not isinstance(failure, KeyboardInterrupt):
        raise KeyboardInterrupt from failure
    if not isinstance(failure, OSError):
        raise failure
    raise _move_error(output_dir, not_removed) from failure


def _move(src: Path, output_dir: Path, job: str, steps: list[_Step]) -> None:
    step = _Step(dest=output_dir / src.name)
    steps.append(step)  # before acting, so that _undo sees a half-done step
    if step.dest.exists():  # only possible with --overwrite: conflicts were checked
        backup = output_dir / f"{TMP_PREFIX}{job}-{src.name}.bak"
        os.replace(step.dest, backup)
        step.backup = backup
    try:
        os.replace(src, step.dest)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        # Different volumes: copy next to the destination, then an atomic rename.
        step.staged = output_dir / f"{TMP_PREFIX}{job}-{src.name}"
        shutil.copyfile(src, step.staged)  # just created: no metadata worth copying
        os.replace(step.staged, step.dest)
        step.staged = None
    step.published = True


def _undo(steps: list[_Step]) -> tuple[list[Path], list[tuple[Path, Path]]]:
    """Restore or withdraw every touched stem; go on after a failure.

    Returns the stems that could not be withdrawn (best effort: one file too many) and the
    (.bak, destination) pairs that could not be restored (the user must act).
    """
    not_removed: list[Path] = []
    not_restored: list[tuple[Path, Path]] = []
    for step in reversed(steps):
        if step.staged is not None:
            _best_effort_unlink(step.staged, not_removed)
        if step.backup is not None:
            try:
                os.replace(step.backup, step.dest)  # onto the published one, or in its place
            except OSError:
                logger.exception("could not restore %s to %s", step.backup, step.dest)
                not_restored.append((step.backup, step.dest))
        elif step.published:
            _best_effort_unlink(step.dest, not_removed)
    return not_removed, not_restored


def _remove_backups(steps: list[_Step]) -> None:
    for step in steps:
        if step.backup is not None:
            _best_effort_unlink(step.backup, [])


def _best_effort_unlink(path: Path, failed: list[Path]) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("could not remove %s; it was left in the output directory", path)
        failed.append(path)


def _move_error(output_dir: Path, not_removed: list[Path]) -> ExportError:
    message = f"No se pudieron mover los stems al directorio de salida {output_dir}."
    if not not_removed:
        return ExportError(
            message,
            "Comprueba el espacio libre y los permisos de ese directorio. Tus archivos "
            "están como antes de ejecutar el comando.",
        )
    return ExportError(
        message + " No se pudo retirar: " + ", ".join(str(p) for p in not_removed) + ".",
        "Tus archivos originales están intactos; borra a mano los archivos indicados y "
        "comprueba el espacio libre y los permisos de ese directorio.",
    )
