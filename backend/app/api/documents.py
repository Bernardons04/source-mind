import hashlib
import logging
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from app.database import get_db
from app.auth.dependencies import get_current_user
from app.auth.jwt import AuthenticatedUser
from app.models.all_models import Assistant, Document, DocumentChunk, DocumentOutbox
from app.schemas.all_schemas import DocumentResponse
from app.services.storage import storage_service

logger = logging.getLogger("manualmind.api.documents")

router = APIRouter(prefix="/assistants/{slug}/documents", tags=["documents"])

MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB


async def _get_user_assistant(slug: str, user: AuthenticatedUser, db: AsyncSession) -> Assistant:
    stmt = select(Assistant).where(
        and_(
            Assistant.user_id == user.id,
            Assistant.slug == slug,
            Assistant.deletion_status == "active",
        )
    )
    res = await db.execute(stmt)
    assistant = res.scalars().first()
    if not assistant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assistant not found")
    return assistant


@router.get("", response_model=List[DocumentResponse])
async def list_documents(
    slug: str,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lists all documents for an assistant."""
    assistant = await _get_user_assistant(slug, user, db)

    stmt = (
        select(Document)
        .where(Document.assistant_id == assistant.id)
        .order_by(Document.created_at.desc())
    )
    res = await db.execute(stmt)
    docs = res.scalars().all()

    return [
        DocumentResponse(
            id=doc.id,
            assistant_id=doc.assistant_id,
            name=doc.name,
            upload_status=doc.upload_status,
            file_size=doc.file_size,
            mime_type=doc.mime_type,
            processing_status=doc.processing_status,
            processing_error=doc.processing_error,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
        )
        for doc in docs
    ]


@router.get("/{doc_id}/status", response_model=DocumentResponse)
async def get_document_status(
    slug: str,
    doc_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Returns the current upload and ingestion status of a document."""
    assistant = await _get_user_assistant(slug, user, db)

    stmt = select(Document).where(and_(Document.id == doc_id, Document.assistant_id == assistant.id))
    res = await db.execute(stmt)
    doc = res.scalars().first()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    return DocumentResponse(
        id=doc.id,
        assistant_id=doc.assistant_id,
        name=doc.name,
        upload_status=doc.upload_status,
        file_size=doc.file_size,
        mime_type=doc.mime_type,
        processing_status=doc.processing_status,
        processing_error=doc.processing_error,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@router.post("", response_model=DocumentResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    slug: str,
    file: UploadFile = File(...),
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Uploads a document with transactional outbox guarantees:
    1. Pre-registers upload intention in PostgreSQL (upload_status='uploading').
    2. Uploads file to Supabase Storage.
    3. Atomically updates document to upload_status='uploaded' and inserts DocumentOutbox event.
    4. Applies dual-write compensation (immediate Storage cleanup) if PostgreSQL commit fails.
    5. Responds HTTP 202 Accepted.
    """
    assistant = await _get_user_assistant(slug, user, db)

    file_bytes = await file.read()
    if len(file_bytes) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty.")
    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File exceeds 25 MB limit.")

    filename = file.filename or "uploaded-document.txt"
    mime_type = file.content_type or "application/octet-stream"
    content_hash = hashlib.sha256(file_bytes).hexdigest()

    doc_id = uuid.uuid4()
    storage_path = f"{assistant.id}/{doc_id}/{filename}"

    # Step 1: Pre-register intention in database
    new_doc = Document(
        id=doc_id,
        assistant_id=assistant.id,
        name=filename,
        upload_status="uploading",
        storage_path=storage_path,
        mime_type=mime_type,
        file_size=len(file_bytes),
        content_hash=content_hash,
        extracted_text="",
        processing_status="pending_upload",
    )
    db.add(new_doc)
    await db.commit()
    await db.refresh(new_doc)

    # Step 2: Upload to Supabase Storage
    try:
        await storage_service.upload_file(
            user_token=user.token,
            storage_path=storage_path,
            file_bytes=file_bytes,
            mime_type=mime_type,
        )
    except Exception as e:
        new_doc.upload_status = "failed"
        new_doc.processing_status = "failed"
        new_doc.processing_error = f"Storage upload failed: {e}"
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to upload document to storage: {e}",
        )

    # Step 3: Transactional Outbox creation + dual-write compensation
    try:
        new_doc.upload_status = "uploaded"
        new_doc.processing_status = "pending"
        new_doc.processing_error = None

        outbox_event = DocumentOutbox(
            document_id=new_doc.id,
            status="pending",
            retry_count=0,
        )
        db.add(outbox_event)
        await db.commit()
        await db.refresh(new_doc)
    except Exception as commit_err:
        await db.rollback()
        # Dual-write compensation: delete file from Storage immediately
        try:
            await storage_service.delete_file_admin(storage_path)
        except Exception as del_err:
            logger.error(f"Dual-write compensation failed to remove '{storage_path}': {del_err}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to commit document metadata: {commit_err}",
        )

    # Step 4: Return HTTP 202 Accepted
    return DocumentResponse(
        id=new_doc.id,
        assistant_id=new_doc.assistant_id,
        name=new_doc.name,
        upload_status=new_doc.upload_status,
        file_size=new_doc.file_size,
        mime_type=new_doc.mime_type,
        processing_status=new_doc.processing_status,
        processing_error=new_doc.processing_error,
        created_at=new_doc.created_at,
        updated_at=new_doc.updated_at,
    )


@router.post("/{doc_id}/reprocess", response_model=DocumentResponse, status_code=status.HTTP_202_ACCEPTED)
async def reprocess_document(
    slug: str,
    doc_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Retries text extraction and indexing asynchronously via Transactional Outbox.
    """
    assistant = await _get_user_assistant(slug, user, db)

    stmt = select(Document).where(and_(Document.id == doc_id, Document.assistant_id == assistant.id))
    res = await db.execute(stmt)
    doc = res.scalars().first()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    # Delete existing chunks for this document
    await db.execute(
        DocumentChunk.__table__.delete().where(DocumentChunk.document_id == doc.id)
    )

    doc.processing_status = "pending"
    doc.processing_error = None

    # Insert outbox event for asynchronous ingestion
    outbox_event = DocumentOutbox(
        document_id=doc.id,
        status="pending",
        retry_count=0,
    )
    db.add(outbox_event)
    await db.commit()
    await db.refresh(doc)

    return DocumentResponse(
        id=doc.id,
        assistant_id=doc.assistant_id,
        name=doc.name,
        upload_status=doc.upload_status,
        file_size=doc.file_size,
        mime_type=doc.mime_type,
        processing_status=doc.processing_status,
        processing_error=doc.processing_error,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    slug: str,
    doc_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Deletes a document, its indexed chunks, and storage file."""
    assistant = await _get_user_assistant(slug, user, db)

    stmt = select(Document).where(and_(Document.id == doc_id, Document.assistant_id == assistant.id))
    res = await db.execute(stmt)
    doc = res.scalars().first()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    if doc.storage_path:
        try:
            await storage_service.delete_file(user.token, doc.storage_path)
        except Exception:
            pass

    await db.delete(doc)
    await db.commit()
    return None
