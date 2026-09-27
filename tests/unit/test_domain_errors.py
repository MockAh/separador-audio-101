"""Exit codes of the domain error hierarchy (interfaces.md §4, RNF-09)."""

import inspect

import pytest

from stemseparator.domain import errors
from stemseparator.domain.errors import StemSeparatorError

EXPECTED_EXIT_CODES: dict[str, int] = {
    "InputNotFoundError": 3,
    "UnsupportedFormatError": 3,
    "NoAudioStreamError": 3,
    "MediaReadError": 3,
    "UnknownModelError": 3,
    "UnsupportedStemError": 3,
    "OutputConflictError": 3,
    "OutputNotWritableError": 3,
    "FileTooLargeError": 3,
    "FFmpegNotFoundError": 4,
    "InsufficientDiskSpaceError": 4,
    "EngineError": 5,
    "ExportError": 5,
    "OutputRestoreError": 6,
}


def _defined_error_classes() -> dict[str, type[StemSeparatorError]]:
    return {
        name: obj
        for name, obj in inspect.getmembers(errors, inspect.isclass)
        if issubclass(obj, StemSeparatorError)
        and obj is not StemSeparatorError
        and obj.__module__ == errors.__name__
    }


@pytest.mark.parametrize(("name", "code"), sorted(EXPECTED_EXIT_CODES.items()))
def test_each_error_has_exit_code_from_table(name: str, code: int) -> None:
    cls = getattr(errors, name)

    assert issubclass(cls, StemSeparatorError)
    assert cls.exit_code == code


def test_hierarchy_matches_table_exactly() -> None:
    assert set(_defined_error_classes()) == set(EXPECTED_EXIT_CODES)


def test_base_class_declares_exit_code_without_value() -> None:
    assert "exit_code" in StemSeparatorError.__annotations__
    assert not hasattr(StemSeparatorError, "exit_code")


def test_base_class_inherits_from_exception() -> None:
    assert issubclass(StemSeparatorError, Exception)


@pytest.mark.parametrize("name", sorted(EXPECTED_EXIT_CODES))
def test_error_keeps_message_and_hint(name: str) -> None:
    cls = getattr(errors, name)

    exc = cls("No se encontró el archivo.", "Comprueba la ruta.")

    assert exc.message == "No se encontró el archivo."
    assert exc.hint == "Comprueba la ruta."
    assert exc.exit_code == EXPECTED_EXIT_CODES[name]


@pytest.mark.parametrize("name", sorted(EXPECTED_EXIT_CODES))
def test_hint_is_mandatory(name: str) -> None:
    cls = getattr(errors, name)

    with pytest.raises(TypeError):
        cls("Mensaje sin sugerencia.")
