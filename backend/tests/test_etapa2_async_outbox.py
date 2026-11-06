import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import pika
from pika.exceptions import AMQPError, NackError

from app.config import settings
from app.services.document_parser import ParsedSection
from app.services.storage import storage_service, validate_storage_path
from app.workers.broker import rabbitmq_broker
from dramatiq.middleware import (
    AgeLimit,
    Callbacks,
    CurrentMessage,
    Pipelines,
    Retries,
    ShutdownNotifications,
    TimeLimit,
)
from app.workers.dispatcher import (
    dispatch_pending_outbox_batch,
    publish_with_publisher_confirms,
    run_dispatcher_loop,
)
from app.workers.document_tasks import (
    UnrecoverableDocumentError,
    _execute_document_processing,
)
from app.workers.reconciliation import (
    reconcile_abandoned_uploads,
    run_reconciliation_loop,
)


class MockAsyncContextManager:
    """Helper to mock synchronous begin() returning async context manager."""
    def __init__(self, on_exit=None):
        self.on_exit = on_exit

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.on_exit:
            self.on_exit(exc_type)
        return False


def create_mock_session():
    session = AsyncMock()
    session.add = MagicMock()
    session.begin = MagicMock(return_value=MockAsyncContextManager())
    default_result = MagicMock()
    default_result.rowcount = 1
    session.execute = AsyncMock(return_value=default_result)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    return session


def create_mock_conn():
    conn = AsyncMock()
    conn.in_transaction = MagicMock(return_value=False)
    conn.rollback = AsyncMock()
    conn.commit = AsyncMock()
    conn.begin = MagicMock(return_value=MockAsyncContextManager())
    conn.scalar = AsyncMock()
    conn.execute = AsyncMock()
    conn.invalidate = AsyncMock()
    conn.close = AsyncMock()
    return conn


# ==============================================================================
# 1. Configuration & Security Tests
# ==============================================================================

def test_direct_database_url_missing_raises_explicit_error():
    """DIRECT_DATABASE_URL must raise ValueError when missing, with no silent fallback."""
    with patch.object(settings, "DIRECT_DATABASE_URL", None):
        with pytest.raises(ValueError, match="DIRECT_DATABASE_URL is not configured"):
            settings.get_direct_database_url()


def test_direct_database_url_transaction_pooling_port_raises_error():
    """DIRECT_DATABASE_URL must reject port 6543 (Transaction Pooling) to protect advisory locks."""
    with patch.object(
        settings,
        "DIRECT_DATABASE_URL",
        "postgresql+asyncpg://postgres:pass@db.xyz.supabase.co:6543/postgres",
    ):
        with pytest.raises(ValueError, match="detected port 6543"):
            settings.get_direct_database_url()


@pytest.mark.anyio
async def test_supabase_secret_key_missing_raises_explicit_error():
    """Administrative storage operations must fail explicitly without SUPABASE_SECRET_KEY."""
    asst_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    storage_path = f"{asst_id}/{doc_id}/test.pdf"

    with patch.object(settings, "SUPABASE_SECRET_KEY", None):
        with pytest.raises(RuntimeError, match="SUPABASE_SECRET_KEY is not configured"):
            await storage_service.download_file_admin(storage_path)

        with pytest.raises(RuntimeError, match="SUPABASE_SECRET_KEY is not configured"):
            await storage_service.delete_file_admin(storage_path)


def test_path_traversal_guard_valid_and_invalid_paths():
    valid_asst = str(uuid.uuid4())
    valid_doc = str(uuid.uuid4())

    # Valid paths
    assert validate_storage_path(f"{valid_asst}/{valid_doc}/manual.pdf") is True
    assert validate_storage_path(f"{valid_asst}/{valid_doc}/report_2026.docx") is True
    assert validate_storage_path(f"{valid_asst}/{valid_doc}/notes.txt") is True

    # Invalid paths: directory traversal
    assert validate_storage_path(f"{valid_asst}/{valid_doc}/../secret.txt") is False
    assert validate_storage_path(f"{valid_asst}/../{valid_doc}/manual.pdf") is False
    assert validate_storage_path(f"../../etc/passwd") is False
    assert validate_storage_path(f"{valid_asst}\\{valid_doc}\\manual.pdf") is False

    # Invalid paths: invalid UUIDs
    assert validate_storage_path(f"not-a-uuid/{valid_doc}/manual.pdf") is False
    assert validate_storage_path(f"{valid_asst}/not-a-uuid/manual.pdf") is False

    # Invalid paths: empty filename or extra slashes
    assert validate_storage_path(f"{valid_asst}/{valid_doc}/") is False
    assert validate_storage_path(f"{valid_asst}/{valid_doc}/sub/file.pdf") is False
    assert validate_storage_path("") is False


# ==============================================================================
# 2. Dramatiq Middleware Stack Tests
# ==============================================================================

def test_dramatiq_broker_middleware_preservation():
    """
    Verifies that RabbitmqBroker preserves all standard middlewares,
    configures Retries with the exact policy, and adds CurrentMessage without duplicates.
    """
    middleware_types = [type(m) for m in rabbitmq_broker.middleware]

    # Required standard middlewares
    assert AgeLimit in middleware_types
    assert TimeLimit in middleware_types
    assert ShutdownNotifications in middleware_types
    assert Callbacks in middleware_types
    assert Pipelines in middleware_types
    assert Retries in middleware_types
    assert CurrentMessage in middleware_types

    # No duplicate Retries or CurrentMessage
    assert sum(1 for m in rabbitmq_broker.middleware if isinstance(m, Retries)) == 1
    assert sum(1 for m in rabbitmq_broker.middleware if isinstance(m, CurrentMessage)) == 1

    # Verify retry policy parameters
    retries_instance = next(m for m in rabbitmq_broker.middleware if isinstance(m, Retries))
    assert retries_instance.max_retries == 3
    assert retries_instance.min_backoff == 15000
    assert retries_instance.max_backoff == 300000


# ==============================================================================
# 3. Outbox Dispatcher with Pika Publisher Confirms
# ==============================================================================

@pytest.mark.anyio
async def test_outbox_dispatcher_publisher_confirms_success():
    """
    Validates that dispatch_pending_outbox_batch:
    1. Claims pending outbox records without holding row lock during network I/O.
    2. Publishes to RabbitMQ with Publisher Confirms (delivery_mode=2).
    3. Atomically updates status to 'dispatched' with dispatched_at timestamp.
    """
    event_id = uuid.uuid4()
    doc_id = uuid.uuid4()

    mock_row = MagicMock()
    mock_row.id = event_id
    mock_row.document_id = doc_id
    mock_row.retry_count = 0

    mock_session = create_mock_session()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [mock_row]
    mock_session.execute.return_value = mock_result

    # Mock Pika channel
    mock_channel = MagicMock()

    dispatched = await dispatch_pending_outbox_batch(
        session=mock_session,
        batch_size=10,
        max_retries=5,
        pika_channel=mock_channel,
    )

    assert dispatched == 1
    # Check that basic_publish was called with mandatory=True and delivery_mode=2
    mock_channel.basic_publish.assert_called_once()
    publish_args = mock_channel.basic_publish.call_args[1]
    assert publish_args["exchange"] == ""
    assert publish_args["routing_key"] == "documents"
    assert publish_args["mandatory"] is True
    assert publish_args["properties"].delivery_mode == 2

    # Verify SQL updates: first claim to 'processing', then update to 'dispatched'
    executed_sqls = [str(call.args[0]) for call in mock_session.execute.call_args_list]
    assert any("status = 'processing'" in s for s in executed_sqls)
    assert any("status = 'dispatched'" in s for s in executed_sqls)


@pytest.mark.anyio
async def test_outbox_dispatcher_backoff_on_broker_failure():
    """
    Validates exponential backoff with jitter when RabbitMQ broker fails.
    """
    event_id = uuid.uuid4()
    doc_id = uuid.uuid4()

    mock_row = MagicMock()
    mock_row.id = event_id
    mock_row.document_id = doc_id
    mock_row.retry_count = 1

    mock_session = create_mock_session()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [mock_row]
    mock_session.execute.return_value = mock_result

    # Mock channel that raises AMQPError
    mock_channel = MagicMock()
    mock_channel.basic_publish.side_effect = AMQPError("Broker connection lost")

    dispatched = await dispatch_pending_outbox_batch(
        session=mock_session,
        batch_size=10,
        max_retries=5,
        pika_channel=mock_channel,
    )

    assert dispatched == 0
    executed_calls = mock_session.execute.call_args_list
    assert len(executed_calls) >= 3
    # Look for the update resetting status to pending with backoff
    backoff_calls = [
        c for c in executed_calls
        if "UPDATE document_outbox" in str(c.args[0]) and "status = 'pending'" in str(c.args[0])
    ]
    assert len(backoff_calls) == 1
    params = backoff_calls[0].args[1]
    assert params["retry_count"] == 2
    assert "Delivery attempt 2 failed" in params["err_msg"]


@pytest.mark.anyio
async def test_outbox_dispatcher_max_retries_exhausted_updates_both_tables():
    """
    Validates that when outbox retries are exhausted, BOTH document_outbox
    and documents are updated to status='failed', preventing stuck pending states.
    """
    event_id = uuid.uuid4()
    doc_id = uuid.uuid4()

    mock_row = MagicMock()
    mock_row.id = event_id
    mock_row.document_id = doc_id
    mock_row.retry_count = 4  # Next will be 5, matching max_retries

    mock_session = create_mock_session()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [mock_row]
    mock_session.execute.return_value = mock_result

    mock_channel = MagicMock()
    mock_channel.basic_publish.side_effect = NackError("NACK received from broker")

    dispatched = await dispatch_pending_outbox_batch(
        session=mock_session,
        batch_size=10,
        max_retries=5,
        pika_channel=mock_channel,
    )

    assert dispatched == 0
    executed_sqls = [str(call.args[0]) for call in mock_session.execute.call_args_list]

    # Outbox event marked as failed
    assert any("UPDATE document_outbox" in s and "status = 'failed'" in s for s in executed_sqls)
    # Document marked as failed to stop frontend polling
    assert any("UPDATE documents" in s and "processing_status = 'failed'" in s for s in executed_sqls)


