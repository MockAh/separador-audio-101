"""Job stages and the progress reporter port (interfaces.md §3).

Lives in `domain` so that both `engines` and `application` can use it without
breaking the dependency rule (architecture.md §1).
"""

from enum import StrEnum
from typing import Protocol


class JobStage(StrEnum):
    VALIDATING = "validating"
    PREPARING = "preparing"
    LOADING_MODEL = "loading_model"
    SEPARATING = "separating"
    EXPORTING = "exporting"
    DONE = "done"
    FAILED = "failed"
    # DONE and FAILED are only terminal lifecycle states (domain.md §4); they are never
    # passed to stage_started, stage_finished or stage_failed.


class ProgressReporter(Protocol):
    """Recibe las transiciones de etapa de un trabajo de separación.

    Quien llamó stage_started de una etapa es responsable de llamar
    stage_failed de esa misma etapa antes de propagar la excepción. El
    servicio no reporta el fallo de las etapas que notifica el motor.
    Así, stage_failed se llama una sola vez por trabajo fallido, con la
    etapa en curso al fallar (incluye Ctrl+C). DONE y FAILED no se
    notifican: en un trabajo correcto, la última llamada es
    stage_finished(EXPORTING)."""

    def stage_started(self, stage: JobStage) -> None: ...
    def stage_finished(self, stage: JobStage, elapsed_s: float) -> None: ...
    def stage_failed(self, stage: JobStage, error: BaseException) -> None: ...
