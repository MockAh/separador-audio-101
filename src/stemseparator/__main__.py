"""`python -m stemseparator`: same entry point as the `stemseparator` script."""

from stemseparator.cli import app

if __name__ == "__main__":
    app(prog_name="stemseparator")