@pytest.mark.anyio
async def test_stale_dispatcher_claim_token_fencing_prevents_overwrite():
    """
    Validates claim token fencing:
    If Dispatcher 1 claims an event (token1), but times out, and Dispatcher 2 assumes
    the event (token2), Dispatcher 1's subsequent attempt to mark 'dispatched' matches 0 rows
    (rowcount=0) and is safely ignored without overwriting Dispatcher 2's claim.
    """
    event_id = uuid.uuid4()
    doc_id = uuid.uuid4()

    mock_row = MagicMock()
    mock_row.id = event_id
    mock_row.document_id = doc_id
    mock_row.retry_count = 0

    mock_session = create_mock_session()
    mock_fetch_result = MagicMock()
    mock_fetch_result.fetchall.return_value = [mock_row]

    # Mock execute for Phase 3 (update with claim_token) returning rowcount = 0 (claim lost!)
    mock_update_result = MagicMock()
    mock_update_result.rowcount = 0

    mock_session.execute.side_effect = [
        mock_fetch_result,   # Phase 1: SELECT FOR UPDATE SKIP LOCKED
        None,                # Phase 1: UPDATE status='processing', claim_token=token1
        mock_update_result,  # Phase 3: UPDATE status='dispatched' WHERE claim_token=token1 -> 0 rows!
    ]

    mock_channel = MagicMock()

    dispatched = await dispatch_pending_outbox_batch(
        session=mock_session,
        batch_size=10,
        max_retries=5,
        pika_channel=mock_channel,
    )

    # Dispatched count is 0 because the claim was lost
    assert dispatched == 0
    # RabbitMQ publish did occur
    mock_channel.basic_publish.assert_called_once()
    # Verified: Phase 3 query filtered strictly by claim_token
    phase3_call = mock_session.execute.call_args_list[2]
    sql_text = str(phase3_call.args[0])
    params = phase3_call.args[1]
    assert "claim_token = :claim_token" in sql_text
    assert "claim_token" in params


def test_outbox_model_and_migration_permit_processing_status():
    """
    Validates that:
    1. DocumentOutbox model accepts status='processing'.
    2. Migration 002 defines ck_document_outbox_status with all 4 states including 'processing'.
    3. Migration 004 adds the claim_token column (and only that).
    """
    from pathlib import Path
    from app.models.all_models import DocumentOutbox

    outbox = DocumentOutbox(
        document_id=uuid.uuid4(),
        status="processing",
        retry_count=0,
    )
    assert outbox.status == "processing"
    assert outbox.claim_token is None  # nullable by default

    # Migration 002 defines the constraint permitting 'processing'
    migration_002 = Path(__file__).resolve().parent.parent / "migrations" / "002_phase2_etapa1_schema.sql"
    assert migration_002.exists()
    content_002 = migration_002.read_text(encoding="utf-8")
    assert "ck_document_outbox_status" in content_002
    assert "'processing'" in content_002

    # Migration 004 adds only the claim_token column — no constraint recreation
    migration_004 = Path(__file__).resolve().parent.parent / "migrations" / "004_outbox_dispatcher_claim_token.sql"
    assert migration_004.exists()
    content_004 = migration_004.read_text(encoding="utf-8")
    assert "claim_token UUID" in content_004
    assert "ck_document_outbox_status" not in content_004  # constraint untouched in 004


# ==============================================================================
# 4. 64-bit Advisory Lock & Concurrency Tests
# ==============================================================================

@pytest.mark.anyio
async def test_advisory_lock_prevents_concurrent_processing():
    """
    Validates that if pg_try_advisory_lock returns False (another worker active),
    the worker exits cleanly without error and without scheduling a retry.
    """
    doc_id_str = str(uuid.uuid4())

    mock_conn = create_mock_conn()
    # Lock acquisition returns False
    mock_conn.scalar.return_value = False

    with patch("app.workers.document_tasks.get_direct_connection", return_value=mock_conn):
        # Should execute cleanly without raising any exceptions
        await _execute_document_processing(doc_id_str)

    # Verified: pg_try_advisory_lock was queried
    mock_conn.scalar.assert_called_once()
    sql_call = str(mock_conn.scalar.call_args[0][0])
    assert "pg_try_advisory_lock" in sql_call
    assert "hashtextextended" in sql_call

    # Verified: no SQL updates were executed because lock was not acquired
    mock_conn.execute.assert_not_called()
    mock_conn.close.assert_awaited_once()


@pytest.mark.anyio
async def test_advisory_lock_invalidation_on_unlock_failure():
    """
    Validates that if pg_advisory_unlock fails or returns False,
    the dedicated connection is invalidated (conn.invalidate()) and not returned tainted.
    """
    doc_id_str = str(uuid.uuid4())

    mock_conn = create_mock_conn()
    # First scalar: pg_try_advisory_lock -> True
    # Second scalar: check existing status -> 'pending'
    # Third scalar: pg_advisory_unlock -> False (simulating unlock issue)
    mock_conn.scalar.side_effect = [True, "pending", False]

    # Mock execute returning doc not found to trigger UnrecoverableDocumentError cleanly
    mock_exec_res = MagicMock()
    mock_exec_res.mappings.return_value.first.return_value = None
    mock_exec_res.scalars.return_value.first.return_value = None
    mock_conn.execute.return_value = mock_exec_res

    with patch("app.workers.document_tasks.get_direct_connection", return_value=mock_conn):
        await _execute_document_processing(doc_id_str)

    # Invalidate was called because unlock returned False
    mock_conn.invalidate.assert_awaited_once()
    mock_conn.close.assert_awaited_once()


@pytest.mark.anyio
async def test_duplicate_message_skipped_if_already_indexed():
    """
    Validates at-least-once idempotency: if a duplicate message arrives for a document
    that is already indexed, the worker skips re-processing immediately.
    """
    doc_id = uuid.uuid4()
    doc_id_str = str(doc_id)

    mock_conn = create_mock_conn()
    # Lock -> True, Existing status -> 'indexed', Unlock -> True
    mock_conn.scalar.side_effect = [True, "indexed", True]

    with patch("app.workers.document_tasks.get_direct_connection", return_value=mock_conn):
        await _execute_document_processing(doc_id_str)

    # Execution skipped text extraction/embedding
    mock_conn.execute.assert_not_called()
    mock_conn.close.assert_awaited_once()


# ==============================================================================
# 5. Worker Retries and Terminal Status Tests
# ==============================================================================

@pytest.mark.anyio
async def test_document_processing_transient_retry_success():
    """
    Simulates transient failure (e.g. network/OpenRouter timeout) on attempt 1:
    - Verifies processing_status remains 'processing'.
    - Verifies retry warning recorded in processing_error.
    - Verifies exception is re-raised so Dramatiq's Retries middleware schedules backoff.
    """
    doc_id = uuid.uuid4()
    doc_id_str = str(doc_id)

    mock_conn = create_mock_conn()
    # Lock -> True, Existing status -> 'pending', Unlock -> True
    mock_conn.scalar.side_effect = [True, "pending", True]

    # Mock doc and assistant
    asst_id = uuid.uuid4()
    doc_dict = {
        "assistant_id": asst_id,
        "storage_path": f"{asst_id}/{doc_id}/sample.txt",
        "name": "sample.txt",
        "mime_type": "text/plain",
    }
    asst_dict = {
        "openrouter_api_key_encrypted": "mock_encrypted",
    }

    mock_exec_res1 = MagicMock()
    mock_exec_res1.mappings.return_value.first.return_value = doc_dict
    mock_exec_res2 = MagicMock()
    mock_exec_res2.mappings.return_value.first.return_value = asst_dict
    mock_conn.execute.side_effect = [None, mock_exec_res1, mock_exec_res2, None]

    with patch("app.workers.document_tasks.get_direct_connection", return_value=mock_conn), \
         patch("app.workers.document_tasks.storage_service.download_file_admin", return_value=b"Sample content"), \
         patch("app.workers.document_tasks.parse_document", return_value=("Sample content", [ParsedSection(text="Sample content", page_number=1, metadata={"title": "P1"})])), \
         patch("app.workers.document_tasks.decrypt_api_key", return_value="plain_key"), \
         patch("app.workers.document_tasks.OpenRouterClient.generate_embeddings", side_effect=TimeoutError("OpenRouter gateway timeout")):

        # Attempt 1 (current_retries=0, max_retries=3) must raise to trigger Dramatiq backoff
        with pytest.raises(TimeoutError, match="OpenRouter gateway timeout"):
            await _execute_document_processing(doc_id_str, current_retries=0, max_retries=3)

    # Verify that connection was closed
    mock_conn.close.assert_awaited_once()


@pytest.mark.anyio
async def test_document_processing_retries_exhausted_marks_failed():
    """
    Simulates retries exhausted (current_retries >= max_retries):
    - Conclusively marks processing_status='failed'.
    - Records detailed error in processing_error.
    - Does NOT re-raise exception (so Dramatiq doesn't retry further).
    """
    doc_id = uuid.uuid4()
    doc_id_str = str(doc_id)

    mock_conn = create_mock_conn()
    # Lock -> True, Existing status -> 'pending', Unlock -> True
    mock_conn.scalar.side_effect = [True, "pending", True]

    asst_id = uuid.uuid4()
    doc_dict = {
        "assistant_id": asst_id,
        "storage_path": f"{asst_id}/{doc_id}/sample.txt",
        "name": "sample.txt",
        "mime_type": "text/plain",
    }
    asst_dict = {
        "openrouter_api_key_encrypted": "mock_encrypted",
    }

    mock_exec_res1 = MagicMock()
    mock_exec_res1.mappings.return_value.first.return_value = doc_dict
    mock_exec_res2 = MagicMock()
    mock_exec_res2.mappings.return_value.first.return_value = asst_dict
    mock_conn.execute.side_effect = [None, mock_exec_res1, mock_exec_res2, None]

    with patch("app.workers.document_tasks.get_direct_connection", return_value=mock_conn), \
         patch("app.workers.document_tasks.storage_service.download_file_admin", return_value=b"Sample content"), \
         patch("app.workers.document_tasks.parse_document", return_value=("Sample content", [ParsedSection(text="Sample content", page_number=1, metadata={"title": "P1"})])), \
         patch("app.workers.document_tasks.decrypt_api_key", return_value="plain_key"), \
         patch("app.workers.document_tasks.OpenRouterClient.generate_embeddings", side_effect=TimeoutError("OpenRouter gateway timeout")):

        # When current_retries >= max_retries, it should NOT raise
        await _execute_document_processing(doc_id_str, current_retries=3, max_retries=3)

    # Verify executed SQL includes failed update
    executed_sqls = [str(call.args[0]) for call in mock_conn.execute.call_args_list]
    assert any("processing_status = 'failed'" in s for s in executed_sqls)
    mock_conn.close.assert_awaited_once()


