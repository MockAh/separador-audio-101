"""Job stages and the progress reporter port (interfaces.md §3)."""

from typing import Protocol

from stemseparator.domain.progress import JobStage, ProgressReporter


def test_job_stage_values() -> None:
    assert [s.value for s in JobStage] == [
        "validating",
        "preparing",
        "loading_model",
        "separating",
        "exporting",
        "done",
        "failed",
    ]


def test_progress_reporter_is_a_protocol() -> None:
    assert Protocol in ProgressReporter.__mro__
    assert getattr(ProgressReporter, "_is_protocol", False) is True


def test_progress_reporter_declares_the_three_callbacks() -> None:
    for name in ("stage_started", "stage_finished", "stage_failed"):
        assert callable(getattr(ProgressReporter, name))
