"""Interactive one-shot setup wizard.

Walks a new user through configuration: checks the environment is supported,
seeds ``.env`` from ``.env.example``, prompts for the required settings,
verifies the tokens with read-only preflight checks, and optionally previews
a run. Every step is wrapped so a problem prints a friendly message instead
of a traceback.

Uses only the standard library for prompting (no extra dependencies).
"""

from __future__ import annotations

import getpass
import os
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Terminal output helpers (mirrors the palette used by install.sh)
# ---------------------------------------------------------------------------

_TTY = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _TTY else text


def _bold(t: str) -> str:
    return _c("1", t)


def _heading(text: str) -> None:
    print()
    print(_c("1;36", f"== {text}"))


def _ok(text: str) -> None:
    print(f"  {_c('32', '✓')} {text}")


def _fail(text: str) -> None:
    print(f"  {_c('31', '✗')} {text}")


def _warn(text: str) -> None:
    print(f"  {_c('33', '!')} {text}")


def _info(text: str) -> None:
    print(f"  {text}")


# ---------------------------------------------------------------------------
# .env reading / writing (preserves comments and untouched keys)
# ---------------------------------------------------------------------------


def _read_env(path: Path) -> dict[str, str]:
    """Parse ``KEY=value`` lines from an env file (best effort)."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def _write_env(path: Path, updates: dict[str, str]) -> None:
    """Apply ``updates`` to an env file, keeping comments and other keys.

    Existing ``KEY=`` lines are rewritten in place; new keys are appended.
    The file is written with mode 600 since it holds tokens.
    """
    lines = path.read_text().splitlines() if path.exists() else []
    remaining = dict(updates)

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.partition("=")[0].strip()
        if key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}"

    for key, value in remaining.items():
        lines.append(f"{key}={value}")

    path.write_text("\n".join(lines) + "\n")
    os.chmod(path, 0o600)


def _mask(value: str) -> str:
    """Show only the tail of a secret, e.g. ``…a1b2c3``."""
    if not value:
        return "(empty)"
    return "…" + value[-4:] if len(value) > 4 else "****"


# ---------------------------------------------------------------------------
# Prompting
# ---------------------------------------------------------------------------


def _prompt(label: str, *, current: str = "", default: str = "",
            secret: bool = False) -> str:
    """Prompt for a value, showing the current/default in brackets.

    Pressing Enter keeps the current value (if any), else the default.
    Secrets are read without echo and shown masked.
    """
    keep = current or default
    if secret and current:
        shown = _mask(current)
    else:
        shown = keep
    suffix = f" [{shown}]" if shown else ""
    reader = getpass.getpass if secret else input
    try:
        entered = reader(f"  {label}{suffix}: ").strip()
    except EOFError:
        entered = ""
    return entered or keep


def _confirm(question: str, *, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"  {question} {hint} ").strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer.startswith("y")


# ---------------------------------------------------------------------------
# Environment preflight (before touching any config)
# ---------------------------------------------------------------------------


def _check_environment() -> str | None:
    """Return an error message if this machine can't run the tool, else None."""
    if sys.platform == "win32":
        return (
            "git-dr-mirror is Unix-only (it uses flock for its run lock). "
            "On Windows, run it under WSL (Windows Subsystem for Linux), or "
            "use macOS/Linux."
        )
    try:
        import fcntl  # noqa: F401
    except ImportError:
        return (
            "This Python lacks the 'fcntl' module, so the run lock cannot "
            "work. git-dr-mirror needs a Unix-like OS (Linux, macOS, WSL)."
        )
    if shutil.which("git") is None:
        return (
            "'git' was not found on PATH. Install git with your package "
            "manager (e.g. 'sudo apt install git') and rerun setup."
        )
    return None


# ---------------------------------------------------------------------------
# Wizard steps
# ---------------------------------------------------------------------------


def _seed_env_file(env_path: Path, example_path: Path) -> None:
    """Create .env from .env.example if it doesn't exist yet."""
    if env_path.exists():
        return
    if example_path.exists():
        shutil.copy(example_path, env_path)
        _info(f"Created {env_path.name} from {example_path.name}.")
    else:
        env_path.touch()
        _info(f"Created an empty {env_path.name}.")
    os.chmod(env_path, 0o600)