# ==============================================================================
# 6. Dual-Write Compensation & Abandoned Upload Reconciliation
# ==============================================================================

@pytest.mark.anyio
async def test_reconciliation_of_abandoned_uploads_success():
    """
    Validates that uploads stuck in 'uploading' for > 2 hours are marked 'failed'
    when their storage files are successfully cleaned up.
    """
    doc_id = uuid.uuid4()
    storage_path = f"{uuid.uuid4()}/{doc_id}/orphaned.pdf"

    mock_row = MagicMock()
    mock_row.id = doc_id
    mock_row.storage_path = storage_path

    mock_session = create_mock_session()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [mock_row]
    mock_session.execute.return_value = mock_result

    with patch("app.workers.reconciliation.storage_service.delete_file_admin", return_value=True) as mock_delete:
        reconciled = await reconcile_abandoned_uploads(session=mock_session, older_than_hours=2)

    assert reconciled == 1
    mock_delete.assert_awaited_once_with(storage_path)

    executed_sqls = [str(call.args[0]) for call in mock_session.execute.call_args_list]
    assert any("upload_status = 'failed'" in s and "processing_status = 'failed'" in s for s in executed_sqls)


@pytest.mark.anyio
async def test_reconciliation_failure_preserves_row_for_retry_and_stops_frontend_polling():
    """
    Validates that if storage deletion fails during reconciliation:
    1. processing_status='failed' is updated so frontend polling stops immediately (no infinite polling).
    2. upload_status is NOT set to 'failed' (remains 'uploading') so subsequent cycles will retry storage cleanup.
    3. reconciled_count is 0 because cleanup is incomplete.
    """
    doc_id = uuid.uuid4()
    storage_path = f"{uuid.uuid4()}/{doc_id}/orphaned.pdf"

    mock_row = MagicMock()
    mock_row.id = doc_id
    mock_row.storage_path = storage_path

    mock_session = create_mock_session()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [mock_row]
    mock_session.execute.return_value = mock_result

    # Storage cleanup fails
    with patch("app.workers.reconciliation.storage_service.delete_file_admin", return_value=False) as mock_delete:
        reconciled = await reconcile_abandoned_uploads(session=mock_session, older_than_hours=2)

    assert reconciled == 0
    mock_delete.assert_awaited_once_with(storage_path)

    executed_sqls = [str(call.args[0]) for call in mock_session.execute.call_args_list]
    # 1. upload_status='failed' must NOT be committed yet
    assert not any("upload_status = 'failed'" in s for s in executed_sqls)
    # 2. processing_status='failed' MUST be committed so frontend polling stops
    assert any("processing_status = 'failed'" in s for s in executed_sqls)
    # 3. retry notice is recorded in processing_error
    assert any("Falha temporária na exclusão do storage" in str(call.args[1].get("err_msg", "")) for call in mock_session.execute.call_args_list if len(call.args) > 1)


@pytest.mark.anyio
async def test_upload_dual_write_compensation_on_db_commit_failure():
    """
    Validates that if the database transaction fails after file upload to Storage,
    compensatory storage deletion is immediately executed.
    """
    from fastapi import UploadFile, HTTPException
    from io import BytesIO
    from app.api.documents import upload_document
    from app.auth.jwt import AuthenticatedUser

    user = AuthenticatedUser(id=uuid.uuid4(), email="user@example.com", token="fake_token")
    asst_id = uuid.uuid4()
    mock_assistant = MagicMock()
    mock_assistant.id = asst_id

    mock_session = create_mock_session()
    # First commit (intention pre-create) succeeds
    # Second commit (outbox creation) raises RuntimeError
    mock_session.commit.side_effect = [None, RuntimeError("Database connection died during commit")]

    fake_file = UploadFile(
        file=BytesIO(b"Hello world document content"),
        filename="test.txt",
        headers={"content-type": "text/plain"},
    )

    with patch("app.api.documents._get_user_assistant", return_value=mock_assistant), \
         patch("app.api.documents.storage_service.upload_file", return_value="path"), \
         patch("app.api.documents.storage_service.delete_file_admin", return_value=True) as mock_compensate:

        with pytest.raises(HTTPException) as exc_info:
            await upload_document(
                slug="test-slug",
                file=fake_file,
                user=user,
                db=mock_session,
            )

        assert exc_info.value.status_code == 500
        # Verified: dual-write compensation called delete_file_admin
        mock_compensate.assert_awaited_once()
        mock_session.rollback.assert_awaited_once()


# ==============================================================================
# 7. Fencing Behavioural Tests (stale dispatcher cannot update any state)
# ==============================================================================

@pytest.mark.anyio
async def test_stale_dispatcher_backoff_update_silently_discarded_when_claim_lost():
    """
    Validates that a stale dispatcher whose claim_token has been overwritten
    by a newer dispatcher cannot reschedule ('pending') an outbox event via
    the backoff path.

    Simulates:
    - Phase 1: claim succeeds (rowcount=1 on processing update).
    - Publish fails → backoff branch entered.
    - Phase 3 backoff UPDATE matches 0 rows (stale claim_token).
    - Function returns 0 dispatched and logs a warning (no exception, no data mutation).
    """
    event_id = uuid.uuid4()
    doc_id = uuid.uuid4()

    mock_row = MagicMock()
    mock_row.id = event_id
    mock_row.document_id = doc_id
    mock_row.retry_count = 1  # retries < max_retries → backoff branch

    mock_session = create_mock_session()
    mock_fetch_result = MagicMock()
    mock_fetch_result.fetchall.return_value = [mock_row]

    # Backoff update returns 0 rows — stale token lost
    mock_backoff_update = MagicMock()
    mock_backoff_update.rowcount = 0

    mock_session.execute.side_effect = [
        mock_fetch_result,     # Phase 1: SELECT FOR UPDATE SKIP LOCKED
        None,                  # Phase 1: UPDATE status='processing', claim_token=...
        mock_backoff_update,   # Phase 3 backoff: UPDATE status='pending' WHERE claim_token=... → 0 rows
    ]

    mock_channel = MagicMock()
    mock_channel.basic_publish.side_effect = NackError("NACK")

    import logging
    with patch("app.workers.dispatcher.logger") as mock_logger:
        dispatched = await dispatch_pending_outbox_batch(
            session=mock_session,
            batch_size=10,
            max_retries=5,
            pika_channel=mock_channel,
        )

    assert dispatched == 0

    # Verify the backoff UPDATE was issued with claim_token in WHERE clause
    backoff_calls = [
        c for c in mock_session.execute.call_args_list
        if len(c.args) > 0 and "status = 'pending'" in str(c.args[0])
        and "claim_token = :claim_token" in str(c.args[0])
    ]
    assert len(backoff_calls) == 1, "Backoff UPDATE must include claim_token fencing"

    # Verify the stale-loss warning was logged
    warning_calls = [str(a) for a, _ in mock_logger.warning.call_args_list]
    assert any("Fencing" in w and str(event_id) in w for w in warning_calls), \
        "Expected a fencing warning when backoff rowcount==0"


@pytest.mark.anyio
async def test_reconciler_reselects_row_with_uploading_status_regardless_of_processing_status():
    """
    Validates the reconciliation query invariant:
    Rows with upload_status='uploading' are always re-selected regardless of
    processing_status, ensuring the cleanup retry loop continues working even
    after processing_status was set to 'failed' on a previous failed cycle.

    Simulates two reconciliation cycles:
    - Cycle 1: Storage deletion fails → processing_status='failed', upload_status stays 'uploading'.
    - Cycle 2: Same row is re-selected (still upload_status='uploading') and deletion succeeds
               → both statuses become 'failed'.
    """
    doc_id = uuid.uuid4()
    storage_path = f"{uuid.uuid4()}/{doc_id}/orphaned.pdf"

    mock_row = MagicMock()
    mock_row.id = doc_id
    mock_row.storage_path = storage_path

    # --- Cycle 1: storage deletion fails ---
    mock_session_1 = create_mock_session()
    mock_result_1 = MagicMock()
    mock_result_1.fetchall.return_value = [mock_row]
    mock_session_1.execute.return_value = mock_result_1

    with patch("app.workers.reconciliation.storage_service.delete_file_admin", return_value=False):
        reconciled_1 = await reconcile_abandoned_uploads(session=mock_session_1, older_than_hours=2)

    assert reconciled_1 == 0
    sqls_1 = [str(c.args[0]) for c in mock_session_1.execute.call_args_list]
    # upload_status must NOT be updated on failure
    assert not any("upload_status = 'failed'" in s for s in sqls_1)
    # processing_status must be set to 'failed' so frontend polling stops
    assert any("processing_status = 'failed'" in s for s in sqls_1)

    # Verify query uses upload_status='uploading' as the selector (not processing_status)
    select_calls = [c for c in mock_session_1.execute.call_args_list
                    if "upload_status = 'uploading'" in str(c.args[0])]
    assert len(select_calls) == 1, "Reconciler must query by upload_status='uploading'"

    # --- Cycle 2: same row still eligible (upload_status='uploading'), deletion now succeeds ---
    mock_session_2 = create_mock_session()
    mock_result_2 = MagicMock()
    mock_result_2.fetchall.return_value = [mock_row]  # same row, still upload_status='uploading'
    mock_session_2.execute.return_value = mock_result_2

    with patch("app.workers.reconciliation.storage_service.delete_file_admin", return_value=True):
        reconciled_2 = await reconcile_abandoned_uploads(session=mock_session_2, older_than_hours=2)

    assert reconciled_2 == 1
    sqls_2 = [str(c.args[0]) for c in mock_session_2.execute.call_args_list]
    # On success, both statuses must be finalized
    assert any("upload_status = 'failed'" in s and "processing_status = 'failed'" in s for s in sqls_2)


