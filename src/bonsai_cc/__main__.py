"""Allow ``python -m bonsai_cc`` to invoke the Typer CLI."""

from bonsai_cc.cli import app

if __name__ == "__main__":
    app()
