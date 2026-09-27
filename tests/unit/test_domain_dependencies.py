"""Dependency rule for `domain` (architecture.md §1).

`domain` imports nothing from the project except `domain` itself. Outside the project
it may only use the standard library and Pydantic.
"""

import ast
import sys
from pathlib import Path

import pytest

import stemseparator.domain

DOMAIN_DIR = Path(stemseparator.domain.__file__).parent
DOMAIN_PACKAGE = "stemseparator.domain"
ALLOWED_THIRD_PARTY = frozenset({"pydantic"})


def _resolve(node: ast.ImportFrom, module_name: str) -> str:
    """Absolute module name targeted by a (possibly relative) `from ... import`."""
    if node.level == 0:
        return node.module or ""
    package_parts = module_name.split(".")[:-1]
    base = package_parts[: len(package_parts) - (node.level - 1)]
    return ".".join([*base, node.module] if node.module else base)


def imported_modules(source: str, module_name: str) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(_resolve(node, module_name))
    return modules


def is_allowed(module: str) -> bool:
    top = module.split(".")[0]
    if top == "stemseparator":
        return module == DOMAIN_PACKAGE or module.startswith(DOMAIN_PACKAGE + ".")
    return top in sys.stdlib_module_names or top in ALLOWED_THIRD_PARTY


def _domain_modules() -> list[tuple[str, Path]]:
    return [
        (f"{DOMAIN_PACKAGE}.{path.stem}", path)
        for path in sorted(DOMAIN_DIR.glob("*.py"))
        if path.stem != "__init__"
    ] + [(f"{DOMAIN_PACKAGE}.__init__", DOMAIN_DIR / "__init__.py")]


@pytest.mark.parametrize(
    "module",
    [
        "pathlib",
        "enum",
        "collections.abc",
        "types",
        "typing",
        "__future__",
        "pydantic",
        "pydantic.fields",
        "stemseparator.domain",
        "stemseparator.domain.models",
    ],
)
def test_rule_allows_stdlib_pydantic_and_domain(module: str) -> None:
    assert is_allowed(module)


@pytest.mark.parametrize(
    "module",
    [
        "stemseparator",
        "stemseparator.application.service",
        "stemseparator.engines.base",
        "stemseparator.media.ffmpeg",
        "stemseparator.cli",
        "audio_separator.separator",
        "numpy",
        "typer",
        "rich",
    ],
)
def test_rule_rejects_other_project_packages_and_third_party(module: str) -> None:
    assert not is_allowed(module)


def test_relative_imports_are_resolved() -> None:
    source = "from .models import StemName\nfrom ..engines import base\nfrom . import errors\n"

    assert imported_modules(source, "stemseparator.domain.catalog") == [
        "stemseparator.domain.models",
        "stemseparator.engines",
        "stemseparator.domain",
    ]


def test_domain_package_has_the_expected_modules() -> None:
    names = {path.stem for path in DOMAIN_DIR.glob("*.py")}

    assert {"models", "errors", "progress", "catalog"} <= names


@pytest.mark.parametrize(("module_name", "path"), _domain_modules(), ids=lambda v: str(v))
def test_domain_module_respects_dependency_rule(module_name: str, path: Path) -> None:
    forbidden = [
        module
        for module in imported_modules(path.read_text(encoding="utf-8"), module_name)
        if not is_allowed(module)
    ]

    assert forbidden == []