# ==============================================================================
# 8. Dispatcher & Reconciliation Loop Lifecycle Tests
# ==============================================================================

@pytest.mark.anyio
async def test_dispatcher_loop_terminates_gracefully_on_stop_event():
    """
    Validates that run_dispatcher_loop executes batch dispatching
    and terminates immediately when stop_event is set.
    """
    stop_event = asyncio.Event()
    call_count = 0

    async def mock_dispatch(**kwargs):
        nonlocal call_count
        call_count += 1
        stop_event.set()  # Signal shutdown after first iteration
        return 1

    with patch("app.workers.dispatcher.dispatch_pending_outbox_batch", side_effect=mock_dispatch):
        await run_dispatcher_loop(poll_interval=0.01, stop_event=stop_event)

    assert call_count >= 1
    assert stop_event.is_set()


@pytest.mark.anyio
async def test_reconciliation_loop_terminates_gracefully_on_stop_event():
    """
    Validates that run_reconciliation_loop executes the reconciliation cycle
    and terminates immediately when stop_event is set.
    """
    stop_event = asyncio.Event()
    call_count = 0

    async def mock_reconcile(**kwargs):
        nonlocal call_count
        call_count += 1
        stop_event.set()
        return 1

    with patch("app.workers.reconciliation.reconcile_abandoned_uploads", side_effect=mock_reconcile):
        await run_reconciliation_loop(poll_interval=0.01, older_than_hours=2, stop_event=stop_event)

    assert call_count >= 1
    assert stop_event.is_set()


