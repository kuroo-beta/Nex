from nex.startup import require_python

require_python()

try:
    from nex.bot import main
except ModuleNotFoundError as exc:
    if exc.name not in {"discord", "aiohttp", "aiosqlite", "dotenv"}:
        raise
    raise SystemExit(
        f"Missing dependency: {exc.name}. Dependency installation did not complete. "
        "Run python start.py with Python 3.12+ to install into .venv, "
        "or install requirements.txt in your active virtual environment."
    ) from None

if __name__ == "__main__":
    main()
