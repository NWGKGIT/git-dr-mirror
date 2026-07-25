"""Command-line entry point for git-dr-mirror."""

from __future__ import annotations

import argparse
import logging
import sys

from . import __version__
from .config import ConfigError, load_config
from .lock import LockHeldError
from .runner import run_backup

log = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="git-dr-mirror",
        description=(
            "One-way disaster-recovery mirror of your GitHub repositories "
            "to GitLab. Configuration comes from environment variables or a "
            ".env file (see .env.example)."
        ),
        epilog=(
            "subcommands:\n"
            "  setup    interactive first-time setup (prompts, writes .env,\n"
            "           verifies your tokens)\n"
            "  check    verify credentials and group access, then exit\n"
            "\n"
            "with no subcommand, runs a backup."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="discover repositories and report what would happen, "
        "but change nothing locally or on GitLab",
    )
    parser.add_argument(
        "--repo",
        metavar="PATTERN",
        help="only process repositories whose name matches this glob "
        "(e.g. --repo my-project); useful for testing",
    )
    parser.add_argument(
        "--env-file",
        metavar="PATH",
        help="load configuration from this dotenv file instead of ./.env",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _run_check(env_file: str | None) -> int:
    """Non-interactive credential check: print results, exit 0/1."""
    from . import preflight

    try:
        config = load_config(env_file=env_file)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    all_ok = True
    for result in preflight.run_all(config):
        mark = "OK  " if result.ok else "FAIL"
        print(f"[{mark}] {result.title}: {result.detail}")
        if not result.ok:
            all_ok = False
            if result.hint:
                print(f"       {result.hint}")
    return 0 if all_ok else 1


def _parse_subcommand_args(rest: list[str]) -> str | None:
    """Parse the optional ``--env-file PATH`` flag for setup/check.

    Using argparse here means both ``--env-file PATH`` and ``--env-file=PATH``
    work, regardless of argument ordering.
    """
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--env-file", metavar="PATH", default=None)
    args, _ = p.parse_known_args(rest)
    return args.env_file


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv

    # Lightweight subcommand layer. The bare command still runs a backup, so
    # existing usage and tests are unaffected.
    if raw and raw[0] == "setup":
        from .wizard import run_wizard

        env_file = _parse_subcommand_args(raw[1:])
        return run_wizard(env_file=env_file)
    if raw and raw[0] == "check":
        env_file = _parse_subcommand_args(raw[1:])
        return _run_check(env_file)

    args = build_parser().parse_args(raw)

    try:
        config = load_config(env_file=args.env_file)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        result = run_backup(config, dry_run=args.dry_run, only_repo=args.repo)
    except LockHeldError as exc:
        # Overlapping schedule ticks are normal operation, not a failure.
        log.info("%s", exc)
        return 0
    except KeyboardInterrupt:
        # Interrupted runs are safe: everything is idempotent and the next
        # run recovers automatically.
        log.warning("Interrupted; rerun to resume — the backup is idempotent.")
        return 130

    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