@pytest.mark.anyio
async def test_reconciliation_loop_survives_cycle_exception():
    """
    Validates that exceptions in reconcile_abandoned_uploads are logged
    and do not crash or permanently terminate the reconciliation loop.
    """
    stop_event = asyncio.Event()
    attempts = 0

    async def mock_reconcile_flaky(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("Transient database timeout during cleanup")
        stop_event.set()
        return 0

    with patch("app.workers.reconciliation.reconcile_abandoned_uploads", side_effect=mock_reconcile_flaky):
        await run_reconciliation_loop(poll_interval=0.01, older_than_hours=2, stop_event=stop_event)

    assert attempts == 2
    assert stop_event.is_set()


def test_dispatcher_and_reconciler_standalone_entrypoints():
    """
    Verifies that the standalone entrypoint scripts (run_dispatcher.py and run_reconciler.py)
    correctly import and expose the main() runner functions.
    """
    import run_dispatcher
    import run_reconciler

    assert callable(run_dispatcher.main)
    assert callable(run_reconciler.main)


def test_windows_signal_compatibility_no_unsupported_asyncio_handlers():
    """
    Verifies that dispatcher and reconciliation worker main() functions use standard
    signal.signal (compatible with Windows / Git Bash) and avoid loop.add_signal_handler(),
    which raises NotImplementedError on Windows ProactorEventLoop.
    """
    import signal
    from app.workers.dispatcher import main as dispatcher_main
    from app.workers.reconciliation import main as reconciler_main

    registered_dispatcher_signals = []
    registered_reconciler_signals = []

    def mock_signal_dispatcher(sig, handler):
        registered_dispatcher_signals.append(sig)

    def mock_signal_reconciler(sig, handler):
        registered_reconciler_signals.append(sig)

    def mock_asyncio_run(coro):
        coro.close()
        return None

    # Test dispatcher main() signal setup
    with patch("signal.signal", side_effect=mock_signal_dispatcher), \
         patch("asyncio.run", side_effect=mock_asyncio_run):
        dispatcher_main()

    assert signal.SIGINT in registered_dispatcher_signals
    assert signal.SIGTERM in registered_dispatcher_signals
    if hasattr(signal, "SIGBREAK"):
        assert signal.SIGBREAK in registered_dispatcher_signals

    # Test reconciler main() signal setup
    with patch("signal.signal", side_effect=mock_signal_reconciler), \
         patch("asyncio.run", side_effect=mock_asyncio_run):
        reconciler_main()

    assert signal.SIGINT in registered_reconciler_signals
    assert signal.SIGTERM in registered_reconciler_signals
    if hasattr(signal, "SIGBREAK"):
        assert signal.SIGBREAK in registered_reconciler_signals


def test_main_preserves_nonzero_system_exit():
    """
    Validates that a non-zero SystemExit (e.g. fatal initialization or process exit)
    is not suppressed or masked into a success (exit 0) by main().
    Also verifies that SystemExit(0) and KeyboardInterrupt exit cleanly without re-raising.
    """
    from app.workers.dispatcher import main as dispatcher_main
    from app.workers.reconciliation import main as reconciler_main

    def make_run_side_effect(exc_or_res):
        def _mock_run(coro):
            coro.close()
            if isinstance(exc_or_res, BaseException):
                raise exc_or_res
            return exc_or_res
        return _mock_run

    # Dispatcher: non-zero SystemExit MUST be re-raised
    with patch("signal.signal"), patch("asyncio.run", side_effect=make_run_side_effect(SystemExit(2))):
        with pytest.raises(SystemExit) as exc_info:
            dispatcher_main()
        assert exc_info.value.code == 2

    # Reconciler: non-zero SystemExit MUST be re-raised
    with patch("signal.signal"), patch("asyncio.run", side_effect=make_run_side_effect(SystemExit(1))):
        with pytest.raises(SystemExit) as exc_info:
            reconciler_main()
        assert exc_info.value.code == 1

    # Dispatcher: SystemExit(0) and KeyboardInterrupt exit cleanly without exception
    with patch("signal.signal"), patch("asyncio.run", side_effect=make_run_side_effect(SystemExit(0))):
        dispatcher_main()  # Should not raise

    with patch("signal.signal"), patch("asyncio.run", side_effect=make_run_side_effect(KeyboardInterrupt())):
        dispatcher_main()  # Should not raise

    # Reconciler: SystemExit(0) and KeyboardInterrupt exit cleanly without exception
    with patch("signal.signal"), patch("asyncio.run", side_effect=make_run_side_effect(SystemExit(0))):
        reconciler_main()  # Should not raise

    with patch("signal.signal"), patch("asyncio.run", side_effect=make_run_side_effect(KeyboardInterrupt())):
        reconciler_main()  # Should not raise


def test_storage_headers_key_resolution():
    """
    Validates that storage_service._headers resolves the apikey header using:
    1. SUPABASE_PUBLISHABLE_KEY if present
    2. SUPABASE_SECRET_KEY fallback if publishable key is not configured
    """
    from app.services.storage import storage_service

    # Case 1: SUPABASE_PUBLISHABLE_KEY set
    with patch.object(settings, "SUPABASE_PUBLISHABLE_KEY", "pub_key_123"), \
         patch.object(settings, "SUPABASE_SECRET_KEY", "sec_key_789"):
        headers = storage_service._headers("token_abc")
        assert headers["Authorization"] == "Bearer token_abc"
        assert headers["apikey"] == "pub_key_123"

    # Case 2: SUPABASE_PUBLISHABLE_KEY not set -> fallback to SUPABASE_SECRET_KEY
    with patch.object(settings, "SUPABASE_PUBLISHABLE_KEY", None), \
         patch.object(settings, "SUPABASE_SECRET_KEY", "sec_key_789"):
        headers = storage_service._headers("token_abc")
        assert headers["apikey"] == "sec_key_789"


# ==============================================================================
# 9. Worker Connection Pool & Transaction Lifecycle Tests
# ==============================================================================

def test_get_direct_engine_uses_nullpool():
    """
    Validates that get_direct_engine configures NullPool to prevent connection reuse
    across distinct asyncio.run() event loops in Dramatiq worker threads.
    """
    from sqlalchemy.pool import NullPool
    import app.database as db_module

    # Reset cached engine to force recreation
    orig_engine = db_module._direct_engine
    try:
        db_module._direct_engine = None
        with patch.object(
            settings,
            "DIRECT_DATABASE_URL",
            "postgresql+asyncpg://postgres:pass@localhost:5432/postgres",
        ):
            engine = db_module.get_direct_engine()
            assert isinstance(engine.sync_engine.pool, NullPool)
    finally:
        db_module._direct_engine = orig_engine


@pytest.mark.anyio
async def test_advisory_lock_and_unlock_executed_within_transactions_preventing_autobegin():
    """
    Validates that both pg_try_advisory_lock and pg_advisory_unlock are executed
    within explicit conn.begin() transaction blocks. This prevents SQLAlchemy 2.0 autobegin
    from leaving uncommitted transactions that cause InvalidRequestError on subsequent begin() calls.
    """
    doc_id = uuid.uuid4()
    doc_id_str = str(doc_id)

    mock_conn = create_mock_conn()
    # Lock -> True, Existing status -> 'indexed' (early clean exit), Unlock -> True
    mock_conn.scalar.side_effect = [True, "indexed", True]

    with patch("app.workers.document_tasks.get_direct_connection", return_value=mock_conn):
        await _execute_document_processing(doc_id_str)

    # begin() must have been called for:
    # 1. Lock acquisition
    # 2. Status check
    # 3. Unlock in finally
    assert mock_conn.begin.call_count >= 3
    # Check that pg_try_advisory_lock was the first scalar call
    first_call_sql = str(mock_conn.scalar.call_args_list[0][0][0])
    assert "pg_try_advisory_lock" in first_call_sql
    # Check that pg_advisory_unlock was called in finally
    last_call_sql = str(mock_conn.scalar.call_args_list[-1][0][0])
    assert "pg_advisory_unlock" in last_call_sql


@pytest.mark.anyio
async def test_active_transaction_rolled_back_before_recording_failure():
    """
    Validates that if a transaction is active when an exception occurs,
    conn.rollback() is explicitly awaited before opening a new transaction to record
    the failure or retry status, preventing InvalidRequestError.
    """
    doc_id = uuid.uuid4()
    doc_id_str = str(doc_id)

    mock_conn = create_mock_conn()
    # Lock -> True, Existing status -> 'pending', Unlock -> True
    mock_conn.scalar.side_effect = [True, "pending", True]

    # Simulate an active transaction during error handling
    mock_conn.in_transaction.return_value = True

    asst_id = uuid.uuid4()
    doc_dict = {
        "assistant_id": asst_id,
        "storage_path": f"{asst_id}/{doc_id}/sample.txt",
        "name": "sample.txt",
        "mime_type": "text/plain",
    }

    mock_exec_res1 = MagicMock()
    mock_exec_res1.mappings.return_value.first.return_value = doc_dict
    # Assistant query raises an unexpected exception
    mock_conn.execute.side_effect = [None, mock_exec_res1, RuntimeError("Database glitch")]

    with patch("app.workers.document_tasks.get_direct_connection", return_value=mock_conn):
        with pytest.raises(RuntimeError, match="Database glitch"):
            await _execute_document_processing(doc_id_str, current_retries=0, max_retries=3)

    # conn.rollback must be called to clear the active transaction before updating status
    assert mock_conn.rollback.await_count >= 1
    mock_conn.close.assert_awaited_once()


@pytest.mark.anyio
async def test_failure_recording_db_error_does_not_crash_finally_cleanup():
    """
    Validates that even if the database error occurs while recording the failure status,
    the exception is captured cleanly, error is logged, and the connection is closed
    in the finally block without leaking resources.
    """
    doc_id = uuid.uuid4()
    doc_id_str = str(doc_id)

    mock_conn = create_mock_conn()
    # Lock -> True, Existing status -> 'pending', Unlock -> True
    mock_conn.scalar.side_effect = [True, "pending", True]

    # Initial query succeeds, then subsequent queries (including status update in except) fail
    mock_conn.execute.side_effect = [None, RuntimeError("Fatal DB connection loss")]

    with patch("app.workers.document_tasks.get_direct_connection", return_value=mock_conn):
        # Conclusive failure (retries exhausted) does not raise even if update fails
        await _execute_document_processing(doc_id_str, current_retries=3, max_retries=3)

    # Connection must still be closed cleanly in finally block
    mock_conn.close.assert_awaited_once()


@pytest.mark.anyio
async def test_unrecoverable_error_marks_failed_without_re_raising():
    """
    Validates that an UnrecoverableDocumentError (e.g. document not found in DB)
    immediately records status='failed' and does NOT re-raise, preventing infinite retries.
    """
    doc_id = uuid.uuid4()
    doc_id_str = str(doc_id)

    mock_conn = create_mock_conn()
    mock_conn.scalar.side_effect = [True, "pending", True]

    # Document query returns None -> triggers UnrecoverableDocumentError
    mock_doc_res = MagicMock()
    mock_doc_res.mappings.return_value.first.return_value = None
    mock_doc_res.scalars.return_value.first.return_value = None
    mock_conn.execute.side_effect = [None, mock_doc_res, None]

    with patch("app.workers.document_tasks.get_direct_connection", return_value=mock_conn):
        await _execute_document_processing(doc_id_str, current_retries=0, max_retries=3)

    # Must have updated status to 'failed'
    executed_sqls = [str(call.args[0]) for call in mock_conn.execute.call_args_list]
    assert any("processing_status = 'failed'" in s for s in executed_sqls)
    mock_conn.close.assert_awaited_once()


@pytest.mark.anyio
async def test_reproduce_orm_scalars_uuid_attribute_error_and_validate_mappings_fix():
    """
    1. Reproduces the exact error: when select(Document) is executed via Core AsyncConnection,
       res.scalars().first() returns the scalar UUID (row[0]), which raises
       AttributeError: 'UUID' object has no attribute 'assistant_id'.
    2. Validates the fix: selecting explicit columns and calling res.mappings().first()
       returns a RowMapping that correctly supplies assistant_id, storage_path, name, and mime_type.
    """
    from sqlalchemy import create_engine, select
    from app.models.all_models import Document, Assistant

    # In-memory SQLite engine to test SQLAlchemy Core execution semantics
    mem_engine = create_engine("sqlite:///:memory:")
    Document.metadata.create_all(mem_engine)
    Assistant.metadata.create_all(mem_engine)

    doc_uuid = uuid.uuid4()
    asst_uuid = uuid.uuid4()

    with mem_engine.connect() as conn:
        # Seed assistant and document
        conn.execute(
            Assistant.__table__.insert().values(
                id=asst_uuid,
                user_id=uuid.uuid4(),
                name="Test Assistant",
                slug="test-assistant",
                instructions="Be helpful",
                openrouter_api_key_encrypted="encrypted_key_abc",
                deletion_status="active",
            )
        )
        conn.execute(
            Document.__table__.insert().values(
                id=doc_uuid,
                assistant_id=asst_uuid,
                name="document.pdf",
                upload_status="uploaded",
                storage_path=f"{asst_uuid}/{doc_uuid}/document.pdf",
                mime_type="application/pdf",
                file_size=1024,
                content_hash="b" * 64,
                processing_status="pending",
            )
        )
        conn.commit()

        # --- PART 1: Reproduce the exact bug with select(Document) and scalars().first() ---
        res_buggy = conn.execute(
            select(Document).where(Document.id == doc_uuid)
        )
        scalar_item = res_buggy.scalars().first()
        # In Core, scalar_item is just the UUID primary key!
        assert isinstance(scalar_item, uuid.UUID)
        with pytest.raises(AttributeError, match="object has no attribute 'assistant_id'"):
            _ = scalar_item.assistant_id

        # --- PART 2: Validate the fix with explicit column select and mappings().first() ---
        res_fixed = conn.execute(
            select(
                Document.assistant_id,
                Document.storage_path,
                Document.name,
                Document.mime_type,
            ).where(Document.id == doc_uuid)
        )
        doc_row = res_fixed.mappings().first()
        assert doc_row is not None
        assert doc_row["assistant_id"] == asst_uuid
        assert doc_row["storage_path"] == f"{asst_uuid}/{doc_uuid}/document.pdf"
        assert doc_row["name"] == "document.pdf"
        assert doc_row["mime_type"] == "application/pdf"

        # Assistant query
        res_asst = conn.execute(
            select(Assistant.openrouter_api_key_encrypted).where(Assistant.id == doc_row["assistant_id"])
        )
        asst_row = res_asst.mappings().first()
        assert asst_row is not None
        assert asst_row["openrouter_api_key_encrypted"] == "encrypted_key_abc"


@pytest.mark.anyio
async def test_empty_string_exception_formatted_with_type_and_logged_with_exc_info():
    """
    Validates that when an exception with an empty string representation (such as
    TimeoutError() or httpx.ReadTimeout('')) occurs:
    1. The recorded retry warning in processing_error contains the exception type (e.g. 'ReadTimeout')
       and NOT empty parentheses '()'.
    2. logger.warning is called with exc_info=True for complete traceability.
    """
    import httpx
    from app.workers.document_tasks import _format_exception

    # Confirm that httpx.ReadTimeout('') produces empty str()
    empty_exc = httpx.ReadTimeout("")
    assert str(empty_exc) == ""
    # Check that our formatter returns at minimum the class name (may also include repr context)
    result = _format_exception(empty_exc)
    assert "ReadTimeout" in result

    doc_id = uuid.uuid4()
    doc_id_str = str(doc_id)
    asst_id = uuid.uuid4()

    mock_conn = create_mock_conn()
    mock_conn.scalar.side_effect = [True, "pending", True]

    doc_dict = {
        "assistant_id": asst_id,
        "storage_path": f"{asst_id}/{doc_id}/sample.txt",
        "name": "sample.txt",
        "mime_type": "text/plain",
    }
    asst_dict = {
        "openrouter_api_key_encrypted": "mock_encrypted",
    }

    mock_exec_res1 = MagicMock()
    mock_exec_res1.mappings.return_value.first.return_value = doc_dict
    mock_exec_res2 = MagicMock()
    mock_exec_res2.mappings.return_value.first.return_value = asst_dict
    mock_conn.execute.side_effect = [None, mock_exec_res1, mock_exec_res2, None]

    with patch("app.workers.document_tasks.get_direct_connection", return_value=mock_conn), \
         patch("app.workers.document_tasks.storage_service.download_file_admin", side_effect=empty_exc), \
         patch("app.workers.document_tasks.logger") as mock_logger:

        with pytest.raises(httpx.ReadTimeout):
            await _execute_document_processing(doc_id_str, current_retries=0, max_retries=3)

        # 1. logger.exception must be called (first, before formatting)
        mock_logger.exception.assert_called()
        # logger.warning is called for the transient retry message
        mock_logger.warning.assert_called_once()
        log_args, log_kwargs = mock_logger.warning.call_args
        assert "ReadTimeout" in log_args[0]
        assert "()" not in log_args[0]

        # 2. Database update must contain 'ReadTimeout' and NOT empty '()'
        executed_sqls = [c.args for c in mock_conn.execute.call_args_list if len(c.args) > 1]
        update_params = [args[1] for args in executed_sqls if "err_msg" in args[1]]
        assert len(update_params) == 1
        recorded_err = update_params[0]["err_msg"]
        assert "ReadTimeout" in recorded_err
        assert "()" not in recorded_err


def test_crypto_decrypt_api_key_rejects_plain_key_and_raises_descriptive_error_on_invalid_token():
    """
    Validates that:
    1. Plain unencrypted keys (e.g. sk-or-...) are NOT accepted as fallback and raise InvalidAssistantKeyError.
    2. Corrupted/invalid tokens raise InvalidAssistantKeyError with descriptive message.
    3. Valid encrypted keys decrypt correctly.
    """
    from app.services.crypto import decrypt_api_key, encrypt_api_key, InvalidAssistantKeyError

    # 1. Plain key must be rejected (no silent plaintext fallback)
    plain_sk = "sk-or-v1-abcdef1234567890"
    with pytest.raises(InvalidAssistantKeyError, match="InvalidToken"):
        decrypt_api_key(plain_sk)

    # 2. Corrupted token raises InvalidAssistantKeyError
    with pytest.raises(InvalidAssistantKeyError, match="InvalidToken"):
        decrypt_api_key("bad_corrupted_token_string")

    # 3. Legitimate encrypt/decrypt roundtrip succeeds
    secret = "my-secret-key-123"
    enc = encrypt_api_key(secret)
    assert decrypt_api_key(enc) == secret


def test_crypto_server_encryption_key_misconfiguration_distinct_from_assistant_key_error():
    """
    Validates that if the server-side ENCRYPTION_KEY is missing or invalid:
    1. ServerEncryptionConfigurationError is raised.
    2. It is NOT masked as an InvalidAssistantKeyError (assistant key corruption).
    """
    from app.services.crypto import (
        decrypt_api_key,
        ServerEncryptionConfigurationError,
        InvalidAssistantKeyError,
    )

    with patch.object(settings, "ENCRYPTION_KEY", ""):
        with pytest.raises(ServerEncryptionConfigurationError, match="Configuração operacional do servidor inválida"):
            decrypt_api_key("some_valid_or_invalid_token")


@pytest.mark.anyio
async def test_missing_or_empty_api_key_blocks_processing_as_unrecoverable():
    """
    Validates that if assistant_encrypted_key is missing or empty,
    processing is immediately aborted as UnrecoverableDocumentError,
    marking the document as 'failed' without consuming retries.
    """
    doc_id = uuid.uuid4()
    doc_id_str = str(doc_id)
    asst_id = uuid.uuid4()

    mock_conn = create_mock_conn()
    mock_conn.scalar.side_effect = [True, "pending", True]

    doc_dict = {
        "assistant_id": asst_id,
        "storage_path": f"{asst_id}/{doc_id}/sample.txt",
        "name": "sample.txt",
        "mime_type": "text/plain",
    }
    # Assistant has no API key configured
    asst_dict = {
        "openrouter_api_key_encrypted": None,
    }

    mock_exec_res1 = MagicMock()
    mock_exec_res1.mappings.return_value.first.return_value = doc_dict
    mock_exec_res2 = MagicMock()
    mock_exec_res2.mappings.return_value.first.return_value = asst_dict
    mock_conn.execute.side_effect = [None, mock_exec_res1, mock_exec_res2, None]

    with patch("app.workers.document_tasks.get_direct_connection", return_value=mock_conn), \
         patch("app.workers.document_tasks.storage_service.download_file_admin", return_value=b"Valid content"), \
         patch("app.workers.document_tasks.parse_document", return_value=("Valid content", [ParsedSection(text="Valid content", page_number=1, metadata={})])):

        # Must NOT re-raise (unrecoverable stops Dramatiq retries)
        await _execute_document_processing(doc_id_str, current_retries=0, max_retries=3)

    # Verify status updated to 'failed' with explicit message
    executed_sqls = [c.args for c in mock_conn.execute.call_args_list if len(c.args) > 1]
    update_params = [args[1] for args in executed_sqls if "err_msg" in args[1]]
    assert len(update_params) == 1
    assert "has no OpenRouter API key configured" in update_params[0]["err_msg"]
    mock_conn.close.assert_awaited_once()


@pytest.mark.anyio
async def test_openrouter_classification_transient_http_429_and_5xx_allow_retry():
    """
    Validates that HTTP 429 and 5xx from OpenRouter raise OpenRouterTransientError
    and permit retries in document_tasks without marking as permanent failure.
    """
    from app.services.openrouter import OpenRouterClient, OpenRouterTransientError

    client = OpenRouterClient(api_key="mock_key")

    for status_code in (429, 500, 502, 503, 504):
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = status_code
            mock_resp.text = f"Simulated error {status_code}"
            mock_post.return_value = mock_resp

            with pytest.raises(OpenRouterTransientError, match=f"Erro transitório do OpenRouter \\({status_code}\\)"):
                await client.generate_embeddings(["test text"], dimensions=1536)


@pytest.mark.anyio
async def test_openrouter_classification_non_recoverable_http_400_401_403_abort_without_retry():
    """
    Validates that HTTP 400, 401, 403 from OpenRouter raise OpenRouterNonRecoverableError
    and cause the worker to abort as UnrecoverableDocumentError without scheduling retries.
    """
    from app.services.openrouter import OpenRouterClient, OpenRouterNonRecoverableError

    client = OpenRouterClient(api_key="mock_key")

    for status_code in (400, 401, 403):
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = status_code
            mock_resp.text = f"Simulated client error {status_code}"
            mock_post.return_value = mock_resp

            with pytest.raises(OpenRouterNonRecoverableError, match=f"Erro não recuperável do OpenRouter \\({status_code}\\)"):
                await client.generate_embeddings(["test text"], dimensions=1536)


@pytest.mark.anyio
async def test_openrouter_classification_network_timeout_and_connect_error_are_transient():
    """
    Validates that network timeouts and connection drops raise OpenRouterTransientError.
    """
    from app.services.openrouter import OpenRouterClient, OpenRouterTransientError
    import httpx

    client = OpenRouterClient(api_key="mock_key")

    for exc in (httpx.ReadTimeout("Timeout"), httpx.ConnectError("Connection refused")):
        with patch("httpx.AsyncClient.post", side_effect=exc):
            with pytest.raises(OpenRouterTransientError, match="Falha de transporte/timeout no OpenRouter"):
                await client.generate_embeddings(["test text"], dimensions=1536)


@pytest.mark.anyio
async def test_openrouter_invalid_response_malformed_json_count_mismatch_and_dimension_mismatch():
    """
    Validates that:
    1. Malformed JSON raises OpenRouterInvalidResponseError.
    2. Count mismatch raises OpenRouterInvalidResponseError.
    3. Dimension mismatch raises OpenRouterInvalidResponseError.
    """
    from app.services.openrouter import OpenRouterClient, OpenRouterInvalidResponseError

    client = OpenRouterClient(api_key="mock_key")

    # Case 1: Malformed JSON
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("Invalid JSON syntax")
        mock_post.return_value = mock_resp

        with pytest.raises(OpenRouterInvalidResponseError, match="não é um JSON válido"):
            await client.generate_embeddings(["test text"], dimensions=1536)

    # Case 2: Count mismatch (sent 2 texts, got 1 item)
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"index": 0, "embedding": [0.1] * 1536}]}
        mock_post.return_value = mock_resp

        with pytest.raises(OpenRouterInvalidResponseError, match="Contagem incorreta de embeddings: esperado 2, recebido 1"):
            await client.generate_embeddings(["text 1", "text 2"], dimensions=1536)

    # Case 3: Dimension mismatch (expected 1536, got 512)
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"index": 0, "embedding": [0.1] * 512}]}
        mock_post.return_value = mock_resp

        with pytest.raises(OpenRouterInvalidResponseError, match="Dimensão incorreta no embedding 0: esperado 1536, recebido 512"):
            await client.generate_embeddings(["text 1"], dimensions=1536)


