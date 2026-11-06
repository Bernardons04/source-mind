import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import asyncpg

# Fixed 64-bit advisory lock ID for ManualMind migrations
MIGRATION_ADVISORY_LOCK_ID = 849204918239019283

# ==============================================================================
# Baseline 001 Structural Specifications
# ==============================================================================
BASELINE_001_REQUIRED_EXTENSIONS = ["uuid-ossp", "vector"]

BASELINE_001_REQUIRED_TABLES = [
    "assistants",
    "documents",
    "document_versions",
    "document_chunks",
    "chats",
    "messages",
    "message_events",
    "message_citations",
    "user_preferences",
]

# Key columns that are characteristic of the 001 schema (pre-phase-2).
# Presence of document_versions + document_chunks.document_version_id confirms
# the versioning model, while documents.current_version_number and
# document_versions.version_number pin the exact 001 structure.
# Does NOT verify all columns, exact types, nullability, PKs, UQ constraints,
# indexes, defaults, or other peripheral details.
BASELINE_001_CHARACTERISTIC_COLUMNS: Dict[str, List[str]] = {
    "documents": ["id", "assistant_id", "name", "current_version_number"],
    "document_versions": ["id", "document_id", "version_number", "storage_path"],
    "document_chunks": ["id", "document_version_id", "chunk_index", "content"],
    "assistants": ["id", "user_id", "name", "slug"],
}

# One representative FK confirming the versioning relationship is in place.
# Tuple: (source_table, source_column, target_table, target_column)
BASELINE_001_KEY_FOREIGN_KEY = ("document_chunks", "document_version_id", "document_versions", "id")


@dataclass
class MigrationFile:
    version: str
    version_num: int
    filename: str
    path: Path
    checksum: str


def mask_db_url(url: str) -> str:
    """Masks database credentials for secure diagnostic logging."""
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return "postgresql://***masked***"
        netloc = ""
        if parsed.username:
            netloc += parsed.username
            if parsed.password:
                netloc += ":****"
            netloc += "@"
        if parsed.hostname:
            netloc += parsed.hostname
        if parsed.port:
            netloc += f":{parsed.port}"
        return f"{parsed.scheme}://{netloc}{parsed.path}"
    except Exception:
        return "postgresql://***masked***"


def normalize_postgres_url(raw_url: str) -> str:
    """Normalizes SQLAlchemy connection strings (e.g. postgresql+asyncpg://) to standard postgresql://."""
    if raw_url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + raw_url[len("postgresql+asyncpg://"):]
    if raw_url.startswith("postgresql+psycopg://"):
        return "postgresql://" + raw_url[len("postgresql+psycopg://"):]
    if raw_url.startswith("postgresql+psycopg2://"):
        return "postgresql://" + raw_url[len("postgresql+psycopg2://"):]
    return raw_url


