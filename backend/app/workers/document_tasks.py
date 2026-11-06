import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional
import dramatiq
from dramatiq.middleware import CurrentMessage
from sqlalchemy import bindparam, delete, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import settings
from app.database import get_direct_connection
from app.models.all_models import Assistant, Document, DocumentChunk
from app.services.crypto import (
    decrypt_api_key,
    InvalidAssistantKeyError,
    ServerEncryptionConfigurationError,
)
from app.services.document_parser import chunk_document_sections, parse_document
from app.services.openrouter import (
    OpenRouterClient,
    OpenRouterTransientError,
    OpenRouterNonRecoverableError,
    OpenRouterInvalidResponseError,
)
from app.services.storage import storage_service, validate_storage_path
from app.workers.broker import rabbitmq_broker

logger = logging.getLogger("manualmind.worker")


class UnrecoverableDocumentError(Exception):
    """Irreversible error during document parsing or validation (should not be retried)."""
    pass


def _format_exception(exc: BaseException) -> str:
    """
    Safely formats an exception for database logging and monitoring, guaranteeing
    a non-empty descriptive string even when str(exc) or repr(exc) are unavailable.

    Defensive against exceptions whose __str__ or __repr__ themselves raise
    (e.g. asyncpg exceptions where str() raises IndexError when args is empty).
    Always returns at least the class name so the original exception type is visible.
    """
    exc_type = type(exc).__name__

    # Attempt str() first — most informative for well-behaved exceptions
    try:
        msg = str(exc).strip()
        if msg:
            return f"{exc_type}: {msg}"
    except Exception:
        pass

    # Fallback: repr() often includes pgcode, args, and other context
    try:
        r = repr(exc).strip()
        # Only use repr if it adds information beyond the bare class name
        if r and r not in (f"{exc_type}()", exc_type):
            return f"{exc_type} | repr: {r[:300]}"
    except Exception:
        pass

    # Last resort: just the class name — never an empty string
    return exc_type


