"""Standard-library-only launcher for hosts that cannot install into .local."""

import os
import subprocess
import sys
from pathlib import Path


def require_python():
    if sys.version_info < (3, 12):  # noqa: UP036 - Hosts can bypass package metadata.
        raise SystemExit(
            "Nex CodeX requires Python 3.12 or newer; "
            f"the selected interpreter is {sys.version_info.major}.{sys.version_info.minor}. "
            "Change the hosting Python image/interpreter to 3.12+ and restart."
        )


def checked(command: list[str], directory: Path):
    subprocess.run(command, cwd=directory, check=True)


def main() -> int:
    require_python()
    root = Path(__file__).resolve().parent.parent
    environment = root / ".venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    try:
        if not python.is_file():
            checked([sys.executable, "-m", "venv", str(environment)], root)
        # Refuse to reuse an environment left over from a different Python image.
        checked(
            [
                str(python),
                "-c",
                "import sys; sys.exit(sys.version_info[:2] != " + repr(sys.version_info[:2]) + ")",
            ],
            root,
        )
        pip = subprocess.run(
            [str(python), "-c", "import pip"],
            cwd=root,
            capture_output=True,
            check=False,
        )
        if pip.returncode:
            checked([str(python), "-m", "ensurepip", "--upgrade"], root)
        # --isolated ignores inherited PIP_PREFIX/PIP_USER and user pip configuration.
        # No --prefix .local and no global/user installs. Fail before starting on errors.
        checked(
            [
                str(python),
                "-m",
                "pip",
                "--isolated",
                "--disable-pip-version-check",
                "--no-cache-dir",
                "install",
                "-r",
                str(root / "requirements.txt"),
            ],
            root,
        )
        os.chdir(root)
        # Replace the launcher so SIGTERM reaches the bot directly.
        os.execv(str(python), [str(python), str(root / "bot.py")])
    except subprocess.CalledProcessError:
        print(
            "Startup preparation failed; the bot was not started. Check the error above. "
            "The server user must be able to write .venv and data/. "
            "If the Python image changed, stop the bot and move the old .venv aside. "
            "The image must provide the Python venv/ensurepip modules.",
            file=sys.stderr,
        )
        return 1
    except OSError as exc:
        message = (
            "Startup cannot write or execute its virtual environment. "
            "Ask the host to restore ownership/access for the server user; "
            "do not use chmod 777."
            if isinstance(exc, PermissionError)
            else "Startup could not access its virtual environment or launch Python. "
            "Check the uploaded project files and Python image."
        )
        print(message, file=sys.stderr)
        return 1
    return 0
