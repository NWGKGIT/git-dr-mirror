"""Interactive one-shot setup wizard.

Walks a new user through configuration: checks the environment is supported,
seeds ``.env`` from ``.env.example``, prompts for the required settings with
per-field validation, verifies the tokens with read-only preflight checks,
and optionally previews a run. Invalid input is re-prompted immediately, and
failing credential checks loop back to the affected fields instead of
aborting, so a typo never means starting over.

Uses only the standard library for prompting (no extra dependencies).
"""

from __future__ import annotations

import getpass
import os
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit

#: How to invoke the tool from the repository root (nothing is put on PATH).
_BIN = "./.venv/bin/git-dr-mirror"


class WizardAbort(Exception):
    """Raised when no more input can be read (stdin closed mid-wizard)."""


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
# Field definitions and validators
# ---------------------------------------------------------------------------


def _validate_token(value: str) -> str | None:
    if any(ch.isspace() for ch in value):
        return "A token cannot contain spaces. Paste it exactly as issued."
    return None


def _normalize_group(value: str) -> str:
    """Accept a pasted group URL and reduce it to the group path."""
    if "://" in value:
        value = urlsplit(value).path
    return value.strip().strip("/")


def _validate_group(value: str) -> str | None:
    if any(ch.isspace() for ch in value):
        return "A group path cannot contain spaces (example: my-github-backup)."
    return None


def _validate_url(value: str) -> str | None:
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return "Enter a full URL, for example https://gitlab.example.com"
    return None


def _validate_visibility(value: str) -> str | None:
    if value not in ("private", "internal", "public"):
        return "Choose one of: private, internal, public."
    return None


#: Everything the wizard knows how to prompt for, keyed by .env variable.
_FIELDS: dict[str, dict] = {
    "GITHUB_TOKEN": {
        "label": "GitHub token",
        "secret": True,
        "required": True,
        "validate": _validate_token,
    },
    "GITLAB_TOKEN": {
        "label": "GitLab token",
        "secret": True,
        "required": True,
        "validate": _validate_token,
    },
    "GITLAB_GROUP": {
        "label": "GitLab group path (must already exist, e.g. my-github-backup)",
        "required": True,
        "normalize": _normalize_group,
        "validate": _validate_group,
    },
    "GITLAB_URL": {
        "label": "GitLab URL",
        "default": "https://gitlab.com",
        "validate": _validate_url,
    },
    "GITLAB_VISIBILITY": {
        "label": "GitLab project visibility (private/internal/public)",
        "default": "private",
        "normalize": str.lower,
        "validate": _validate_visibility,
    },
    "MIRROR_DIR": {
        "label": "Local mirror directory",
        "default": "~/github-backup",
    },
}

_REQUIRED_KEYS = ("GITHUB_TOKEN", "GITLAB_TOKEN", "GITLAB_GROUP")
_OPTIONAL_KEYS = ("GITLAB_URL", "GITLAB_VISIBILITY", "MIRROR_DIR")


# ---------------------------------------------------------------------------
# Prompting
# ---------------------------------------------------------------------------


def _read_line(prompt_text: str, *, secret: bool) -> str:
    """Read one line of input; raise WizardAbort when stdin is exhausted."""
    # getpass needs a real terminal to suppress echo; without one it prints
    # noisy warnings, so fall back to input() (nothing echoes anyway).
    use_getpass = secret and sys.stdin.isatty()
    reader = getpass.getpass if use_getpass else input
    try:
        return reader(prompt_text).strip()
    except EOFError:
        raise WizardAbort(
            "No input available (stdin is closed). Run the wizard from an "
            "interactive terminal, or fill in .env by hand (see .env.example)."
        ) from None


def _prompt_field(key: str, current: str) -> str:
    """Prompt for one field, looping until the value validates.

    Pressing Enter keeps the current value (if any), else the default.
    Secrets are read without echo and shown masked.
    """
    spec = _FIELDS[key]
    secret = spec.get("secret", False)
    default = spec.get("default", "")
    keep = current or default
    shown = _mask(current) if secret and current else keep
    suffix = f" [{shown}]" if shown else ""

    while True:
        entered = _read_line(f"  {spec['label']}{suffix}: ", secret=secret)
        value = entered or keep
        normalize = spec.get("normalize")
        if normalize:
            normalized = normalize(value)
            if entered and normalized != value:
                _info(f"Using {normalized!r}.")
            value = normalized
        if not value:
            if spec.get("required"):
                _warn("This field is required.")
                continue
            return ""
        validate = spec.get("validate")
        error = validate(value) if validate else None
        if error:
            _warn(error)
            continue
        return value