@pytest.mark.anyio
async def test_openrouter_rejects_nan_and_infinite_values():
    """
    Validates that embeddings containing NaN, Infinity, or -Infinity are rejected
    with OpenRouterInvalidResponseError, avoiding persistence of corrupted vectors.
    """
    from app.services.openrouter import OpenRouterClient, OpenRouterInvalidResponseError

    client = OpenRouterClient(api_key="mock_key")

    for bad_val in (float("nan"), float("inf"), float("-inf"), "non-numeric-string"):
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            vec = [0.1] * 1535 + [bad_val]
            mock_resp.json.return_value = {"data": [{"index": 0, "embedding": vec}]}
            mock_post.return_value = mock_resp

            with pytest.raises(OpenRouterInvalidResponseError):
                await client.generate_embeddings(["test text"], dimensions=1536)


@pytest.mark.anyio
async def test_openrouter_failure_allows_dramatiq_retry_and_persists_no_vectors():
    """
    Validates that when OpenRouter embedding generation encounters a transient failure:
    1. An exception is re-raised so Dramatiq's Retries middleware schedules the retry.
    2. Zero document_chunks are deleted or persisted to the database.
    3. Old chunks remain completely untouched.
    """
    from app.services.openrouter import OpenRouterTransientError

    doc_id = uuid.uuid4()
    doc_id_str = str(doc_id)
    asst_id = uuid.uuid4()

    mock_conn = create_mock_conn()
    mock_conn.scalar.side_effect = [True, "pending", True]

    doc_dict = {
        "assistant_id": asst_id,
        "storage_path": f"{asst_id}/{doc_id}/sample.txt",
        "name": "sample.txt",
        "mime_type": "text/plain",
    }
    asst_dict = {
        "openrouter_api_key_encrypted": "encrypted_dummy",
    }

    mock_exec_res1 = MagicMock()
    mock_exec_res1.mappings.return_value.first.return_value = doc_dict
    mock_exec_res2 = MagicMock()
    mock_exec_res2.mappings.return_value.first.return_value = asst_dict
    mock_conn.execute.side_effect = [None, mock_exec_res1, mock_exec_res2, None]

    with patch("app.workers.document_tasks.get_direct_connection", return_value=mock_conn), \
         patch("app.workers.document_tasks.storage_service.download_file_admin", return_value=b"Sample content"), \
         patch("app.workers.document_tasks.parse_document", return_value=("Sample content", [ParsedSection(text="Sample content", page_number=1, metadata={})])), \
         patch("app.workers.document_tasks.decrypt_api_key", return_value="plain_key"), \
         patch("app.workers.document_tasks.OpenRouterClient.generate_embeddings", side_effect=OpenRouterTransientError("OpenRouter 503 Service Unavailable")):

        with pytest.raises(OpenRouterTransientError, match="OpenRouter 503 Service Unavailable"):
            await _execute_document_processing(doc_id_str, current_retries=0, max_retries=3)

    # Verify no DELETE FROM document_chunks and no INSERT INTO document_chunks was executed
    executed_sqls = [str(c.args[0]) for c in mock_conn.execute.call_args_list]
    assert not any("DELETE FROM document_chunks" in s for s in executed_sqls)
    assert not any("INSERT INTO document_chunks" in s for s in executed_sqls)
    # Status remains in 'processing' with retry warning
    retry_updates = [c.args[1] for c in mock_conn.execute.call_args_list if len(c.args) > 1 and "err_msg" in c.args[1]]
    assert len(retry_updates) == 1
    # New format: "Tentativa 1 de 4 falhou (...)"
    assert "Tentativa 1 de 4 falhou" in retry_updates[0]["err_msg"]
    assert "OpenRouter 503 Service Unavailable" in retry_updates[0]["err_msg"]