def _collect_config(env_path: Path) -> dict[str, str]:
    """Prompt for required and common-optional settings; return updates."""
    current = _read_env(env_path)
    updates: dict[str, str] = {}

    _heading("Required settings")
    _info("Leave a field blank to keep the value shown in [brackets].")
    updates["GITHUB_TOKEN"] = _prompt(
        "GitHub token", current=current.get("GITHUB_TOKEN", ""), secret=True)
    updates["GITLAB_TOKEN"] = _prompt(
        "GitLab token", current=current.get("GITLAB_TOKEN", ""), secret=True)
    updates["GITLAB_GROUP"] = _prompt(
        "GitLab group path (must already exist, e.g. my-github-backup)",
        current=current.get("GITLAB_GROUP", ""))

    _heading("Common options")
    if _confirm("Customize hosts, visibility, or mirror directory?", default=False):
        updates["GITLAB_URL"] = _prompt(
            "GitLab URL", current=current.get("GITLAB_URL", ""),
            default="https://gitlab.com")
        updates["GITLAB_VISIBILITY"] = _prompt(
            "GitLab project visibility (private/internal/public)",
            current=current.get("GITLAB_VISIBILITY", ""), default="private")
        updates["MIRROR_DIR"] = _prompt(
            "Local mirror directory", current=current.get("MIRROR_DIR", ""),
            default="~/github-backup")
    # Drop keys the user left completely empty so we don't write blanks.
    return {k: v for k, v in updates.items() if v}


def _run_preflight(config) -> bool:
    """Run read-only credential checks; return True if all passed."""
    from . import preflight

    _heading("Verifying credentials (read-only, nothing is created)")
    all_ok = True
    for result in preflight.run_all(config):
        if result.ok:
            _ok(f"{result.title}: {result.detail}")
        else:
            all_ok = False
            _fail(f"{result.title}: {result.detail}")
            if result.hint:
                _info(_c("33", f"    → {result.hint}"))
    return all_ok


def run_wizard(env_file: str | None = None) -> int:
    """Run the interactive setup wizard. Returns a process exit code."""
    try:
        print(_bold("git-dr-mirror setup"))

        # 1. Environment must be able to run the tool at all.
        env_error = _check_environment()
        if env_error:
            _heading("Environment")
            _fail(env_error)
            return 2
        _heading("Environment")
        _ok(f"Python {sys.version.split()[0]}, git found, Unix-like OS.")

        # 2. Seed and collect configuration.
        env_path = Path(env_file) if env_file else Path.cwd() / ".env"
        example_path = Path(__file__).resolve().parents[2] / ".env.example"
        if not example_path.exists():
            example_path = Path.cwd() / ".env.example"
        _seed_env_file(env_path, example_path)

        updates = _collect_config(env_path)
        _write_env(env_path, updates)
        _info(f"Saved {env_path} (mode 600).")

        # 3. Load config and run preflight checks.
        from .config import ConfigError, load_config
        try:
            config = load_config(env_file=str(env_path))
        except ConfigError as exc:
            _heading("Configuration")
            _fail(str(exc))
            return 2

        checks_ok = _run_preflight(config)
        if not checks_ok:
            _heading("Next steps")
            _info("Fix the issues above and rerun: git-dr-mirror setup")
            return 1

        # 4. Optional dry-run preview.
        _heading("Preview")
        if _confirm("Do a dry-run now (lists what would be mirrored)?", default=True):
            from .runner import run_backup
            try:
                result = run_backup(config, dry_run=True)
                _ok(f"Dry-run complete: {result.summary()}")
            except Exception as exc:  # keep the wizard friendly
                _fail(f"Dry-run could not complete: {exc}")

        # 5. Summary.
        _heading("You're set up")
        _info("Run a backup now:   git-dr-mirror")
        _info("Enable the timer:   systemctl --user enable --now git-dr-mirror.timer")
        _info("Watch logs:         journalctl --user -u git-dr-mirror -f")
        return 0
    except KeyboardInterrupt:
        print()
        _warn("Setup cancelled.")
        return 130
