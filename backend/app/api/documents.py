import hashlib
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from app.database import get_db
from app.auth.dependencies import get_current_user
from app.auth.jwt import AuthenticatedUser
from app.models.all_models import Assistant, Document, DocumentVersion, DocumentChunk
from app.schemas.all_schemas import DocumentResponse, DocumentVersionResponse
from app.services.crypto import decrypt_api_key
from app.services.storage import storage_service
from app.services.document_parser import parse_document, chunk_document_sections
from app.services.openrouter import OpenRouterClient

router = APIRouter(prefix="/assistants/{slug}/documents", tags=["documents"])

MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB


async def _get_user_assistant(slug: str, user: AuthenticatedUser, db: AsyncSession) -> Assistant:
    stmt = select(Assistant).where(and_(Assistant.user_id == user.id, Assistant.slug == slug))
    res = await db.execute(stmt)
    assistant = res.scalars().first()
    if not assistant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assistant not found")
    return assistant


async def _process_version_pipeline(
    doc_version: DocumentVersion,
    file_bytes: bytes,
    filename: str,
    mime_type: str,
    assistant: Assistant,
    db: AsyncSession,
):
    """
    Executes extraction, chunking, and vector embedding for a document version.
    """
    try:
        doc_version.processing_status = "processing"
        await db.commit()

        # 1. Parse text and extract sections
        extracted_text, sections = parse_document(filename, file_bytes, mime_type)
        doc_version.extracted_text = extracted_text

        # 2. Chunk text
        chunks_data = chunk_document_sections(sections, chunk_size=500, chunk_overlap=80)

        # 3. Decrypt assistant's OpenRouter key and generate embeddings
        plain_key = decrypt_api_key(assistant.openrouter_api_key_encrypted)
        openrouter = OpenRouterClient(api_key=plain_key)

        chunk_texts = [c["content"] for c in chunks_data]
        embeddings = await openrouter.generate_embeddings(chunk_texts)

        # 4. Persist chunks
        for idx, chunk in enumerate(chunks_data):
            emb = embeddings[idx] if idx < len(embeddings) else None
            db_chunk = DocumentChunk(
                document_version_id=doc_version.id,
                chunk_index=chunk["chunk_index"],
                content=chunk["content"],
                embedding=emb,
                metadata_=chunk["metadata"],
            )
            db.add(db_chunk)

        doc_version.processing_status = "indexed"
        doc_version.processing_error = None
        await db.commit()

    except Exception as e:
        await db.rollback()
        doc_version.processing_status = "failed"
        doc_version.processing_error = str(e)
        await db.commit()


