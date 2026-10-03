"""Dependency rule for `application` (architecture.md §1).

`application` imports `domain` and the ports (Protocols), never concrete adapters.
Reuses the import walker of the `domain` rule.
"""

import sys
from pathlib import Path

import pytest

import stemseparator.application
from tests.unit.test_domain_dependencies import imported_modules

APPLICATION_DIR = Path(stemseparator.application.__file__).parent
APPLICATION_PACKAGE = "stemseparator.application"
ALLOWED_PROJECT_MODULES = frozenset({"stemseparator.media.base", "stemseparator.engines.base"})
ALLOWED_PROJECT_PREFIXES = ("stemseparator.domain", APPLICATION_PACKAGE)
ALLOWED_THIRD_PARTY = frozenset({"pydantic"})


def is_allowed(module: str) -> bool:
    top = module.split(".")[0]
    if top == "stemseparator":
        return module in ALLOWED_PROJECT_MODULES or any(
            module == prefix or module.startswith(prefix + ".")
            for prefix in ALLOWED_PROJECT_PREFIXES
        )
    return top in sys.stdlib_module_names or top in ALLOWED_THIRD_PARTY


@pytest.mark.parametrize(
    "module",
    [
        "tempfile",
        "time",
        "pathlib",
        "stemseparator.domain",
        "stemseparator.domain.catalog",
        "stemseparator.media.base",
        "stemseparator.engines.base",
    ],
)
def test_rule_allows_stdlib_domain_and_ports(module: str) -> None:
    assert is_allowed(module)


@pytest.mark.parametrize(
    "module",
    [
        "stemseparator.media",
        "stemseparator.media.ffmpeg",
        "stemseparator.media.system",
        "stemseparator.engines",
        "stemseparator.engines.audio_separator_engine",
        "stemseparator.cli",
        "audio_separator.separator",
        "platformdirs",
        "typer",
        "rich",
    ],
)
def test_rule_rejects_adapters_cli_and_third_party(module: str) -> None:
    assert not is_allowed(module)


def _application_modules() -> list[tuple[str, Path]]:
    return [
        (f"{APPLICATION_PACKAGE}.{path.stem}", path)
        for path in sorted(APPLICATION_DIR.glob("*.py"))
        if path.stem != "__init__"
    ] + [(f"{APPLICATION_PACKAGE}.__init__", APPLICATION_DIR / "__init__.py")]


@pytest.mark.parametrize(("module_name", "path"), _application_modules(), ids=lambda v: str(v))
def test_application_module_respects_dependency_rule(module_name: str, path: Path) -> None:
    forbidden = [
        module
        for module in imported_modules(path.read_text(encoding="utf-8"), module_name)
        if not is_allowed(module)
    ]

    assert forbidden == []
