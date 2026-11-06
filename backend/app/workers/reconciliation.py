import asyncio
import logging
import signal
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.services.storage import storage_service

logger = logging.getLogger("manualmind.reconciliation")


async def reconcile_abandoned_uploads(
    session: Optional[AsyncSession] = None,
    older_than_hours: int = 2,
) -> int:
    """
    Finds and marks documents stuck in upload_status = 'uploading' for more than older_than_hours.
    Attempts compensatory cleanup in Supabase Storage.
    Returns the count of reconciled documents.
    """
    owns_session = session is None
    if owns_session:
        session = AsyncSessionLocal()

    reconciled_count = 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)

    try:
        async with session.begin():
            fetch_stmt = text(
                """
                SELECT id, storage_path
                FROM documents
                WHERE upload_status = 'uploading' AND created_at <= :cutoff
                FOR UPDATE SKIP LOCKED;
                """
            )
            result = await session.execute(fetch_stmt, {"cutoff": cutoff})
            rows = result.fetchall()

            for row in rows:
                doc_id = row.id
                storage_path = row.storage_path

                # Compensatory cleanup of possible orphaned storage file
                cleanup_success = True
                if storage_path:
                    try:
                        cleanup_success = await storage_service.delete_file_admin(storage_path)
                    except Exception as del_err:
                        logger.warning(
                            f"Reconciliation: Could not remove storage file '{storage_path}': {del_err}"
                        )
                        cleanup_success = False

                if not cleanup_success:
                    # Storage deletion failed:
                    # 1. Set user-facing processing_status='failed' to stop frontend polling immediately.
                    # 2. Preserve internal upload_status='uploading' so future cycles continue retrying storage cleanup.
                    # 3. Do NOT increment reconciled_count as cleanup is incomplete.
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
                            "err_msg": "Upload abandonado (> 2h). Falha temporária na exclusão do storage; nova tentativa agendada.",
                        },
                    )
                    logger.error(
                        f"Reconciliation incomplete for document {doc_id}: orphaned file '{storage_path}' could not be deleted. Retrying on next cycle."
                    )
                else:
                    # Storage file successfully deleted or already absent: mark reconciliation complete
                    await session.execute(
                        text(
                            """
                            UPDATE documents
                            SET upload_status = 'failed',
                                processing_status = 'failed',
                                processing_error = 'Upload abandonado (> 2h). Arquivo do storage removido com sucesso.',
                                updated_at = NOW()
                            WHERE id = :doc_id
                            """
                        ),
                        {"doc_id": doc_id},
                    )
                    reconciled_count += 1
                    logger.info(f"Reconciled abandoned upload for document {doc_id}")

        return reconciled_count

    finally:
        if owns_session and session:
            await session.close()


async def run_reconciliation_loop(
    poll_interval: Optional[float] = None,
    older_than_hours: Optional[int] = None,
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """
    Continuous background loop that periodically checks for abandoned uploads
    and cleans up orphaned Storage files.
    Exits cleanly when stop_event is set.
    """
    interval = poll_interval or settings.RECONCILIATION_INTERVAL_SECONDS
    hours = older_than_hours if older_than_hours is not None else settings.RECONCILIATION_OLDER_THAN_HOURS
    logger.info(
        f"Starting Abandoned Upload Reconciliation loop (interval: {interval}s, older_than: {hours}h)..."
    )

    while not (stop_event and stop_event.is_set()):
        try:
            reconciled = await reconcile_abandoned_uploads(older_than_hours=hours)
            if reconciled > 0:
                logger.info(f"Reconciliation cycle completed: {reconciled} abandoned uploads reconciled.")
        except (asyncio.CancelledError, KeyboardInterrupt):
            logger.info("Termination requested. Exiting reconciliation loop...")
            break
        except Exception as err:
            logger.error(f"Unexpected error in reconciliation cycle: {err}")

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

    logger.info("Abandoned Upload Reconciliation loop terminated.")


def main() -> None:
    """
    Entrypoint for running the Reconciliation service as an independent process.
    Supports graceful termination on SIGINT, SIGTERM, and SIGBREAK (Windows).
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Initializing Abandoned Upload Reconciliation process...")

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
        asyncio.run(run_reconciliation_loop(stop_event=stop_event))
    except KeyboardInterrupt:
        logger.info("Process interrupted by user (KeyboardInterrupt). Exiting...")
    except SystemExit as exc:
        if exc.code not in (0, None):
            logger.error(f"Process terminated with non-zero exit code: {exc.code}")
            raise
    finally:
        logger.info("Abandoned Upload Reconciliation process shutdown complete.")


if __name__ == "__main__":
    main()


