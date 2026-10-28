"""Hosting launcher regressions; unit tests require no network or bot token."""

import subprocess
import sys
from collections import namedtuple
from pathlib import Path
from unittest.mock import Mock

import pytest

from nex import startup


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    (tmp_path / "nex").mkdir()
    monkeypatch.setattr(startup, "__file__", str(tmp_path / "nex" / "startup.py"))
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(startup.subprocess, "run", run)
    execute = Mock()
    monkeypatch.setattr(startup.os, "execv", execute)
    monkeypatch.setattr(startup.os, "chdir", Mock())
    return tmp_path, calls, execute


def test_launcher_uses_private_venv_and_replaces_itself(launcher):
    root, calls, execute = launcher
    assert startup.main() == 0
    commands = [command for command, _ in calls]
    assert commands[0] == [sys.executable, "-m", "venv", str(root / ".venv")]
    install = commands[-1]
    assert "--isolated" in install and "--no-cache-dir" in install
    assert "--prefix" not in install and "--user" not in install
    assert install[-3:] == ["install", "-r", str(root / "requirements.txt")]
    assert all(options["cwd"] == root for _, options in calls)
    assert all(isinstance(command, list) for command in commands)
    assert all(not options.get("shell") for _, options in calls)
    python = root / ".venv" / "bin" / "python"
    execute.assert_called_once_with(str(python), [str(python), str(root / "bot.py")])


def test_existing_environment_reused_and_pip_bootstrapped(launcher, monkeypatch):
    root, calls, execute = launcher
    python = root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    original_run = startup.subprocess.run

    def run(command, **kwargs):
        result = original_run(command, **kwargs)
        if command[-1] == "import pip":
            result.returncode = 1
        return result

    monkeypatch.setattr(startup.subprocess, "run", run)
    assert startup.main() == 0
    commands = [command for command, _ in calls]
    assert not any("venv" in command for command in commands)
    assert [str(python), "-m", "ensurepip", "--upgrade"] in commands
    execute.assert_called_once()


def test_install_failure_never_starts_bot(launcher, monkeypatch, capsys):
    _, _, execute = launcher
    original_run = startup.subprocess.run

    def run(command, **kwargs):
        if "install" in command:
            raise subprocess.CalledProcessError(1, command)
        return original_run(command, **kwargs)

    monkeypatch.setattr(startup.subprocess, "run", run)
    assert startup.main() == 1
    execute.assert_not_called()
    assert "bot was not started" in capsys.readouterr().err


def test_permission_failure_is_actionable(launcher, monkeypatch, capsys):
    _, _, execute = launcher
    monkeypatch.setattr(startup.subprocess, "run", Mock(side_effect=PermissionError()))
    assert startup.main() == 1
    execute.assert_not_called()
    error = capsys.readouterr().err
    assert "restore ownership" in error and "chmod 777" in error


def test_python_311_fails_before_installation(launcher, monkeypatch):
    _, calls, execute = launcher
    version = namedtuple("Version", "major minor micro")
    monkeypatch.setattr(startup.sys, "version_info", version(3, 11, 17))
    with pytest.raises(SystemExit, match="requires Python 3.12.*3.11"):
        startup.main()
    assert calls == []
    execute.assert_not_called()


def test_incompatible_existing_environment_never_starts(launcher, monkeypatch, capsys):
    root, _, execute = launcher
    python = root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(
        startup.subprocess,
        "run",
        Mock(side_effect=subprocess.CalledProcessError(1, "version check")),
    )
    assert startup.main() == 1
    execute.assert_not_called()
    assert "move the old .venv aside" in capsys.readouterr().err


def test_bot_entry_point_explains_missing_dependencies():
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "-S", str(root / "bot.py")],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    assert "Dependency installation did not complete" in result.stderr
    assert "python start.py" in result.stderr
    assert "Traceback" not in result.stderr
