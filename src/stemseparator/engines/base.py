"""Engine port (interfaces.md §3). The implementation lives in engines/audio_separator_engine.py."""

from pathlib import Path
from typing import Protocol

from stemseparator.domain.models import ModelSpec, StemName
from stemseparator.domain.progress import ProgressReporter


class SeparationEngine(Protocol):
    def separate(
        self,
        audio_path: Path,
        model: ModelSpec,
        work_dir: Path,
        model_dir: Path,
        reporter: ProgressReporter | None = None,
    ) -> dict[StemName, Path]:
        """Separate audio_path with the model, writing only inside work_dir.

        Returns one WAV path per stem of model.stems. With a reporter, notifies
        stage_started/stage_finished (or stage_failed) of LOADING_MODEL (weights download
        and load) and SEPARATING (inference). Raises EngineError if the engine fails, an
        expected output is missing, or the engine chose CUDA (RNF-05).
        """
        ...
