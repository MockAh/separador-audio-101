"""Model catalog (domain.md §3, ADR-002)."""

from stemseparator.domain.errors import UnknownModelError
from stemseparator.domain.models import ModelSpec, StemName


def _build_catalog() -> tuple[ModelSpec, ...]:
    """Build fresh catalog entries on every call, so callers never share state.

    Order is stable: the default model first, then by increasing `cpu_rtf`.
    """
    return (
        ModelSpec(
            id="vocals",
            engine_filename="UVR-MDX-NET-Voc_FT.onnx",
            stems=(StemName.VOCALS, StemName.INSTRUMENTAL),
            engine_labels={"Vocals": StemName.VOCALS, "Instrumental": StemName.INSTRUMENTAL},
            cpu_rtf=2.4,
            cpu_reference="Intel Core i7-8565U, 4 núcleos / 8 hilos, WSL2",
            weights_license="MIT (UVR), con atribución",
            download_size_bytes=66_762_490,
            description=(
                "Separador de voz MDX-Net del proyecto UVR. Buena calidad con un coste en "
                "CPU moderado; opción práctica para uso habitual."
            ),
            is_default=True,
        ),
        ModelSpec(
            id="vocals-max",
            engine_filename="model_bs_roformer_ep_317_sdr_12.9755.ckpt",
            stems=(StemName.VOCALS, StemName.INSTRUMENTAL),
            engine_labels={"Vocals": StemName.VOCALS, "Instrumental": StemName.INSTRUMENTAL},
            cpu_rtf=37.1,
            cpu_reference="Intel Core i7-8565U, 4 núcleos / 8 hilos, WSL2",
            weights_license="Sin licencia declarada",
            download_size_bytes=639_333_486,
            description=(
                "Separador de voz BS-Roformer (viperx). El SDR de referencia más alto del "
                "catálogo, con un coste en CPU mucho mayor."
            ),
        ),
    )


def list_models() -> tuple[ModelSpec, ...]:
    """Return the catalog, default model first. Each call returns new objects."""
    return _build_catalog()


def get_model(model_id: str | None) -> ModelSpec:
    """Return the model with `model_id`, or the default one if it is None.

    Raises UnknownModelError if the id is not in the catalog.
    """
    models = list_models()
    if model_id is None:
        return next(m for m in models if m.is_default)
    for model in models:
        if model.id == model_id:
            return model
    raise UnknownModelError(
        f"El modelo '{model_id}' no está en el catálogo.",
        "Ejecuta `stemseparator models` para ver los modelos disponibles.",
    )
