"""Command-line interface. Commands are added in T-12 (`separate`) and T-13 (`models`, `doctor`)."""

from typing import Annotated

import typer

from stemseparator import __version__

app = typer.Typer(
    name="stemseparator",
    help="Separa un archivo de audio o video en pistas (stems). Solo CPU.",
    no_args_is_help=True,
    add_completion=False,
)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"stemseparator {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Muestra la versión y termina.",
            callback=_print_version,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Separa un archivo de audio o video en pistas (stems). Solo CPU."""