async def _execute_document_processing(
    document_id_str: str,
    current_retries: int = 0,
    max_retries: int = 3,
) -> None:
    """
    Asynchronous execution of document ingestion with:
    1. Dedicated connection from direct_engine (Session Mode / port 5432).
    2. 64-bit session advisory lock via hashtextextended.
    3. Immediate status update to 'processing'.
    4. Text extraction, structural chunking, and embedding generation outside SQL transactions.
    5. Atomic persistence transaction.
    6. Differentiated transient vs terminal failure handling.
    7. Guaranteed advisory lock release and connection invalidation on unlock error.
    """
    try:
        doc_uuid = uuid.UUID(document_id_str)
    except (ValueError, TypeError) as e:
        logger.error(f"Invalid document UUID format '{document_id_str}': {e}")
        return

    # Dedicated connection for session-level advisory lock
    conn: Optional[AsyncConnection] = None
    lock_acquired = False

    try:
        conn = await get_direct_connection()

        # 1. 64-bit PostgreSQL session advisory lock using hashtextextended
        # Executed within an explicit transaction to prevent uncontrolled autobegin.
        # Session advisory locks persist on the connection across transaction commit/rollback.
        async with conn.begin():
            lock_res = await conn.scalar(
                text("SELECT pg_try_advisory_lock(hashtextextended(:doc_id, 0))"),
                {"doc_id": document_id_str},
            )
        lock_acquired = bool(lock_res)

        if not lock_acquired:
            # Another worker is actively processing this document.
            # Silently exit and ACK to broker (no retry).
            logger.info(
                f"Advisory lock busy for document {document_id_str}. Another worker is running. Skipping."
            )
            return

        # 2. Quick Transaction 1: Check existing status & mark status as 'processing' immediately
        async with conn.begin():
            existing_status = await conn.scalar(
                text("SELECT processing_status FROM documents WHERE id = :document_id"),
                {"document_id": doc_uuid},
            )
            if existing_status == "indexed":
                logger.info(
                    f"Document {document_id_str} is already indexed. Duplicate at-least-once message detected; skipping."
                )
                return

            await conn.execute(
                text(
                    """
                    UPDATE documents
                    SET processing_status = 'processing',
                        processing_error = NULL,
                        updated_at = NOW()
                    WHERE id = :document_id
                    """
                ),
                {"document_id": doc_uuid},
            )

        # 3. Retrieve document & assistant metadata in a dedicated quick read transaction
        async with conn.begin():
            res_doc = await conn.execute(
                select(
                    Document.assistant_id,
                    Document.storage_path,
                    Document.name,
                    Document.mime_type,
                ).where(Document.id == doc_uuid)
            )
            doc_row = res_doc.mappings().first()
            if not doc_row:
                raise UnrecoverableDocumentError(f"Document {document_id_str} not found in database.")

            assistant_id = doc_row["assistant_id"]
            storage_path = doc_row["storage_path"]
            doc_name = doc_row["name"]
            doc_mime = doc_row["mime_type"]

            res_asst = await conn.execute(
                select(Assistant.openrouter_api_key_encrypted).where(Assistant.id == assistant_id)
            )
            asst_row = res_asst.mappings().first()
            if not asst_row:
                raise UnrecoverableDocumentError(f"Assistant {assistant_id} not found in database.")

            assistant_encrypted_key = asst_row["openrouter_api_key_encrypted"]

        # 4. Outside SQL transaction: Path Traversal Guard & File Download
        if not validate_storage_path(storage_path):
            raise UnrecoverableDocumentError(
                f"Path Traversal Guard: storage_path '{storage_path}' is invalid or insecure."
            )

        try:
            file_bytes = await storage_service.download_file_admin(storage_path)
        except Exception as dl_err:
            if "404" in str(dl_err) or "not found" in str(dl_err).lower():
                raise UnrecoverableDocumentError(f"Document file missing in storage: {dl_err}")
            raise dl_err

        if not file_bytes or len(file_bytes) == 0:
            raise UnrecoverableDocumentError("Downloaded file is empty (0 bytes).")

        # 5. Outside SQL transaction: Parsing and chunking
        try:
            extracted_text, sections = parse_document(doc_name, file_bytes, doc_mime)
        except Exception as parse_err:
            raise UnrecoverableDocumentError(f"Document parsing error: {parse_err}")

        chunks_data = chunk_document_sections(
            sections,
            chunk_size=500,
            chunk_overlap=80,
            document_name=doc_name,
        )

        # 6. Outside SQL transaction: Embedding generation
        if not assistant_encrypted_key or not assistant_encrypted_key.strip():
            raise UnrecoverableDocumentError(
                f"Assistant {assistant_id} has no OpenRouter API key configured. Document processing cannot proceed."
            )

        try:
            plain_key = decrypt_api_key(assistant_encrypted_key)
        except InvalidAssistantKeyError as key_err:
            raise UnrecoverableDocumentError(
                f"Chave do assistente inválida: {_format_exception(key_err)}"
            )
        # Note: ServerEncryptionConfigurationError is NOT caught as UnrecoverableDocumentError;
        # it propagates as an operational server error without falsely blaming the assistant's key.

        if not plain_key or not plain_key.strip():
            raise UnrecoverableDocumentError(
                f"Assistant {assistant_id} has an empty OpenRouter API key. Document processing cannot proceed."
            )

        openrouter = OpenRouterClient(api_key=plain_key)

        chunk_texts = [c["content"] for c in chunks_data]
        embeddings: List[List[float]] = []
        if chunk_texts:
            try:
                embeddings = await openrouter.generate_embeddings(chunk_texts)
            except (OpenRouterNonRecoverableError, OpenRouterInvalidResponseError) as non_rec_err:
                # 400, 401, 403 or invalid responses (NaN, dimension mismatch, count mismatch)
                # cannot be resolved by retries -> mark as terminal failure
                raise UnrecoverableDocumentError(
                    f"Falha não recuperável no OpenRouter: {_format_exception(non_rec_err)}"
                ) from non_rec_err
            # OpenRouterTransientError (429, 5xx, timeouts, network) is intentionally NOT caught here
            # so that it bubbles up to the outer exception block as a transient failure for Dramatiq retry.

        # 7. Atomic Persistence Transaction 2
        chunk_insert_stmt = text(
            """
            INSERT INTO document_chunks (id, document_id, chunk_index, content, embedding, metadata, created_at)
            VALUES (:id, :document_id, :chunk_index, :content, :embedding, :metadata, NOW())
            """
        ).bindparams(bindparam("metadata", type_=JSONB))

        async with conn.begin():
            # Atomically delete old chunks and insert new ones
            await conn.execute(
                delete(DocumentChunk).where(DocumentChunk.document_id == doc_uuid)
            )

            for idx, chunk in enumerate(chunks_data):
                emb = embeddings[idx] if idx < len(embeddings) else None
                await conn.execute(
                    chunk_insert_stmt,
                    {
                        "id": uuid.uuid4(),
                        "document_id": doc_uuid,
                        "chunk_index": chunk["chunk_index"],
                        "content": chunk["content"],
                        "embedding": str(emb) if emb else None,
                        "metadata": chunk["metadata"],
                    },
                )

            await conn.execute(
                text(
                    """
                    UPDATE documents
                    SET processing_status = 'indexed',
                        processing_error = NULL,
                        file_size = :file_size,
                        extracted_text = :extracted_text,
                        updated_at = NOW()
                    WHERE id = :document_id
                    """
                ),
                {
                    "document_id": doc_uuid,
                    "file_size": len(file_bytes),
                    "extracted_text": extracted_text,
                },
            )

        logger.info(f"Successfully processed and indexed document {document_id_str}")

    except ServerEncryptionConfigurationError as server_err:
        # Operational server-side configuration error:
        # 1. Do NOT blame the assistant's API key or mark document permanently 'failed'.
        # 2. Do NOT consume Dramatiq retries: a server environment misconfiguration cannot resolve on immediate retry.
        # 3. Log with full traceback via logger.critical.
        # 4. Set processing_status = 'pending' with an explicit operational error message so it can be reprocessed
        #    once the server configuration is corrected.
        err_msg = f"Erro operacional de infraestrutura: {server_err}"
        logger.critical(
            f"Server operational configuration error on document {document_id_str}: {server_err}",
            exc_info=True,
        )
        if conn is not None:
            try:
                if conn.in_transaction():
                    await conn.rollback()
                async with conn.begin():
                    await conn.execute(
                        text(
                            """
                            UPDATE documents
                            SET processing_status = 'pending',
                                processing_error = :err_msg,
                                updated_at = NOW()
                            WHERE id = :document_id
                            """
                        ),
                        {"document_id": doc_uuid, "err_msg": err_msg},
                    )
            except Exception as db_err:
                logger.critical(f"Failed to record operational server error: {db_err}")
        return

    except Exception as exc:
        # Log the full traceback immediately, before any operation that could itself fail.
        # This guarantees the original exception type and stack trace are always visible
        # in the worker logs, even if subsequent DB updates or _format_exception calls fail.
        logger.exception(
            f"Document {document_id_str} processing failed on attempt {current_retries + 1} "
            f"(retries={current_retries}, max_retries={max_retries}): {type(exc).__name__}"
        )

        is_unrecoverable = isinstance(exc, UnrecoverableDocumentError)
        # Dramatiq sets retries=0 on first attempt, incrementing after each failure.
        # With max_retries=3 in the actor decorator, the 4th call arrives with retries=3.
        # is_exhausted must be True when retries >= max_retries so the 4th attempt marks 'failed'.
        is_exhausted = current_retries >= max_retries
        exc_detail = _format_exception(exc)

        if conn is not None:
            # Ensure any failed/aborted transaction on the connection is rolled back
            # before attempting to record failure/retry warnings.
            try:
                if conn.in_transaction():
                    await conn.rollback()
            except Exception as rb_err:
                logger.warning(f"Error rolling back connection on failure: {rb_err}")

        if is_unrecoverable or is_exhausted:
            # Conclusive failure: mark as 'failed', do NOT retry
            if is_unrecoverable:
                err_msg = f"Falha definitiva no processamento: {exc_detail}"
            else:
                err_msg = (
                    f"Falha definitiva após {current_retries + 1} tentativas "
                    f"(máximo {max_retries} retries): {exc_detail}"
                )
            logger.error(
                f"Document {document_id_str} marked as permanently failed: {err_msg}"
            )
            if conn is not None:
                try:
                    async with conn.begin():
                        await conn.execute(
                            text(
                                """
                                UPDATE documents
                                SET processing_status = 'failed',
                                    processing_error = :err_msg,
                                    updated_at = NOW()
                                WHERE id = :document_id
                                """
                            ),
                            {"document_id": doc_uuid, "err_msg": err_msg},
                        )
                except Exception as db_err:
                    logger.critical(f"Failed to record terminal failure in documents table: {db_err}")
            # Do NOT re-raise so Dramatiq marks message done without scheduling retry
            return
        else:
            # Transient failure: keep 'processing', update warning, re-raise for Dramatiq retry
            retry_msg = (
                f"Tentativa {current_retries + 1} de {max_retries + 1} falhou "
                f"({exc_detail}). Nova tentativa agendada..."
            )
            logger.warning(
                f"Document {document_id_str} transient failure on attempt "
                f"{current_retries + 1} of {max_retries + 1}: {exc_detail}"
            )
            if conn is not None:
                try:
                    async with conn.begin():
                        await conn.execute(
                            text(
                                """
                                UPDATE documents
                                SET processing_status = 'processing',
                                    processing_error = :err_msg,
                                    updated_at = NOW()
                                WHERE id = :document_id
                                """
                            ),
                            {"document_id": doc_uuid, "err_msg": retry_msg},
                        )
                except Exception as db_err:
                    logger.error(f"Failed to record transient warning in documents table: {db_err}")
            # Re-raise so Dramatiq's Retries middleware schedules the retry with backoff
            raise exc

    finally:
        # Guaranteed lock cleanup & connection safety
        if conn is not None:
            if lock_acquired:
                try:
                    if conn.in_transaction():
                        await conn.rollback()

                    async with conn.begin():
                        unlock_res = await conn.scalar(
                            text("SELECT pg_advisory_unlock(hashtextextended(:doc_id, 0))"),
                            {"doc_id": document_id_str},
                        )
                    if not unlock_res:
                        logger.warning(
                            f"pg_advisory_unlock returned false for document {document_id_str}. Invalidating connection."
                        )
                        await conn.invalidate()
                except Exception as unlock_err:
                    logger.error(f"Error during pg_advisory_unlock: {unlock_err}. Invalidating connection.")
                    try:
                        await conn.invalidate()
                    except Exception:
                        pass

            try:
                await conn.close()
            except Exception:
                pass


@dramatiq.actor(
    queue_name="documents",
    max_retries=3,
    min_backoff=15000,   # 15 seconds
    max_backoff=300000,  # 5 minutes
)
def process_document_task(message_payload: dict):
    """
    Dramatiq actor entry point for background document processing.
    """
    document_id_str = message_payload.get("document_id")
    if not document_id_str:
        logger.error("Missing document_id in message_payload.")
        return

    # Check current retries from Dramatiq message context
    msg = CurrentMessage.get_current_message()
    current_retries = msg.options.get("retries", 0) if msg else message_payload.get("retries", 0)
    max_retries = 3

    # Run the async pipeline
    asyncio.run(
        _execute_document_processing(
            document_id_str=document_id_str,
            current_retries=current_retries,
            max_retries=max_retries,
        )
    )