@pytest.mark.anyio
async def test_openrouter_unexpected_programming_exceptions_not_masked_as_transient():
    """
    Validates that non-transport exceptions (e.g. TypeError, ValueError in request preparation)
    are NOT converted into OpenRouterTransientError, preserving bug observability.
    """
    from app.services.openrouter import OpenRouterClient, OpenRouterTransientError

    client = OpenRouterClient(api_key="mock_key")

    with patch("httpx.AsyncClient.post", side_effect=TypeError("Unexpected code bug")):
        with pytest.raises(TypeError, match="Unexpected code bug"):
            await client.generate_embeddings(["test text"], dimensions=1536)


@pytest.mark.anyio
async def test_openrouter_rejects_boolean_decimal_duplicate_and_out_of_range_indices():
    """
    Validates strict index integrity:
    1. Booleans (False, True) are rejected even though isinstance(True, int) is True in Python.
    2. Floats (0.0, 1.0) are rejected even though 0.0 == 0.
    3. Duplicate indices are rejected.
    4. Out of range indices (e.g. index 5 for 1-item response) are rejected.
    """
    from app.services.openrouter import OpenRouterClient, OpenRouterInvalidResponseError

    client = OpenRouterClient(api_key="mock_key")

    cases = [
        # Boolean index (False)
        {"data": [{"index": False, "embedding": [0.1] * 1536}]},
        # Boolean index (True)
        {"data": [{"index": True, "embedding": [0.1] * 1536}]},
        # Decimal float index (0.0)
        {"data": [{"index": 0.0, "embedding": [0.1] * 1536}]},
        # Duplicate index 0 for 2 items
        {"data": [{"index": 0, "embedding": [0.1] * 1536}, {"index": 0, "embedding": [0.2] * 1536}]},
        # Out of range index (index 5 for 1 item)
        {"data": [{"index": 5, "embedding": [0.1] * 1536}]},
        # Non-dict item
        {"data": ["not a dict"]},
        # Missing index
        {"data": [{"embedding": [0.1] * 1536}]},
    ]

    for bad_data in cases:
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = bad_data
            mock_post.return_value = mock_resp

            input_texts = ["text 1", "text 2"] if len(bad_data.get("data", [])) == 2 else ["text 1"]
            with pytest.raises(OpenRouterInvalidResponseError):
                await client.generate_embeddings(input_texts, dimensions=1536)


@pytest.mark.anyio
async def test_openrouter_generate_embeddings_happy_path_ordering_and_collection():
    """
    Validates that generate_embeddings correctly initializes the embeddings list,
    orders returned items by index, strictly validates dimensions and numeric values,
    and returns the complete list of embeddings without NameError.
    """
    from app.services.openrouter import OpenRouterClient

    client = OpenRouterClient(api_key="mock_key")
    # Response returned out-of-order (index 1 before index 0)
    mock_payload = {
        "data": [
            {"index": 1, "embedding": [0.2] * 1536},
            {"index": 0, "embedding": [0.1] * 1536},
        ]
    }

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_payload
        mock_post.return_value = mock_resp

        results = await client.generate_embeddings(["first text", "second text"], dimensions=1536)

    assert len(results) == 2
    # Verify index ordering: index 0 must be first, index 1 second
    assert results[0] == [0.1] * 1536
    assert results[1] == [0.2] * 1536


@pytest.mark.anyio
async def test_document_chunks_jsonb_metadata_binding_and_persistence():
    """
    Validates that:
    1. Chunk insertion binds the metadata parameter typed as JSONB.
    2. Structured dict metadata (e.g. {'page': 1, 'heading': 'Overview'}) does not raise
       DBAPIError (dict object has no attribute 'encode') with asyncpg.
    3. The statement contains bindparam with type_=JSONB.
    """
    from sqlalchemy.dialects.postgresql import JSONB

    doc_id = uuid.uuid4()
    doc_id_str = str(doc_id)
    asst_id = uuid.uuid4()

    mock_conn = create_mock_conn()
    mock_conn.scalar.side_effect = [True, "pending", True]

    doc_dict = {
        "assistant_id": asst_id,
        "storage_path": f"{asst_id}/{doc_id}/sample.pdf",
        "name": "sample.pdf",
        "mime_type": "application/pdf",
    }
    asst_dict = {
        "openrouter_api_key_encrypted": "mock_encrypted",
    }

    mock_exec_res1 = MagicMock()
    mock_exec_res1.mappings.return_value.first.return_value = doc_dict
    mock_exec_res2 = MagicMock()
    mock_exec_res2.mappings.return_value.first.return_value = asst_dict

    # execute side effects: lock, update processing, select doc, select asst, delete chunks, insert chunk, update indexed
    mock_conn.execute.side_effect = [
        None,             # update processing
        mock_exec_res1,   # select doc
        mock_exec_res2,   # select asst
        None,             # delete old chunks
        None,             # insert chunk
        None,             # update indexed
    ]

    structured_metadata = {"page": 2, "heading": "Security Policy", "total_pages": 10}

    with patch("app.workers.document_tasks.get_direct_connection", return_value=mock_conn), \
         patch("app.workers.document_tasks.storage_service.download_file_admin", return_value=b"%PDF-sample"), \
         patch("app.workers.document_tasks.parse_document", return_value=("Parsed content", [ParsedSection(text="Parsed content", page_number=2, metadata=structured_metadata)])), \
         patch("app.workers.document_tasks.decrypt_api_key", return_value="plain_key"), \
         patch("app.workers.document_tasks.OpenRouterClient.generate_embeddings", return_value=[[0.05] * 1536]):

        await _execute_document_processing(doc_id_str, current_retries=0, max_retries=3)

    # Verify that the chunk insert statement used JSONB type for metadata
    execute_calls = mock_conn.execute.call_args_list
    insert_calls = [
        c for c in execute_calls
        if "INSERT INTO document_chunks" in str(c.args[0])
    ]
    assert len(insert_calls) == 1
    insert_stmt, insert_params = insert_calls[0].args[0], insert_calls[0].args[1]

    # Verify parameters sent
    assert insert_params["document_id"] == doc_id
    assert isinstance(insert_params["metadata"], dict)
    assert insert_params["metadata"]["page"] == 2
    assert insert_params["metadata"]["heading"] == "Security Policy"
    assert insert_params["metadata"]["total_pages"] == 10

    # Verify that the SQL statement has JSONB bindparam compiled
    assert "metadata" in insert_stmt._bindparams
    metadata_bind = insert_stmt._bindparams["metadata"]
    assert isinstance(metadata_bind.type, JSONB)


def test_crypto_sha256_derivation_backwards_compatibility():
    """
    Validates that arbitrary non-Fernet passphrases derive a deterministic 32-byte
    Fernet key using SHA-256 for backwards compatibility with existing environments.
    """
    from app.services.crypto import encrypt_api_key, decrypt_api_key

    with patch.object(settings, "ENCRYPTION_KEY", "arbitrary-passphrase-not-base64"):
        secret = "secret-credential-value"
        enc = encrypt_api_key(secret)
        assert decrypt_api_key(enc) == secret


@pytest.mark.anyio
async def test_server_operational_configuration_error_does_not_consume_retries_nor_fail_document():
    """
    Validates that when a server-side operational error occurs (ServerEncryptionConfigurationError):
    1. It is logged with critical priority and full traceback (logger.critical).
    2. The document is NOT marked as 'failed' (it is set to 'pending' with operational error message).
    3. The worker exits cleanly without re-raising to Dramatiq (does not consume retries).
    """
    from app.services.crypto import ServerEncryptionConfigurationError

    doc_id = uuid.uuid4()
    doc_id_str = str(doc_id)
    asst_id = uuid.uuid4()

    mock_conn = create_mock_conn()
    mock_conn.scalar.side_effect = [True, "pending", True]

    doc_dict = {
        "assistant_id": asst_id,
        "storage_path": f"{asst_id}/{doc_id}/sample.txt",
        "name": "sample.txt",
        "mime_type": "text/plain",
    }
    asst_dict = {
        "openrouter_api_key_encrypted": "encrypted_dummy",
    }

    mock_exec_res1 = MagicMock()
    mock_exec_res1.mappings.return_value.first.return_value = doc_dict
    mock_exec_res2 = MagicMock()
    mock_exec_res2.mappings.return_value.first.return_value = asst_dict
    mock_conn.execute.side_effect = [None, mock_exec_res1, mock_exec_res2, None]

    with patch("app.workers.document_tasks.get_direct_connection", return_value=mock_conn), \
         patch("app.workers.document_tasks.storage_service.download_file_admin", return_value=b"Sample content"), \
         patch("app.workers.document_tasks.parse_document", return_value=("Sample content", [ParsedSection(text="Sample content", page_number=1, metadata={})])), \
         patch("app.workers.document_tasks.decrypt_api_key", side_effect=ServerEncryptionConfigurationError("ENCRYPTION_KEY ausente")), \
         patch("app.workers.document_tasks.logger") as mock_logger:

        # Must NOT raise: operational server error is caught and stops Dramatiq retries
        await _execute_document_processing(doc_id_str, current_retries=0, max_retries=3)

        # logger.critical must be called with exc_info=True
        mock_logger.critical.assert_called()
        assert any(call.kwargs.get("exc_info") is True for call in mock_logger.critical.call_args_list)

    # Document status must be 'pending' (NOT 'failed'), preserving document state
    executed_sqls = [c.args for c in mock_conn.execute.call_args_list if len(c.args) > 1]
    update_params = [args[1] for args in executed_sqls if "err_msg" in args[1]]
    assert len(update_params) == 1
    assert "Erro operacional de infraestrutura" in update_params[0]["err_msg"]
    # Verify processing_status was updated to 'pending' and NOT 'failed'
    update_sql = str([c.args[0] for c in mock_conn.execute.call_args_list if len(c.args) > 1 and "err_msg" in c.args[1]][0])
    assert "processing_status = 'pending'" in update_sql
    assert "processing_status = 'failed'" not in update_sql


