"""README.md (T-14): the FFmpeg install commands are the ones the CLI prints."""

from pathlib import Path

from stemseparator.media.system import INSTALL_HINT

ROOT = Path(__file__).parents[2]
README = ROOT / "README.md"
PRODUCT = ROOT / "specs" / "product.md"


def _install_commands() -> list[str]:
    """The command of each "  <system>: <command>" line of INSTALL_HINT."""
    return [
        line.split(": ", 1)[1]
        for line in INSTALL_HINT.splitlines()
        if line.startswith("  ") and ": " in line
    ]


def test_install_hint_has_one_command_per_system() -> None:
    assert len(_install_commands()) == 3


def test_readme_shows_the_ffmpeg_install_commands_of_the_cli() -> None:
    readme = README.read_text(encoding="utf-8")

    for command in _install_commands():
        assert command in readme, command


def _bullets(markdown: str, heading: str) -> int:
    """Top-level "- " items of the "## " section whose title starts with heading."""
    lines = markdown.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(f"## {heading}")), None)
    assert start is not None, f"no '## {heading}' section"
    count = 0
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        count += line.startswith("- ")
    return count


def test_product_lists_known_limitations() -> None:
    assert _bullets(PRODUCT.read_text(encoding="utf-8"), "7. Limitaciones conocidas") > 0


def test_readme_has_at_least_one_limitation_per_product_limitation() -> None:
    """A guard against forgetting one, not a transcription: the README words them freely."""
    product = _bullets(PRODUCT.read_text(encoding="utf-8"), "7. Limitaciones conocidas")

    assert _bullets(README.read_text(encoding="utf-8"), "Limitaciones conocidas") >= product
