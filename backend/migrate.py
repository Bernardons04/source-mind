#!/usr/bin/env python
"""
ManualMind Migration CLI
Automated PostgreSQL database migration executor.

Commands:
    status    - Displays status of all migrations (APPLIED, PENDING, CHECKSUM_MISMATCH)
    baseline  - Verifies remote schema and marks migration 001 as applied without re-running SQL
    apply     - Applies all pending migrations in chronological order inside safe transactions
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# Load .env if present
try:
    from dotenv import load_dotenv
    for env_path in [backend_dir / ".env", backend_dir.parent / ".env"]:
        if env_path.exists():
            load_dotenv(env_path)
            break
except ImportError:
    pass

from app.config import settings
from app.migrations.runner import MigrationRunner, mask_db_url


def format_timestamp(dt) -> str:
    if not dt:
        return "-"
    if hasattr(dt, "strftime"):
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    return str(dt)[:19]


async def cmd_status(runner: MigrationRunner):
    print(f"\nTarget Database: {runner.masked_url}")
    print(f"Migrations Directory: {runner.migrations_dir}\n")

    res = await runner.get_status()
    history_init = res["history_initialized"]
    items = res["items"]

    if not history_init:
        print("[NOTICE] Table 'schema_migrations' does not exist yet (history uninitialized).")
        print("Run 'python migrate.py baseline --version 001' to verify and register your initial schema.\n")

    if not items:
        print("No migration files found in migrations directory.\n")
        return

    # Print table header
    header = f"{'VERSION':<10} | {'STATUS':<20} | {'FILENAME':<35} | {'APPLIED AT':<23}"
    print(header)
    print("-" * len(header))

    pending_count = 0
    applied_count = 0
    mismatch_count = 0

    for s in items:
        ver = s["version"]
        fname = s["filename"]
        st = s["status"]
        applied_at = format_timestamp(s["applied_at"])

        if st == "APPLIED":
            applied_count += 1
            status_display = f"\033[92m{st:<20}\033[0m" if sys.stdout.isatty() else f"{st:<20}"
        elif "PENDING" in st:
            pending_count += 1
            status_display = f"\033[93m{st:<20}\033[0m" if sys.stdout.isatty() else f"{st:<20}"
        else:
            mismatch_count += 1
            status_display = f"\033[91m{st:<20}\033[0m" if sys.stdout.isatty() else f"{st:<20}"

        print(f"{ver:<10} | {status_display} | {fname:<35} | {applied_at:<23}")

    print("\nSummary:")
    print(f"  Applied:     {applied_count}")
    print(f"  Pending:     {pending_count}")
    if mismatch_count > 0:
        print(f"  Inconsistent/Mismatched: {mismatch_count}")
    print()


async def cmd_baseline(runner: MigrationRunner, version: str):
    print(f"\nTarget Database: {runner.masked_url}")
    print(f"Verifying schema baseline for migration '{version}'...")

    res = await runner.bootstrap_baseline(target_version=version)
    if res["status"] == "already_recorded":
        print(f"\n[INFO] {res['message']}\n")
    else:
        print(f"\n[SUCCESS] {res['message']}")
        print(f"Recorded checksum: {res['checksum'][:16]}...\n")


async def cmd_apply(runner: MigrationRunner, dry_run: bool, up_to: str = None):
    print(f"\nTarget Database: {runner.masked_url}")
    if dry_run:
        print("[DRY-RUN MODE] Pure simulation. No changes will be executed or committed to the database.\n")

    results = await runner.apply_pending(dry_run=dry_run, up_to=up_to)
    if not results:
        print("No pending migrations found. Database is up to date.\n")
        return

    if results and results[0].get("status") == "UNINITIALIZED_BASELINE_REQUIRED":
        print("[NOTICE] Migration history is uninitialized.")
        print(f"  {results[0]['message']}")
        print("\nAction required for existing database:")
        print("  python migrate.py baseline --version 001\n")
        return

    print(f"Found {len(results)} migration(s) to process:\n")
    for r in results:
        v = r["version"]
        f = r["filename"]
        st = r["status"]
        if st == "APPLIED":
            print(f"  [OK] Applied and committed {v} ({f})")
        elif st == "DRY_RUN":
            print(f"  [DRY-RUN] Planned execution: {v} ({f})")

    print("\nAll migration operations completed successfully.\n")


def main():
    parser = argparse.ArgumentParser(
        description="ManualMind Automated Database Migration Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Command: status
    parser_status = subparsers.add_parser(
        "status", help="List all migrations and their current status (pure read-only)"
    )

    # Command: baseline
    parser_baseline = subparsers.add_parser(
        "baseline",
        help="Verify remote schema and register an existing baseline (default: 001) without running its SQL",
    )
    parser_baseline.add_argument(
        "--version",
        default="001",
        help="Migration version to register as baseline (default: 001)",
    )

    # Command: apply
    parser_apply = subparsers.add_parser("apply", help="Apply pending migrations in chronological order")
    parser_apply.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate execution without applying SQL or modifying the database",
    )
    parser_apply.add_argument(
        "--up-to",
        help="Apply migrations only up to the specified version (numeric)",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    db_url = settings.DATABASE_URL
    if not db_url:
        print("ERROR: DATABASE_URL is not set in environment or .env file.", file=sys.stderr)
        sys.exit(1)

    runner = MigrationRunner(db_url=db_url)

    try:
        if args.command == "status":
            asyncio.run(cmd_status(runner))
        elif args.command == "baseline":
            asyncio.run(cmd_baseline(runner, args.version))
        elif args.command == "apply":
            asyncio.run(cmd_apply(runner, dry_run=args.dry_run, up_to=args.up_to))
    except Exception as e:
        print(f"\n[ERROR] Migration failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
