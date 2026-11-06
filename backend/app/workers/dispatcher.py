import asyncio
import logging
import random
import signal
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import pika
from pika.exceptions import AMQPError, NackError, UnroutableError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.workers.document_tasks import process_document_task

logger = logging.getLogger("manualmind.dispatcher")


def get_pika_publisher_channel() -> tuple[pika.BlockingConnection, pika.adapters.blocking_connection.BlockingChannel]:
    """
    Creates a Pika connection with Publisher Confirms enabled.
    """
    params = pika.URLParameters(settings.RABBITMQ_URL)
    connection = pika.BlockingConnection(params)
    channel = connection.channel()
    channel.confirm_delivery()
    return connection, channel


def publish_with_publisher_confirms(
    channel: pika.adapters.blocking_connection.BlockingChannel,
    document_id_str: str,
) -> None:
    """
    Publishes a document processing message to the 'documents' queue using Pika with Publisher Confirms.
    Uses the exact Dramatiq message envelope. Raises on Nack or unroutable delivery.
    """
    # Create valid Dramatiq message
    msg = process_document_task.message({"document_id": document_id_str})
    body = msg.encode()

    channel.basic_publish(
        exchange="",
        routing_key="documents",
        body=body,
        properties=pika.BasicProperties(
            delivery_mode=2,  # Persistent message
            content_type="application/json",
        ),
        mandatory=True,
    )


async def dispatch_pending_outbox_batch(
    session: Optional[AsyncSession] = None,
    batch_size: int = 50,
    max_retries: int = 5,
    pika_channel: Optional[Any] = None,
) -> int:
    """
    Scans the document_outbox table, claims a batch safely without holding row locks
    across RabbitMQ network I/O, publishes via Pika with Publisher Confirms, and updates
    state atomically.
    
    Guarantees:
    - FOR UPDATE SKIP LOCKED protects concurrent dispatchers without blocking database transactions.
    - Network I/O occurs outside PostgreSQL locks.
    - Delivery is at-least-once: if RabbitMQ confirms but PostgreSQL commit fails, the message
      is already published; subsequent redelivery will be handled idempotently by the worker.
    - Terminal delivery failure marks BOTH document_outbox and documents as 'failed'.
    """
    owns_session = session is None
    if owns_session:
        session = AsyncSessionLocal()

    dispatched_count = 0
    pika_conn = None
    close_pika = False

    try:
        # Phase 1: Claim batch inside quick DB transaction (SKIP LOCKED) with unique claim token
        claimed_items = []
        async with session.begin():
            fetch_stmt = text(
                """
                SELECT id, document_id, retry_count
                FROM document_outbox
                WHERE (status = 'pending' OR (status = 'processing' AND next_retry_at <= NOW()))
                  AND next_retry_at <= NOW()
                ORDER BY created_at ASC
                LIMIT :batch_size
                FOR UPDATE SKIP LOCKED;
                """
            )
            result = await session.execute(fetch_stmt, {"batch_size": batch_size})
            rows = result.fetchall()

            if not rows:
                return 0

            claim_expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
            for row in rows:
                token = uuid.uuid4()
                claimed_items.append({
                    "id": row.id,
                    "document_id": row.document_id,
                    "retry_count": row.retry_count,
                    "claim_token": token,
                })
                # Claim row with unique claim token for fencing
                await session.execute(
                    text(
                        """
                        UPDATE document_outbox
                        SET status = 'processing',
                            claim_token = :claim_token,
                            next_retry_at = :expiry
                        WHERE id = :id
                        """
                    ),
                    {"id": row.id, "claim_token": token, "expiry": claim_expiry},
                )
        # Commit completes Phase 1: row locks are now released!

        # Phase 2 & 3: Network publication and individual state resolution
        if pika_channel is None:
            try:
                pika_conn, pika_channel = get_pika_publisher_channel()
                close_pika = True
            except Exception as conn_err:
                logger.error(f"Failed to connect to RabbitMQ broker for outbox dispatch: {conn_err}")
                # Reset claimed items back to pending if we still hold the claim token
                async with session.begin():
                    for item in claimed_items:
                        res = await session.execute(
                            text(
                                """
                                UPDATE document_outbox
                                SET status = 'pending',
                                    last_error = :err,
                                    claim_token = NULL
                                WHERE id = :id AND claim_token = :claim_token
                                """
                            ),
                            {
                                "id": item["id"],
                                "claim_token": item["claim_token"],
                                "err": f"Broker connection failed: {conn_err}",
                            },
                        )
                        if res.rowcount == 0:
                            logger.warning(
                                f"Fencing: Claim token lost for outbox event {item['id']} on broker connect failure."
                            )
                return 0

        for item in claimed_items:
            event_id = item["id"]
            doc_id = item["document_id"]
            doc_id_str = str(doc_id)
            retry_count = item["retry_count"]
            claim_token = item["claim_token"]

            publish_success = False
            pub_error = None

            # Network I/O outside DB transaction
            try:
                publish_with_publisher_confirms(pika_channel, doc_id_str)
                publish_success = True
            except (AMQPError, NackError, UnroutableError, Exception) as err:
                publish_success = False
                pub_error = err

            # Phase 3: Update DB state using fencing claim_token
            if publish_success:
                try:
                    async with session.begin():
                        res = await session.execute(
                            text(
                                """
                                UPDATE document_outbox
                                SET status = 'dispatched',
                                    dispatched_at = NOW(),
                                    last_error = NULL,
                                    claim_token = NULL
                                WHERE id = :event_id AND claim_token = :claim_token
                                """
                            ),
                            {"event_id": event_id, "claim_token": claim_token},
                        )
                        if res.rowcount == 0:
                            logger.warning(
                                f"Fencing: Claim token lost for outbox event {event_id}. "
                                f"Another dispatcher assumed ownership; ignoring stale update."
                            )
                            continue

                    dispatched_count += 1
                    logger.info(f"Dispatched outbox event {event_id} for document {doc_id_str}")
                except Exception as commit_err:
                    # Message was confirmed by RabbitMQ, but local DB update failed (at-least-once edge case).
                    # The message will be processed by the worker. The event remains in 'processing' and will expire.
                    logger.critical(
                        f"At-least-once warning: RabbitMQ confirmed event {event_id}, but DB commit failed: {commit_err}. "
                        f"Worker will process message idempotently."
                    )
            else:
                new_retries = retry_count + 1
                base_delay = min(300.0, (2 ** new_retries) * 2.0)
                jitter = random.uniform(0.1, 1.5)
                next_retry = datetime.now(timezone.utc) + timedelta(seconds=base_delay + jitter)

                if new_retries >= max_retries:
                    err_msg = f"Delivery failed after {new_retries} attempts: {pub_error}"
                    logger.error(f"Outbox event {event_id} permanently failed: {err_msg}")

                    # Transactionally mark BOTH outbox event AND document as failed (guarded by claim_token)
                    async with session.begin():
                        res = await session.execute(
                            text(
                                """
                                UPDATE document_outbox
                                SET status = 'failed',
                                    retry_count = :retry_count,
                                    last_error = :err_msg,
                                    claim_token = NULL
                                WHERE id = :event_id AND claim_token = :claim_token
                                """
                            ),
                            {
                                "event_id": event_id,
                                "claim_token": claim_token,
                                "retry_count": new_retries,
                                "err_msg": err_msg,
                            },
                        )
                        if res.rowcount == 0:
                            logger.warning(
                                f"Fencing: Claim token lost for failed event {event_id}. Skipping stale document update."
                            )
                        else:
                            await session.execute(
                                text(
                                    """
                                    UPDATE documents
                                    SET processing_status = 'failed',
                                        processing_error = :err_msg,
                                        updated_at = NOW()
                                    WHERE id = :doc_id
                                    """
                                ),
                                {
                                    "doc_id": doc_id,
                                    "err_msg": f"Outbox dispatch permanently failed: {pub_error}",
                                },
                            )
                else:
                    warn_msg = f"Delivery attempt {new_retries} failed ({pub_error}). Backoff until {next_retry.isoformat()}"
                    logger.warning(f"Outbox event {event_id} backoff: {warn_msg}")
                    async with session.begin():
                        res = await session.execute(
                            text(
                                """
                                UPDATE document_outbox
                                SET status = 'pending',
                                    retry_count = :retry_count,
                                    next_retry_at = :next_retry,
                                    last_error = :err_msg,
                                    claim_token = NULL
                                WHERE id = :event_id AND claim_token = :claim_token
                                """
                            ),
                            {
                                "event_id": event_id,
                                "claim_token": claim_token,
                                "retry_count": new_retries,
                                "next_retry": next_retry,
                                "err_msg": warn_msg,
                            },
                        )
                        if res.rowcount == 0:
                            logger.warning(
                                f"Fencing: Claim token lost for outbox event {event_id} during backoff update."
                            )

        return dispatched_count

    finally:
        if close_pika and pika_conn:
            try:
                pika_conn.close()
            except Exception:
                pass
        if owns_session and session:
            await session.close()