def calculate_file_checksum(file_path: Path) -> str:
    """Calculates SHA-256 checksum of file contents."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()


class MigrationRunner:
    """
    Automated PostgreSQL migration runner using asyncpg exclusively.
    Guarantees:
    - Pure read-only status and dry-run (zero side-effects when history is uninitialized).
    - Hard protection preventing automatic execution of migration 001.
    - Essential baseline validation for existing databases.
    - Transactional atomicity: migration SQL + history insert committed together.
    """

    def __init__(self, db_url: str, migrations_dir: Optional[Path] = None):
        self.raw_url = db_url
        self.normalized_url = normalize_postgres_url(db_url)
        self.masked_url = mask_db_url(self.normalized_url)

        if migrations_dir is None:
            self.migrations_dir = Path(__file__).resolve().parent.parent.parent / "migrations"
        else:
            self.migrations_dir = Path(migrations_dir)

    def discover_migrations(self) -> List[MigrationFile]:
        """
        Discovers, validates, and sorts all SQL migration files in migrations_dir.
        Enforces naming convention and detects duplicate versions.
        """
        if not self.migrations_dir.exists():
            return []

        migration_files: List[MigrationFile] = []
        seen_versions: Dict[int, str] = {}
        pattern = re.compile(r"^(\d+)_([a-zA-Z0-9_-]+)\.sql$")

        for file in sorted(self.migrations_dir.glob("*.sql")):
            match = pattern.match(file.name)
            if not match:
                # Skip auxiliary SQL files (e.g. verify_schema.sql) that are not migrations.
                continue

            version_str = match.group(1)
            version_num = int(version_str)

            if version_num in seen_versions:
                raise ValueError(
                    f"Duplicate migration version detected for version {version_num}: "
                    f"'{seen_versions[version_num]}' and '{file.name}'."
                )
            seen_versions[version_num] = file.name

            checksum = calculate_file_checksum(file)
            migration_files.append(
                MigrationFile(
                    version=version_str,
                    version_num=version_num,
                    filename=file.name,
                    path=file,
                    checksum=checksum,
                )
            )

        return sorted(migration_files, key=lambda m: m.version_num)

    async def _get_connection(self) -> asyncpg.Connection:
        """Creates a direct async connection using asyncpg."""
        return await asyncpg.connect(self.normalized_url)

    async def has_schema_migrations_table(self, conn: asyncpg.Connection) -> bool:
        """Read-only check to determine if the schema_migrations table exists."""
        sql = """
        SELECT EXISTS (
            SELECT 1 
            FROM information_schema.tables 
            WHERE table_schema = 'public' AND table_name = 'schema_migrations'
        );
        """
        return bool(await conn.fetchval(sql))

    async def ensure_schema_migrations_table(self, conn: asyncpg.Connection) -> None:
        """Creates the schema_migrations tracking table if it does not already exist."""
        sql = """
        CREATE TABLE IF NOT EXISTS public.schema_migrations (
            version VARCHAR(50) PRIMARY KEY,
            filename VARCHAR(255) NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            checksum VARCHAR(64) NOT NULL
        );
        """
        await conn.execute(sql)

    async def acquire_advisory_lock(self, conn: asyncpg.Connection) -> bool:
        """Acquires a session-level PostgreSQL advisory lock. Returns False if already locked."""
        sql = f"SELECT pg_try_advisory_lock({MIGRATION_ADVISORY_LOCK_ID});"
        res = await conn.fetchval(sql)
        return bool(res)

    async def release_advisory_lock(self, conn: asyncpg.Connection) -> bool:
        """Releases the session-level advisory lock."""
        sql = f"SELECT pg_advisory_unlock({MIGRATION_ADVISORY_LOCK_ID});"
        res = await conn.fetchval(sql)
        return bool(res)

    async def get_applied_migrations(self, conn: asyncpg.Connection) -> Dict[str, Dict[str, Any]]:
        """Returns all applied migrations from schema_migrations keyed by version."""
        if not await self.has_schema_migrations_table(conn):
            return {}

        sql = "SELECT version, filename, applied_at, checksum FROM public.schema_migrations ORDER BY applied_at ASC;"
        rows = await conn.fetch(sql)

        applied = {}
        for r in rows:
            applied[r["version"]] = {
                "version": r["version"],
                "filename": r["filename"],
                "applied_at": r["applied_at"],
                "checksum": r["checksum"],
            }
        return applied

    async def verify_baseline_001(self, conn: asyncpg.Connection) -> Tuple[bool, List[str]]:
        """
        Verifies that the remote database schema is compatible with migration 001.

        Checks:
        1. Required extensions (uuid-ossp, vector).
        2. All 9 tables exist in the public schema.
        3. Characteristic columns that identify the 001 schema structure:
           documents.current_version_number, document_versions.version_number,
           document_chunks.document_version_id, and key columns in assistants.
        4. One representative FK: document_chunks.document_version_id -> document_versions.id.

        Does NOT verify: column types/nullability, all FKs, PKs, unique constraints,
        indexes, defaults, or other peripheral schema details.
        """
        missing: List[str] = []

        # 1. Extensions
        ext_rows = await conn.fetch("SELECT extname FROM pg_extension;")
        existing_exts = set(r["extname"] for r in ext_rows)
        for ext in BASELINE_001_REQUIRED_EXTENSIONS:
            if ext not in existing_exts:
                missing.append(f"Extension '{ext}' is missing")

        # 2. Tables
        table_rows = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';"
        )
        existing_tables = set(r["table_name"] for r in table_rows)
        for tbl in BASELINE_001_REQUIRED_TABLES:
            if tbl not in existing_tables:
                missing.append(f"Table '{tbl}' is missing")

        # 3. Characteristic columns
        col_rows = await conn.fetch(
            "SELECT table_name, column_name "
            "FROM information_schema.columns WHERE table_schema = 'public';"
        )
        existing_cols: Dict[str, set] = {}
        for r in col_rows:
            existing_cols.setdefault(r["table_name"], set()).add(r["column_name"])

        for tbl, columns in BASELINE_001_CHARACTERISTIC_COLUMNS.items():
            if tbl in existing_tables:
                for col in columns:
                    if col not in existing_cols.get(tbl, set()):
                        missing.append(f"Column '{tbl}.{col}' is missing")

        # 4. Representative FK: document_chunks.document_version_id -> document_versions.id
        src_tbl, src_col, tgt_tbl, tgt_col = BASELINE_001_KEY_FOREIGN_KEY
        fk_rows = await conn.fetch(
            "SELECT "
            "  tc.table_name AS source_table, "
            "  kcu.column_name AS source_column, "
            "  ccu.table_name AS target_table, "
            "  ccu.column_name AS target_column "
            "FROM information_schema.table_constraints AS tc "
            "JOIN information_schema.key_column_usage AS kcu "
            "  ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema "
            "JOIN information_schema.constraint_column_usage AS ccu "
            "  ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema "
            "WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public' "
            f"  AND tc.table_name = '{src_tbl}' AND kcu.column_name = '{src_col}';"
        )
        fk_found = any(
            r["source_table"] == src_tbl
            and r["source_column"] == src_col
            and r["target_table"] == tgt_tbl
            and r["target_column"] == tgt_col
            for r in fk_rows
        )
        if not fk_found:
            missing.append(
                f"Foreign key '{src_tbl}.{src_col} -> {tgt_tbl}.{tgt_col}' is missing"
            )

        return (len(missing) == 0, missing)

    async def bootstrap_baseline(self, target_version: str = "001") -> Dict[str, Any]:
        """
        Safely records an existing schema baseline (001) in schema_migrations without executing SQL.
        Verifies actual database schema presence, checks checksums if already recorded,
        and creates table + baseline record inside a single transaction.
        """
        conn = await self._get_connection()
        try:
            locked = await self.acquire_advisory_lock(conn)
            if not locked:
                raise RuntimeError(
                    f"Could not acquire migration advisory lock ({MIGRATION_ADVISORY_LOCK_ID}). Another process is migrating."
                )

            all_files = self.discover_migrations()
            target_file = next((f for f in all_files if f.version == target_version), None)
            if not target_file:
                raise FileNotFoundError(
                    f"Migration file for version '{target_version}' not found in {self.migrations_dir}."
                )

            has_table = await self.has_schema_migrations_table(conn)
            if has_table:
                applied = await self.get_applied_migrations(conn)
                if target_version in applied:
                    recorded_checksum = applied[target_version]["checksum"]
                    if recorded_checksum != target_file.checksum:
                        raise RuntimeError(
                            f"Baseline integrity error: Migration {target_version} is already registered with checksum "
                            f"{recorded_checksum}, but local file has {target_file.checksum}."
                        )
                    return {
                        "status": "already_recorded",
                        "version": target_version,
                        "filename": applied[target_version]["filename"],
                        "message": f"Migration {target_version} is already recorded as applied with matching checksum.",
                    }

            # Verify that remote schema actually matches 001
            if target_version == "001":
                is_valid, missing = await self.verify_baseline_001(conn)
                if not is_valid:
                    raise RuntimeError(
                        f"Cannot baseline migration 001: remote database schema does not match migration 001.\n"
                        f"Missing or mismatched structural objects:\n  - " + "\n  - ".join(missing)
                    )

            # Create table and insert baseline in a SINGLE atomic transaction
            async with conn.transaction():
                await self.ensure_schema_migrations_table(conn)
                insert_sql = """
                INSERT INTO public.schema_migrations (version, filename, applied_at, checksum)
                VALUES ($1, $2, now(), $3);
                """
                await conn.execute(insert_sql, target_file.version, target_file.filename, target_file.checksum)

            return {
                "status": "success",
                "version": target_file.version,
                "filename": target_file.filename,
                "checksum": target_file.checksum,
                "message": f"Successfully verified schema and registered migration {target_file.version} as baseline.",
            }

        finally:
            try:
                await self.release_advisory_lock(conn)
            except Exception:
                pass
            await conn.close()

    async def get_status(self) -> Dict[str, Any]:
        """
        Pure read-only status query.
        Does NOT create schema_migrations table or execute DDL.
        Detects APPLIED, PENDING, CHECKSUM_MISMATCH, and MISSING_LOCAL_FILE.
        """
        conn = await self._get_connection()
        try:
            discovered = self.discover_migrations()
            has_table = await self.has_schema_migrations_table(conn)

            if not has_table:
                items = [
                    {
                        "version": m.version,
                        "filename": m.filename,
                        "status": "PENDING (uninitialized)",
                        "applied_at": None,
                        "file_checksum": m.checksum,
                        "applied_checksum": None,
                    }
                    for m in discovered
                ]
                return {
                    "history_initialized": False,
                    "items": items,
                }

            applied = await self.get_applied_migrations(conn)
            discovered_map = {m.version: m for m in discovered}
            status_list = []

            # Check all local migrations
            for m in discovered:
                if m.version in applied:
                    app_info = applied[m.version]
                    if m.checksum == app_info["checksum"]:
                        status = "APPLIED"
                    else:
                        status = "CHECKSUM_MISMATCH"

                    status_list.append({
                        "version": m.version,
                        "filename": m.filename,
                        "status": status,
                        "applied_at": app_info["applied_at"],
                        "file_checksum": m.checksum,
                        "applied_checksum": app_info["checksum"],
                    })
                else:
                    status_list.append({
                        "version": m.version,
                        "filename": m.filename,
                        "status": "PENDING",
                        "applied_at": None,
                        "file_checksum": m.checksum,
                        "applied_checksum": None,
                    })

            # Check for migrations recorded in DB whose file is missing locally
            for ver, app_info in applied.items():
                if ver not in discovered_map:
                    status_list.append({
                        "version": ver,
                        "filename": app_info["filename"],
                        "status": "MISSING_LOCAL_FILE",
                        "applied_at": app_info["applied_at"],
                        "file_checksum": None,
                        "applied_checksum": app_info["checksum"],
                    })

            # Sort by version: numeric versions first (by value), then non-numeric (alphabetically).
            # Always returns a tuple (int, str) to avoid TypeError when comparing int with str.
            status_list.sort(key=lambda s: (0, s["version"].zfill(20)) if s["version"].isdigit() else (1, s["version"]))

            return {
                "history_initialized": True,
                "items": status_list,
            }

        finally:
            await conn.close()

    async def apply_pending(self, dry_run: bool = False, up_to: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Applies pending migrations in chronological order.
        Strict safeguards:
        - If schema_migrations does not exist, ABORTS immediately without creating tables or executing SQL.
        - In dry_run mode, informs that baseline is required if history does not exist.
        - Performs integrity validation on existing history before running any pending migrations.
        - Executes each migration and records history in a single transaction.
        """
        conn = await self._get_connection()
        try:
            locked = await self.acquire_advisory_lock(conn)
            if not locked:
                raise RuntimeError(
                    f"Could not acquire migration advisory lock ({MIGRATION_ADVISORY_LOCK_ID}). Another process is migrating."
                )

            has_table = await self.has_schema_migrations_table(conn)

            # Strict guard against accidental execution of migration 001
            if not has_table:
                if dry_run:
                    return [
                        {
                            "version": "UNINITIALIZED",
                            "filename": "N/A",
                            "status": "UNINITIALIZED_BASELINE_REQUIRED",
                            "message": (
                                "Table 'public.schema_migrations' does not exist. Migration history is uninitialized. "
                                "For an existing database with initial schema, you must run "
                                "'python migrate.py baseline --version 001' before planning or applying migrations."
                            ),
                        }
                    ]
                else:
                    raise RuntimeError(
                        "Cannot apply migrations: table 'public.schema_migrations' does not exist.\n"
                        "The database history must be explicitly initialized first to prevent accidental re-execution of schema 001.\n"
                        "Action required: Run 'python migrate.py baseline --version 001' to verify and register your existing schema."
                    )

            discovered = self.discover_migrations()
            applied = await self.get_applied_migrations(conn)

            # Integrity checks on existing history
            for version, app_info in applied.items():
                matching_file = next((m for m in discovered if m.version == version), None)
                if matching_file is None:
                    raise RuntimeError(
                        f"Integrity check failed: Migration {version} ('{app_info['filename']}') is recorded "
                        f"as applied in schema_migrations, but its local file is missing from {self.migrations_dir}. "
                        f"Aborting execution."
                    )
                if matching_file.checksum != app_info["checksum"]:
                    raise RuntimeError(
                        f"Integrity check failed: Checksum mismatch for migration {version} ('{matching_file.filename}'). "
                        f"Recorded in DB: {app_info['checksum']}, Local file: {matching_file.checksum}. "
                        f"Applied migrations must never be altered. Aborting execution."
                    )

            # Filter pending migrations
            pending = [m for m in discovered if m.version not in applied]

            if up_to is not None:
                try:
                    up_to_num = int(up_to)
                except ValueError:
                    raise ValueError(f"Invalid --up-to version '{up_to}'. Version must be numeric.")
                pending = [m for m in pending if m.version_num <= up_to_num]

            if dry_run:
                # Dry run returns plan without modifying database
                return [
                    {
                        "version": m.version,
                        "filename": m.filename,
                        "status": "DRY_RUN",
                    }
                    for m in pending
                ]

            if not pending:
                return []

            results = []
            for migration in pending:
                with open(migration.path, "r", encoding="utf-8") as f:
                    sql_content = f.read()

                # Single atomic transaction per migration:
                # Executes the migration SQL AND records it in schema_migrations.
                # If either fails, asyncpg rolls back the entire transaction.
                async with conn.transaction():
                    await conn.execute(sql_content)
                    record_sql = """
                    INSERT INTO public.schema_migrations (version, filename, applied_at, checksum)
                    VALUES ($1, $2, now(), $3);
                    """
                    await conn.execute(record_sql, migration.version, migration.filename, migration.checksum)

                results.append({
                    "version": migration.version,
                    "filename": migration.filename,
                    "status": "APPLIED",
                })

            return results

        finally:
            try:
                await self.release_advisory_lock(conn)
            except Exception:
                pass
            await conn.close()