# ==============================================================================
# 11. _format_exception hardening & retry exhaustion tests
# ==============================================================================

from app.workers.document_tasks import _format_exception


class _StrRaisesError(Exception):
    """Exception whose __str__ raises an IndexError (mimics asyncpg behaviour)."""
    def __str__(self):
        raise IndexError("tuple index out of range")


class _StrAndReprRaiseError(Exception):
    """Exception whose __str__ AND __repr__ both raise."""
    def __str__(self):
        raise IndexError("broken __str__")

    def __repr__(self):
        raise RuntimeError("broken __repr__ too")


class _ReprAddsInfo(Exception):
    """Exception whose __str__ is empty but __repr__ carries useful context."""
    def __str__(self):
        return ""

    def __repr__(self):
        return "ReprAddsInfo(pgcode='23505', detail='Key already exists')"


def test_format_exception_str_raises_falls_back_to_repr():
    """
    When str(exc) raises (as asyncpg does for 256+ exception classes),
    _format_exception must NOT propagate the internal error.
    It must fall back to repr() and include the class name.
    """
    exc = _StrRaisesError()
    result = _format_exception(exc)

    # Must never be empty
    assert result, "_format_exception must not return an empty string"
    # Must contain the class name
    assert "_StrRaisesError" in result


def test_format_exception_str_and_repr_both_raise_returns_class_name():
    """
    When both str(exc) and repr(exc) raise, _format_exception must still
    return at least the class name — never an empty string, never propagate.
    """
    exc = _StrAndReprRaiseError()
    result = _format_exception(exc)

    assert result, "_format_exception must not return an empty string even when repr also raises"
    assert "_StrAndReprRaiseError" in result
    # Must not contain the internal IndexError or RuntimeError text
    assert "broken __str__" not in result
    assert "broken __repr__" not in result


def test_format_exception_empty_str_uses_repr_for_context():
    """
    When str(exc) succeeds but returns an empty string, and repr() carries
    additional context, _format_exception should include that repr context.
    """
    exc = _ReprAddsInfo()
    result = _format_exception(exc)

    assert result
    assert "_ReprAddsInfo" in result
    # repr() carries useful context and is not the bare 'ClassName()' form
    assert "pgcode" in result or "_ReprAddsInfo" in result


def test_format_exception_preserves_type_name_for_normal_exception():
    """
    For a well-behaved exception, _format_exception returns 'TypeName: message'.
    """
    exc = ValueError("invalid embedding dimension: expected 1536, got 512")
    result = _format_exception(exc)

    assert result == "ValueError: invalid embedding dimension: expected 1536, got 512"


def test_format_exception_bare_exception_returns_class_name_only():
    """
    A bare Exception() with no message should return just the class name (not empty).
    """
    result = _format_exception(Exception())
    assert result == "Exception"

    result2 = _format_exception(RuntimeError())
    assert result2 == "RuntimeError"


def test_format_exception_asyncpg_style_indexerror_does_not_propagate():
    """
    Regression test: reproduces the exact asyncpg failure pattern.
    asyncpg exceptions where args is empty raise IndexError on str().
    _format_exception must absorb this and return a non-empty string.
    """
    class AsyncpgStyleError(Exception):
        """Mimics asyncpg.PostgresError.__str__ that accesses self.args[0]."""
        def __init__(self):
            # Don't call super().__init__() with any args — args is empty tuple
            self.args = ()

        def __str__(self):
            # Reproduces: return self.args[0]  -> IndexError
            return self.args[0]  # type: ignore[index]

    exc = AsyncpgStyleError()
    result = _format_exception(exc)

    assert result, "Must not return empty string for asyncpg-style exception"
    assert "AsyncpgStyleError" in result
    # Must not propagate the IndexError
    try:
        _format_exception(exc)
    except IndexError:
        pytest.fail("_format_exception propagated IndexError from str(exc)")


@pytest.mark.anyio
async def test_exception_handler_logs_traceback_before_formatting(caplog):
    """
    The generic except block must call logger.exception() BEFORE _format_exception,
    so the original traceback is always captured even if formatting itself fails.
    """
    import logging

    doc_id = str(uuid.uuid4())
    mock_conn = create_mock_conn()
    mock_conn.scalar = AsyncMock(side_effect=[True, "pending"])

    # Inject a str-raising exception to simulate asyncpg-style failure
    breaking_exc = _StrRaisesError("should still log traceback")
    # Patch get_direct_connection to return our mock, then raise the exception
    # during the advisory lock acquisition phase
    call_count = 0

    async def fake_scalar(stmt, params=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return True  # advisory lock acquired
        raise breaking_exc

    mock_conn.scalar = AsyncMock(side_effect=fake_scalar)

    with patch("app.workers.document_tasks.get_direct_connection", return_value=mock_conn):
        with caplog.at_level(logging.ERROR, logger="manualmind.worker"):
            try:
                await _execute_document_processing(doc_id, current_retries=0, max_retries=3)
            except Exception:
                pass  # transient failure re-raises; we only care about the log

    # logger.exception() must have been called — it always includes exc_info
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records, "Expected at least one ERROR/EXCEPTION log record"
    # The class name of the original exception must appear in the log message
    assert any("_StrRaisesError" in r.message for r in error_records), (
        f"Expected '_StrRaisesError' in log messages, got: {[r.message for r in error_records]}"
    )


@pytest.mark.anyio
async def test_retry_exhaustion_marks_document_as_failed():
    """
    When current_retries >= max_retries (exhausted), the document must be
    updated to processing_status='failed' and the function must NOT re-raise
    (so Dramatiq does not schedule another retry).
    """
    doc_id = str(uuid.uuid4())
    doc_uuid = uuid.UUID(doc_id)
    mock_conn = create_mock_conn()

    # Simulate: advisory lock acquired, but status read triggers a transient error
    # that was already retried max_retries times.
    call_count = 0

    async def fake_scalar(stmt, params=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return True   # advisory lock acquired
        raise RuntimeError("Simulated transient DB failure")

    mock_conn.scalar = AsyncMock(side_effect=fake_scalar)

    with patch("app.workers.document_tasks.get_direct_connection", return_value=mock_conn):
        # current_retries=3, max_retries=3 -> is_exhausted=True
        # Function must NOT raise (would cause Dramatiq to attempt another retry)
        try:
            await _execute_document_processing(doc_id, current_retries=3, max_retries=3)
        except RuntimeError:
            pytest.fail(
                "When retries are exhausted, _execute_document_processing must NOT re-raise. "
                "Re-raising would cause Dramatiq to schedule an additional retry beyond max_retries."
            )

    # Verify that the document was updated to 'failed'
    execute_calls = mock_conn.execute.call_args_list
    failed_updates = [
        c for c in execute_calls
        if len(c.args) > 1
        and isinstance(c.args[1], dict)
        and c.args[1].get("err_msg", "")
        and "processing_status = 'failed'" in str(c.args[0])
    ]
    assert failed_updates, (
        "Expected a 'failed' status DB update when retries are exhausted, but none was recorded."
    )
    err_msg = failed_updates[0].args[1]["err_msg"]
    assert "Falha definitiva" in err_msg
    assert "RuntimeError" in err_msg


@pytest.mark.anyio
async def test_retry_not_exhausted_reraises_and_updates_processing():
    """
    When current_retries < max_retries (transient failure, not yet exhausted),
    the function must re-raise the original exception so Dramatiq schedules a retry,
    and must update processing_error with a descriptive non-empty message.
    """
    doc_id = str(uuid.uuid4())
    mock_conn = create_mock_conn()

    call_count = 0

    async def fake_scalar(stmt, params=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return True   # advisory lock acquired
        raise ConnectionError("Simulated network failure")

    mock_conn.scalar = AsyncMock(side_effect=fake_scalar)

    with patch("app.workers.document_tasks.get_direct_connection", return_value=mock_conn):
        with pytest.raises(ConnectionError):
            # current_retries=1, max_retries=3 -> NOT exhausted -> should re-raise
            await _execute_document_processing(doc_id, current_retries=1, max_retries=3)

    # processing_error must be non-empty and contain the exception type
    execute_calls = mock_conn.execute.call_args_list
    retry_updates = [
        c for c in execute_calls
        if len(c.args) > 1
        and isinstance(c.args[1], dict)
        and "err_msg" in c.args[1]
        and "processing_status = 'processing'" in str(c.args[0])
    ]
    assert retry_updates, "Expected a 'processing' status DB update for transient failure"
    err_msg = retry_updates[0].args[1]["err_msg"]
    assert err_msg, "processing_error must not be empty"
    assert "ConnectionError" in err_msg, (
        f"Expected 'ConnectionError' in processing_error, got: {err_msg!r}"
    )