@router.get("", response_model=List[DocumentResponse])
async def list_documents(
    slug: str,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lists all documents for an assistant along with their current version details."""
    assistant = await _get_user_assistant(slug, user, db)

    stmt = (
        select(Document, DocumentVersion)
        .join(
            DocumentVersion,
            and_(
                Document.id == DocumentVersion.document_id,
                Document.current_version_number == DocumentVersion.version_number,
            ),
        )
        .where(Document.assistant_id == assistant.id)
        .order_by(Document.created_at.desc())
    )
    res = await db.execute(stmt)
    rows = res.all()

    documents = []
    for doc, ver in rows:
        documents.append(
            DocumentResponse(
                id=doc.id,
                assistant_id=doc.assistant_id,
                name=doc.name,
                current_version_number=doc.current_version_number,
                file_size=ver.file_size,
                mime_type=ver.mime_type,
                processing_status=ver.processing_status,
                processing_error=ver.processing_error,
                created_at=doc.created_at,
                updated_at=doc.updated_at,
            )
        )
    return documents


@router.post("", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    slug: str,
    file: UploadFile = File(...),
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Uploads and ingests a new PDF, DOCX, or TXT document."""
    assistant = await _get_user_assistant(slug, user, db)

    file_bytes = await file.read()
    if len(file_bytes) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty.")
    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File exceeds 25 MB limit.")

    filename = file.filename or "uploaded-document.txt"
    mime_type = file.content_type or "application/octet-stream"
    content_hash = hashlib.sha256(file_bytes).hexdigest()

    # Create Document record
    new_doc = Document(
        assistant_id=assistant.id,
        name=filename,
        current_version_number=1,
    )
    db.add(new_doc)
    await db.commit()
    await db.refresh(new_doc)

    # Upload to Supabase Storage
    storage_path = f"{assistant.id}/{new_doc.id}/v1/{filename}"
    try:
        await storage_service.upload_file(
            user_token=user.token,
            storage_path=storage_path,
            file_bytes=file_bytes,
            mime_type=mime_type,
        )
    except Exception as e:
        # If remote bucket upload fails, log but record path so pipeline can complete locally
        print(f"Storage upload notice: {e}")

    # Create DocumentVersion record
    doc_ver = DocumentVersion(
        document_id=new_doc.id,
        version_number=1,
        storage_path=storage_path,
        mime_type=mime_type,
        file_size=len(file_bytes),
        content_hash=content_hash,
        processing_status="processing",
    )
    db.add(doc_ver)
    await db.commit()
    await db.refresh(doc_ver)

    # Run extraction and chunking pipeline
    await _process_version_pipeline(
        doc_version=doc_ver,
        file_bytes=file_bytes,
        filename=filename,
        mime_type=mime_type,
        assistant=assistant,
        db=db,
    )

    await db.refresh(doc_ver)

    return DocumentResponse(
        id=new_doc.id,
        assistant_id=new_doc.assistant_id,
        name=new_doc.name,
        current_version_number=new_doc.current_version_number,
        file_size=doc_ver.file_size,
        mime_type=doc_ver.mime_type,
        processing_status=doc_ver.processing_status,
        processing_error=doc_ver.processing_error,
        created_at=new_doc.created_at,
        updated_at=new_doc.updated_at,
    )


@router.post("/{doc_id}/replace", response_model=DocumentResponse)
async def replace_document(
    slug: str,
    doc_id: uuid.UUID,
    file: UploadFile = File(...),
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Uploads a new version of an existing document, preserving version history.
    Increments current_version_number and indexes the new version.
    """
    assistant = await _get_user_assistant(slug, user, db)

    stmt = select(Document).where(and_(Document.id == doc_id, Document.assistant_id == assistant.id))
    res = await db.execute(stmt)
    doc = res.scalars().first()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    file_bytes = await file.read()
    if len(file_bytes) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty.")

    filename = file.filename or doc.name
    mime_type = file.content_type or "application/octet-stream"
    content_hash = hashlib.sha256(file_bytes).hexdigest()
    next_version = doc.current_version_number + 1

    storage_path = f"{assistant.id}/{doc.id}/v{next_version}/{filename}"
    try:
        await storage_service.upload_file(
            user_token=user.token,
            storage_path=storage_path,
            file_bytes=file_bytes,
            mime_type=mime_type,
        )
    except Exception as e:
        print(f"Storage upload notice: {e}")

    new_ver = DocumentVersion(
        document_id=doc.id,
        version_number=next_version,
        storage_path=storage_path,
        mime_type=mime_type,
        file_size=len(file_bytes),
        content_hash=content_hash,
        processing_status="processing",
    )
    db.add(new_ver)
    doc.current_version_number = next_version
    doc.name = filename
    await db.commit()
    await db.refresh(new_ver)

    await _process_version_pipeline(
        doc_version=new_ver,
        file_bytes=file_bytes,
        filename=filename,
        mime_type=mime_type,
        assistant=assistant,
        db=db,
    )
    await db.refresh(new_ver)

    return DocumentResponse(
        id=doc.id,
        assistant_id=doc.assistant_id,
        name=doc.name,
        current_version_number=doc.current_version_number,
        file_size=new_ver.file_size,
        mime_type=new_ver.mime_type,
        processing_status=new_ver.processing_status,
        processing_error=new_ver.processing_error,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@router.post("/{doc_id}/reprocess", response_model=DocumentResponse)
async def reprocess_document(
    slug: str,
    doc_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Retries text extraction and indexing for a failed or updated document version.
    """
    assistant = await _get_user_assistant(slug, user, db)

    stmt = (
        select(Document, DocumentVersion)
        .join(
            DocumentVersion,
            and_(
                Document.id == DocumentVersion.document_id,
                Document.current_version_number == DocumentVersion.version_number,
            ),
        )
        .where(and_(Document.id == doc_id, Document.assistant_id == assistant.id))
    )
    res = await db.execute(stmt)
    row = res.first()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    doc, ver = row

    # Delete existing chunks for this version
    await db.execute(
        DocumentChunk.__table__.delete().where(DocumentChunk.document_version_id == ver.id)
    )
    await db.commit()

    # Download original file from storage
    try:
        file_bytes = await storage_service.download_file(user.token, ver.storage_path)
    except Exception:
        # If storage retrieval fails, use existing extracted_text if available
        file_bytes = ver.extracted_text.encode("utf-8")

    await _process_version_pipeline(
        doc_version=ver,
        file_bytes=file_bytes,
        filename=doc.name,
        mime_type=ver.mime_type,
        assistant=assistant,
        db=db,
    )
    await db.refresh(ver)

    return DocumentResponse(
        id=doc.id,
        assistant_id=doc.assistant_id,
        name=doc.name,
        current_version_number=doc.current_version_number,
        file_size=ver.file_size,
        mime_type=ver.mime_type,
        processing_status=ver.processing_status,
        processing_error=ver.processing_error,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@router.get("/{doc_id}/versions", response_model=List[DocumentVersionResponse])
async def list_document_versions(
    slug: str,
    doc_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieves all versions of a document."""
    assistant = await _get_user_assistant(slug, user, db)
    doc_stmt = select(Document).where(and_(Document.id == doc_id, Document.assistant_id == assistant.id))
    res = await db.execute(doc_stmt)
    if not res.scalars().first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    ver_stmt = (
        select(DocumentVersion)
        .where(DocumentVersion.document_id == doc_id)
        .order_by(DocumentVersion.version_number.desc())
    )
    versions = (await db.execute(ver_stmt)).scalars().all()
    return [
        DocumentVersionResponse(
            id=v.id,
            document_id=v.document_id,
            version_number=v.version_number,
            file_size=v.file_size,
            mime_type=v.mime_type,
            processing_status=v.processing_status,
            processing_error=v.processing_error,
            created_at=v.created_at,
        )
        for v in versions
    ]


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    slug: str,
    doc_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Deletes a document, its versions, its indexed chunks, and storage files."""
    assistant = await _get_user_assistant(slug, user, db)

    stmt = select(Document).where(and_(Document.id == doc_id, Document.assistant_id == assistant.id))
    res = await db.execute(stmt)
    doc = res.scalars().first()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    # Fetch storage paths for cleanup
    ver_stmt = select(DocumentVersion.storage_path).where(DocumentVersion.document_id == doc.id)
    paths = (await db.execute(ver_stmt)).scalars().all()
    for p in paths:
        try:
            await storage_service.delete_file(user.token, p)
        except Exception:
            pass

    await db.delete(doc)
    await db.commit()
    return None