def _prompt_fields(keys, env_path: Path) -> dict[str, str]:
    """Prompt for the given fields; return the entered (non-empty) values."""
    current = _read_env(env_path)
    updates: dict[str, str] = {}
    for key in keys:
        value = _prompt_field(key, current.get(key, ""))
        if value:
            updates[key] = value
    return updates


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


def _has_systemd() -> bool:
    """Return True if a systemd user session is reachable on this machine."""
    import subprocess
    result = subprocess.run(
        ["systemctl", "--user", "show-environment"],
        capture_output=True,
    )
    return result.returncode == 0


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
    _heading("Required settings")
    _info("Leave a field blank to keep the value shown in [brackets].")
    updates = _prompt_fields(_REQUIRED_KEYS, env_path)

    _heading("Common options")
    if _confirm("Customize hosts, visibility, or mirror directory?", default=False):
        updates.update(_prompt_fields(_OPTIONAL_KEYS, env_path))
    return updates


def _run_preflight(config) -> list:
    """Run read-only credential checks; print and return the results."""
    from . import preflight

    _heading("Verifying credentials (read-only, nothing is created)")
    results = preflight.run_all(config)
    for result in results:
        if result.ok:
            _ok(f"{result.title}: {result.detail}")
        else:
            _fail(f"{result.title}: {result.detail}")
            if result.hint:
                _info(_c("33", f"    → {result.hint}"))
    return results


def _verify_until_ok(env_path: Path, updates: dict[str, str]) -> object:
    """Write config, run the checks, and loop on failure.

    Returns the validated Config on success, or None if the user declined
    another attempt (a message with resume instructions is printed).
    """
    from .config import ConfigError, load_config

    while True:
        _write_env(env_path, updates)
        _info(f"Saved {env_path} (mode 600).")
        # The wizard's answers must win over anything inherited from the
        # shell, and over values load_dotenv cached on a previous loop pass.
        os.environ.update(updates)

        try:
            config = load_config(env_file=str(env_path))
        except ConfigError as exc:
            _heading("Configuration")
            _fail(str(exc))
            if not _confirm("Correct the required values now?", default=True):
                _info(f"Resume anytime with: {_BIN} setup")
                return None
            updates = _prompt_fields(_REQUIRED_KEYS, env_path)
            continue

        results = _run_preflight(config)
        failing = [r for r in results if not r.ok]
        if not failing:
            return config

        # Re-prompt only the fields tied to the failing checks (in order,
        # deduplicated), then check again.
        retry_keys = []
        for result in failing:
            for key in result.keys or _REQUIRED_KEYS:
                if key in _FIELDS and key not in retry_keys:
                    retry_keys.append(key)
        if not _confirm("Re-enter the failing values and check again?", default=True):
            _info(f"Resume anytime with: {_BIN} setup")
            _info(f"Re-run just the checks with: {_BIN} check")
            return None
        _info("Press Enter to keep a value and retry the check as-is.")
        updates = _prompt_fields(retry_keys, env_path)


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

        # 3. Write, verify, and loop until the checks pass (or the user stops).
        config = _verify_until_ok(env_path, updates)
        if config is None:
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
        _info(f"Run a backup now:   {_BIN}")
        if _has_systemd():
            _info("Enable the timer:   systemctl --user enable --now git-dr-mirror.timer")
            _info("Watch logs:         journalctl --user -u git-dr-mirror -f")
        else:
            _info("Schedule with cron (no systemd detected):")
            _info(f"  17 */6 * * *  cd $(pwd) && {_BIN} >> ~/.local/state/git-dr-mirror.log 2>&1")
            _info("Logs go to ~/.local/state/git-dr-mirror.log")
        return 0
    except WizardAbort as exc:
        print()
        _fail(str(exc))
        return 2
    except KeyboardInterrupt:
        print()
        _warn("Setup cancelled.")
        return 130
