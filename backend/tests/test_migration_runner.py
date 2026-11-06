from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest
import asyncpg
from app.migrations.runner import (
    MigrationRunner,
    calculate_file_checksum,
    BASELINE_001_REQUIRED_EXTENSIONS,
    BASELINE_001_REQUIRED_TABLES,
    BASELINE_001_CHARACTERISTIC_COLUMNS,
    BASELINE_001_KEY_FOREIGN_KEY,
)


class MockAsyncContextManager:
    """Async context manager helper for mocking asyncpg transactions."""
    def __init__(self):
        self.entered = False
        self.exited = False
        self.exc_type = None

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.exited = True
        self.exc_type = exc_type
        return False  # Do not suppress exceptions


def create_mock_connection():
    """Builds a properly configured mock asyncpg Connection."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    conn.execute = AsyncMock(return_value=None)
    conn.close = AsyncMock(return_value=None)

    # In asyncpg, conn.transaction() is a sync call returning an async context manager
    tx = MockAsyncContextManager()
    conn.transaction = MagicMock(return_value=tx)
    return conn, tx


# ==============================================================================
# 1. Migration discovery
# ==============================================================================

def test_numeric_version_ordering(tmp_path):
    (tmp_path / "1_first.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "2_second.sql").write_text("SELECT 2;", encoding="utf-8")
    (tmp_path / "10_tenth.sql").write_text("SELECT 10;", encoding="utf-8")

    runner = MigrationRunner(db_url="postgresql://mock:mock@localhost:5432/test", migrations_dir=tmp_path)
    migrations = runner.discover_migrations()

    assert [m.version for m in migrations] == ["1", "2", "10"]
    assert [m.version_num for m in migrations] == [1, 2, 10]


def test_rejection_of_duplicate_version(tmp_path):
    (tmp_path / "001_first.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "001_duplicate.sql").write_text("SELECT 2;", encoding="utf-8")

    runner = MigrationRunner(db_url="postgresql://mock:mock@localhost:5432/test", migrations_dir=tmp_path)
    with pytest.raises(ValueError, match="Duplicate migration version detected"):
        runner.discover_migrations()


def test_ignores_auxiliary_sql_files(tmp_path):
    """Auxiliary SQL files without the <number>_<name>.sql pattern must be silently ignored."""
    (tmp_path / "001_init.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "verify_schema.sql").write_text("SELECT * FROM pg_tables;", encoding="utf-8")
    (tmp_path / "notes.sql").write_text("-- notes", encoding="utf-8")

    runner = MigrationRunner(db_url="postgresql://mock:mock@localhost:5432/test", migrations_dir=tmp_path)
    migrations = runner.discover_migrations()

    assert [m.version for m in migrations] == ["001"]


# ==============================================================================
# 2. apply blocked without migration history
# ==============================================================================

@pytest.mark.anyio
async def test_apply_fails_and_does_not_modify_db_when_table_missing(tmp_path):
    (tmp_path / "001_init.sql").write_text("CREATE TABLE test (id INT);", encoding="utf-8")

    runner = MigrationRunner(db_url="postgresql://mock:mock@localhost:5432/test", migrations_dir=tmp_path)
    conn, _ = create_mock_connection()

    # Advisory lock returns True, but has_schema_migrations_table returns False
    conn.fetchval.side_effect = lambda q: True if "advisory" in q else False
    runner._get_connection = AsyncMock(return_value=conn)

    with pytest.raises(RuntimeError, match="table 'public.schema_migrations' does not exist"):
        await runner.apply_pending(dry_run=False)

    # CRITICAL: Verify that no execute was called (no table created, no migration SQL executed)
    conn.execute.assert_not_called()


# ==============================================================================
# 3. baseline rejects incompatible schema
# ==============================================================================

def _build_baseline_mocks():
    """Builds minimal mock dataset representing a healthy 001 schema."""
    ext_rows = [{"extname": ext} for ext in BASELINE_001_REQUIRED_EXTENSIONS]
    table_rows = [{"table_name": tbl} for tbl in BASELINE_001_REQUIRED_TABLES]
    col_rows = [
        {"table_name": tbl, "column_name": col}
        for tbl, columns in BASELINE_001_CHARACTERISTIC_COLUMNS.items()
        for col in columns
    ]
    src_tbl, src_col, tgt_tbl, tgt_col = BASELINE_001_KEY_FOREIGN_KEY
    fk_rows = [
        {
            "source_table": src_tbl,
            "source_column": src_col,
            "target_table": tgt_tbl,
            "target_column": tgt_col,
        }
    ]
    return [ext_rows, table_rows, col_rows, fk_rows]


@pytest.mark.anyio
async def test_baseline_rejects_schema_missing_table():
    """baseline must reject when a required table is absent."""
    runner = MigrationRunner(db_url="postgresql://mock:mock@localhost:5432/test")
    conn, _ = create_mock_connection()

    mocks = _build_baseline_mocks()
    # Remove 'document_versions' from tables
    mocks[1] = [r for r in mocks[1] if r["table_name"] != "document_versions"]

    conn.fetch.side_effect = mocks
    is_valid, missing = await runner.verify_baseline_001(conn)

    assert is_valid is False
    assert any("document_versions" in m for m in missing)


@pytest.mark.anyio
async def test_baseline_rejects_schema_missing_characteristic_column():
    """baseline must reject when a characteristic column is absent."""
    runner = MigrationRunner(db_url="postgresql://mock:mock@localhost:5432/test")
    conn, _ = create_mock_connection()

    mocks = _build_baseline_mocks()
    # Remove documents.current_version_number
    mocks[2] = [
        r for r in mocks[2]
        if not (r["table_name"] == "documents" and r["column_name"] == "current_version_number")
    ]

    conn.fetch.side_effect = mocks
    is_valid, missing = await runner.verify_baseline_001(conn)

    assert is_valid is False
    assert any("documents.current_version_number" in m for m in missing)


@pytest.mark.anyio
async def test_baseline_accepts_valid_schema():
    """verify_baseline_001 must pass with a healthy 001 schema."""
    runner = MigrationRunner(db_url="postgresql://mock:mock@localhost:5432/test")
    conn, _ = create_mock_connection()

    conn.fetch.side_effect = _build_baseline_mocks()
    is_valid, missing = await runner.verify_baseline_001(conn)

    assert is_valid is True
    assert missing == []


# ==============================================================================
# 4. Transactional atomicity
# ==============================================================================

@pytest.mark.anyio
async def test_transactional_commit_order(tmp_path):
    """Migration SQL and schema_migrations insert must both occur inside a single transaction."""
    file2 = tmp_path / "002_add.sql"
    file2.write_text("CREATE TABLE test2 (id INT);", encoding="utf-8")

    runner = MigrationRunner(db_url="postgresql://mock:mock@localhost:5432/test", migrations_dir=tmp_path)
    conn, tx = create_mock_connection()
    conn.fetch.return_value = []
    runner._get_connection = AsyncMock(return_value=conn)

    results = await runner.apply_pending()

    assert len(results) == 1
    assert results[0]["status"] == "APPLIED"

    # Transaction entered and exited cleanly
    assert tx.entered is True
    assert tx.exited is True
    assert tx.exc_type is None

    # Order: migration SQL first, then schema_migrations insert
    executed_sqls = [call.args[0] for call in conn.execute.call_args_list]
    assert len(executed_sqls) == 2
    assert "CREATE TABLE test2 (id INT);" in executed_sqls[0]
    assert "INSERT INTO public.schema_migrations" in executed_sqls[1]


@pytest.mark.anyio
async def test_transactional_rollback_on_migration_sql_failure(tmp_path):
    """A SQL error must cause the transaction to exit with an exception (triggering rollback)."""
    file2 = tmp_path / "002_add.sql"
    file2.write_text("SYNTAX ERROR IN SQL;", encoding="utf-8")

    runner = MigrationRunner(db_url="postgresql://mock:mock@localhost:5432/test", migrations_dir=tmp_path)
    conn, tx = create_mock_connection()
    conn.fetch.return_value = []
    conn.execute.side_effect = asyncpg.PostgresSyntaxError("syntax error at or near 'SYNTAX'")
    runner._get_connection = AsyncMock(return_value=conn)

    with pytest.raises(asyncpg.PostgresSyntaxError):
        await runner.apply_pending()

    # Transaction entered and exited with an exception (triggering rollback)
    assert tx.entered is True
    assert tx.exited is True
    assert issubclass(tx.exc_type, asyncpg.PostgresSyntaxError)

    # Insert into schema_migrations was never called
    assert conn.execute.call_count == 1


# ==============================================================================
# 5. Integrity: checksum and missing file detection
# ==============================================================================

@pytest.mark.anyio
async def test_detection_of_checksum_mismatch(tmp_path):
    """Status reports CHECKSUM_MISMATCH; apply aborts when a local file was modified after execution."""
    file1 = tmp_path / "001_init.sql"
    file1.write_text("CREATE TABLE test (id INT);", encoding="utf-8")

    runner = MigrationRunner(db_url="postgresql://mock:mock@localhost:5432/test", migrations_dir=tmp_path)

    tampered_checksum = "tampered_checksum_differs_from_actual_sha256"

    # 1. Status detects CHECKSUM_MISMATCH
    conn_status, _ = create_mock_connection()
    conn_status.fetch.return_value = [
        {"version": "001", "filename": "001_init.sql", "applied_at": "2026-10-25", "checksum": tampered_checksum}
    ]
    runner._get_connection = AsyncMock(return_value=conn_status)

    status_res = await runner.get_status()
    assert status_res["history_initialized"] is True
    assert status_res["items"][0]["status"] == "CHECKSUM_MISMATCH"

    # 2. Apply aborts on CHECKSUM_MISMATCH
    conn_apply, _ = create_mock_connection()
    conn_apply.fetch.return_value = [
        {"version": "001", "filename": "001_init.sql", "applied_at": "2026-10-25", "checksum": tampered_checksum}
    ]
    runner._get_connection = AsyncMock(return_value=conn_apply)

    with pytest.raises(RuntimeError, match="Checksum mismatch for migration 001"):
        await runner.apply_pending()


@pytest.mark.anyio
async def test_detection_of_missing_local_file(tmp_path):
    """Status reports MISSING_LOCAL_FILE; apply aborts when an applied migration has no local file."""
    runner = MigrationRunner(db_url="postgresql://mock:mock@localhost:5432/test", migrations_dir=tmp_path)

    db_record = [
        {"version": "999", "filename": "999_deleted_local.sql", "applied_at": "2026-10-25", "checksum": "some_checksum"}
    ]

    # 1. Status shows MISSING_LOCAL_FILE
    conn_status, _ = create_mock_connection()
    conn_status.fetch.return_value = db_record
    runner._get_connection = AsyncMock(return_value=conn_status)

    status_res = await runner.get_status()
    missing_entry = next(item for item in status_res["items"] if item["version"] == "999")
    assert missing_entry["status"] == "MISSING_LOCAL_FILE"

    # 2. Apply aborts when applied file is missing locally
    conn_apply, _ = create_mock_connection()
    conn_apply.fetch.return_value = db_record
    runner._get_connection = AsyncMock(return_value=conn_apply)

    with pytest.raises(RuntimeError, match="recorded as applied in schema_migrations, but its local file is missing"):
        await runner.apply_pending()
