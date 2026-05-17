"""Claude Code hook integration: installer, client script, doctor.

Empty re-export surface on purpose -- the hook client must not pull
in pydantic / typer / structlog through this package, or its cold
start tanks.
"""
