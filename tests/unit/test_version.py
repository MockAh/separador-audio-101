from typer.testing import CliRunner

from stemseparator import __version__
from stemseparator.cli import app


def test_version_option_prints_version_and_exits_zero() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == f"stemseparator {__version__}"


def test_version_is_semver_like() -> None:
    parts = __version__.split(".")

    assert len(parts) == 3
    assert all(part.isdigit() for part in parts)