async def run_dispatcher_loop(
    poll_interval: Optional[float] = None,
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """
    Continuous background loop that polls and dispatches outbox events.
    Exits cleanly when stop_event is set.
    """
    interval = poll_interval or settings.OUTBOX_POLL_INTERVAL_SECONDS
    logger.info(f"Starting Outbox Dispatcher loop (interval: {interval}s)...")

    while not (stop_event and stop_event.is_set()):
        try:
            dispatched = await dispatch_pending_outbox_batch(
                batch_size=settings.OUTBOX_BATCH_SIZE,
                max_retries=settings.OUTBOX_MAX_RETRIES,
            )
            if dispatched > 0:
                logger.debug(f"Dispatcher processed {dispatched} events.")
        except (asyncio.CancelledError, KeyboardInterrupt):
            logger.info("Termination requested. Exiting dispatcher loop...")
            break
        except Exception as err:
            logger.error(f"Unexpected error in dispatcher loop: {err}")

        if stop_event:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
            except (asyncio.CancelledError, KeyboardInterrupt):
                break
        else:
            try:
                await asyncio.sleep(interval)
            except (asyncio.CancelledError, KeyboardInterrupt):
                break

    logger.info("Outbox Dispatcher loop terminated.")


def main() -> None:
    """
    Entrypoint for running the Outbox Dispatcher as an independent process.
    Supports graceful termination on SIGINT, SIGTERM, and SIGBREAK (Windows).
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Initializing Outbox Dispatcher process...")

    stop_event = asyncio.Event()

    def handle_signal(sig: int, frame: Any) -> None:
        try:
            sig_name = signal.Signals(sig).name
        except Exception:
            sig_name = str(sig)
        logger.info(f"Received signal {sig_name}. Requesting graceful shutdown...")
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, handle_signal)

    try:
        asyncio.run(run_dispatcher_loop(stop_event=stop_event))
    except KeyboardInterrupt:
        logger.info("Process interrupted by user (KeyboardInterrupt). Exiting...")
    except SystemExit as exc:
        if exc.code not in (0, None):
            logger.error(f"Process terminated with non-zero exit code: {exc.code}")
            raise
    finally:
        logger.info("Outbox Dispatcher process shutdown complete.")


if __name__ == "__main__":
    main()

