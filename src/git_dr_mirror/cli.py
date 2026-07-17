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
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

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
