"""Command-line interface and mode dispatch for sbr-config."""

import argparse
import sys
import logging
from typing import List

from . import __version__
from .configurator import apply_changes
from .detector import detect_system_state
from .exceptions import SbrConfigError
from .logger import setup_logging
from .models import PlannedChange
from .output import Output
from .planner import plan_changes
from .rollback import list_backups, rollback, save_state
from .utils import FileLock, check_root
from .validator import validate

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="sbr-config",
        description="Configure source-based routing for multi-NIC Linux systems.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  sbr-config --validate              Check current SBR state
  sbr-config --configure             Apply changes interactively
  sbr-config --configure --force     Apply without confirmation
  sbr-config --configure --persist   Also write boot-persistent config
  sbr-config --configure --dry-run   Show changes without applying
  sbr-config --rollback              Restore previous state
""",
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--validate",
        action="store_true",
        help="Check current SBR configuration and report findings",
    )
    mode.add_argument(
        "--configure",
        action="store_true",
        help="Compute and apply needed SBR changes",
    )
    mode.add_argument(
        "--rollback",
        action="store_true",
        help="Restore previous configuration from backup",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip interactive confirmation (use with --configure)",
    )
    parser.add_argument(
        "--persist",
        action="store_true",
        help="Write persistent config that survives reboot",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show proposed changes without applying them",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="IFACE",
        help="Exclude interface from SBR (repeatable)",
    )
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="IFACE",
        help="Only configure these interfaces (repeatable)",
    )
    parser.add_argument(
        "--backup-file",
        metavar="PATH",
        help="Specific backup file to restore from (with --rollback)",
    )
    parser.add_argument(
        "--log-file",
        default="/var/log/sbr-config.log",
        metavar="PATH",
        help="Log file path (default: /var/log/sbr-config.log)",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (use -vv for debug)",
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress non-error output",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    return parser


def main(argv: List[str] = None) -> int:
    """Main entry point.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    # Setup
    setup_logging(args.log_file, args.verbose)
    out = Output(color=not args.no_color, quiet=args.quiet)

    try:
        check_root()
    except SbrConfigError as e:
        out.error(str(e))
        return 1

    try:
        if args.validate:
            return _do_validate(args, out)
        elif args.configure:
            return _do_configure(args, out)
        elif args.rollback:
            return _do_rollback(args, out)
    except SbrConfigError as e:
        out.error(str(e))
        logger.exception("Fatal error")
        return 1
    except KeyboardInterrupt:
        out.nl()
        out.error("Interrupted by user")
        return 130

    return 0


# ---------------------------------------------------------------------------
# Mode implementations
# ---------------------------------------------------------------------------

def _do_validate(args: argparse.Namespace, out: Output) -> int:
    """Run validation-only mode."""
    out.banner()
    out.header("Detecting System State")

    state = detect_system_state(
        exclude=args.exclude,
        include=args.include,
    )

    out.info(f"Network manager: {state.network_manager.value}")
    out.interface_table(state.interfaces)

    out.header("Validation Results")
    results = validate(state)
    out.validation_report(results)

    passed = sum(1 for r in results if r.is_correct)
    failed = sum(1 for r in results if not r.is_correct)
    out.summary(passed, failed)

    if failed > 0:
        out.nl()
        out.info("Run 'sbr-config --configure' to fix detected issues.")
        return 1

    return 0


def _do_configure(args: argparse.Namespace, out: Output) -> int:
    """Run configuration mode."""
    with FileLock():
        out.banner()
        out.header("Detecting System State")

        state = detect_system_state(
            exclude=args.exclude,
            include=args.include,
        )

        out.info(f"Network manager: {state.network_manager.value}")
        out.interface_table(state.interfaces)

        # Validate
        out.header("Validating Current Configuration")
        results = validate(state)

        passed = sum(1 for r in results if r.is_correct)
        failed = sum(1 for r in results if not r.is_correct)
        out.summary(passed, failed)

        if failed == 0:
            out.nl()
            out.info("System is correctly configured for source-based routing.")
            return 0

        # Plan changes
        out.header("Proposed Changes")
        changes = plan_changes(state, results)

        if not changes:
            out.info("No actionable changes could be planned.")
            out.info("Some issues may require manual intervention.")
            return 1

        out.changes_report(changes)

        # Dry run stops here
        if args.dry_run:
            out.nl()
            out.info("Dry run complete. No changes were made.")
            return 0

        # Interactive confirmation
        if not args.force:
            if not out.prompt_yn(
                f"Apply {len(changes)} change(s) to the system?",
                default=False,
            ):
                out.info("Aborted by user. No changes made.")
                return 0

        # Save state for rollback
        out.header("Applying Changes")
        backup_path = save_state(state)
        out.info(f"State backup saved to: {backup_path}")

        # Apply changes
        applied = apply_changes(changes)
        out.nl()
        out.info(f"Successfully applied {applied} change(s).")

        # Write persistent config if requested
        if args.persist:
            out.header("Writing Persistent Configuration")
            _write_persistence(state, changes, out)

        out.nl()
        out.info("Source-based routing is now configured.")
        out.info(f"To undo these changes: sbr-config --rollback")

        return 0


def _do_rollback(args: argparse.Namespace, out: Output) -> int:
    """Run rollback mode."""
    with FileLock():
        out.banner()

        if not args.force:
            # Show available backups
            backups = list_backups()
            if not backups:
                out.error("No backups found. Nothing to roll back.")
                return 1

            out.header("Available Backups")
            for b in backups:
                marker = " (latest)" if b["is_latest"] else ""
                out.info(f"{b['timestamp']} -- {b['path']}{marker}")

            if not args.backup_file:
                if not out.prompt_yn(
                    "Restore from the latest backup?",
                    default=False,
                ):
                    out.info("Aborted.")
                    return 0

        out.header("Rolling Back")
        rollback(backup_path=args.backup_file)
        out.nl()
        out.info("Rollback complete. Previous SBR configuration has been removed.")
        out.info("Run 'sbr-config --validate' to verify the current state.")

        return 0


# ---------------------------------------------------------------------------
# Persistence helper
# ---------------------------------------------------------------------------

def _write_persistence(
    state,
    changes: List[PlannedChange],
    out: Output,
) -> None:
    """Write persistent configuration based on detected network manager."""
    from .persistence import write_persistence

    try:
        files = write_persistence(state, changes)
        for f in files:
            out.info(f"Wrote: {f}")
    except SbrConfigError as e:
        out.warning(f"Persistence failed: {e}")
        out.warning("Runtime changes are active but may not survive reboot.")
