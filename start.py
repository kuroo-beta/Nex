"""Hosting entry point: select this as PY_FILE to prepare dependencies in .venv."""

from nex.startup import main

if __name__ == "__main__":
    raise SystemExit(main())
