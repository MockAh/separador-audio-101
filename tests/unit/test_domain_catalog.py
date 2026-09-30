"""Model catalog (domain.md §3, interfaces.md §3, ADR-002; RF-03, RF-10, CA-07)."""

from typing import Any, cast

import pytest

from stemseparator.domain.catalog import get_model, list_models
from stemseparator.domain.errors import UnknownModelError
from stemseparator.domain.models import ModelSpec, StemName

VOICE_STEMS = (StemName.VOCALS, StemName.INSTRUMENTAL)
VOICE_LABELS = {"Vocals": StemName.VOCALS, "Instrumental": StemName.INSTRUMENTAL}
CPU_REFERENCE = "Intel Core i7-8565U, 4 núcleos / 8 hilos, WSL2"

# Literal values from domain.md §3; CA-14 shows them as they are.
EXPECTED: dict[str, dict[str, Any]] = {
    "vocals": {
        "engine_filename": "UVR-MDX-NET-Voc_FT.onnx",
        "stems": VOICE_STEMS,
        "engine_labels": VOICE_LABELS,
        "cpu_rtf": 2.4,
        "cpu_reference": CPU_REFERENCE,
        "weights_license": "MIT (UVR), con atribución",
        "download_size_bytes": 66_762_490,
        "description": (
            "Separador de voz MDX-Net del proyecto UVR. Buena calidad con un coste en CPU "
            "moderado; opción práctica para uso habitual."
        ),
    },
    "vocals-max": {
        "engine_filename": "model_bs_roformer_ep_317_sdr_12.9755.ckpt",
        "stems": VOICE_STEMS,
        "engine_labels": VOICE_LABELS,
        "cpu_rtf": 37.1,
        "cpu_reference": CPU_REFERENCE,
        "weights_license": "Sin licencia declarada",
        "download_size_bytes": 639_333_486,
        "description": (
            "Separador de voz BS-Roformer (viperx). El SDR de referencia más alto del "
            "catálogo, con un coste en CPU mucho mayor."
        ),
    },
}


# --- Invariants (domain.md §3) ---


def test_exactly_one_default_and_it_is_vocals() -> None:
    defaults = [m for m in list_models() if m.is_default]
    assert [m.id for m in defaults] == ["vocals"]


def test_ids_are_unique() -> None:
    ids = [m.id for m in list_models()]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("model", list_models(), ids=lambda m: m.id)
def test_engine_labels_cover_stems(model: ModelSpec) -> None:
    assert set(model.engine_labels.values()) == set(model.stems)
    assert len(model.engine_labels) == len(model.stems)


@pytest.mark.parametrize("model", list_models(), ids=lambda m: m.id)
def test_required_fields_are_set(model: ModelSpec) -> None:
    assert model.weights_license
    assert model.cpu_reference
    assert model.cpu_rtf > 0
    assert model.download_size_bytes > 0


def test_order_is_stable() -> None:
    assert [m.id for m in list_models()] == ["vocals", "vocals-max"]


# --- Exact data (domain.md §3, CA-14) ---


@pytest.mark.parametrize("model_id", list(EXPECTED))
def test_entry_matches_domain_spec(model_id: str) -> None:
    model = get_model(model_id)
    actual = {field: getattr(model, field) for field in EXPECTED[model_id]}
    actual["engine_labels"] = dict(model.engine_labels)
    assert actual == EXPECTED[model_id]


# --- get_model() ---


def test_get_model_none_returns_default() -> None:
    model = get_model(None)
    assert model.id == "vocals"
    assert model.is_default


def test_get_model_by_id() -> None:
    assert get_model("vocals-max").id == "vocals-max"


def test_get_model_unknown_id_raises() -> None:
    """CA-07, domain part: exit code 3 and a hint pointing to `stemseparator models`."""
    with pytest.raises(UnknownModelError) as exc_info:
        get_model("no-existe")
    assert exc_info.value.exit_code == 3
    assert "stemseparator models" in exc_info.value.hint


# --- Independent copies (interfaces.md v0.6, T-04 decision) ---


def test_list_models_returns_fresh_objects() -> None:
    first, second = list_models(), list_models()
    for a, b in zip(first, second, strict=True):
        assert a is not b
        assert a.engine_labels is not b.engine_labels


def test_get_model_returns_fresh_objects() -> None:
    a, b = get_model("vocals"), get_model("vocals")
    assert a is not b
    assert a.engine_labels is not b.engine_labels


def test_mutating_a_result_does_not_leak() -> None:
    labels = cast(dict[str, StemName], get_model("vocals").engine_labels)
    labels["Vocals"] = StemName.INSTRUMENTAL
    labels["Extra"] = StemName.VOCALS

    assert dict(list_models()[0].engine_labels) == VOICE_LABELS
    assert dict(get_model("vocals").engine_labels) == VOICE_LABELS
