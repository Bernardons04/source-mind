import uuid
from datetime import datetime, timezone
import pytest
from app.models.all_models import (
    Assistant,
    Document,
    DocumentChunk,
    DocumentOutbox,
    Chat,
    Message,
)
from app.schemas.all_schemas import (
    DocumentResponse,
    AssistantResponse,
)


def test_document_model_fields():
    doc_id = uuid.uuid4()
    asst_id = uuid.uuid4()
    doc = Document(
        id=doc_id,
        assistant_id=asst_id,
        name="manual_seguranca.pdf",
        upload_status="uploading",
        storage_path=f"{asst_id}/{doc_id}/manual_seguranca.pdf",
        mime_type="application/pdf",
        file_size=1024,
        content_hash="a" * 64,
        extracted_text="Conteudo de teste",
        processing_status="pending_upload",
    )
    assert doc.id == doc_id
    assert doc.assistant_id == asst_id
    assert doc.upload_status == "uploading"
    assert doc.processing_status == "pending_upload"
    assert not hasattr(doc, "current_version_number")
    assert not hasattr(doc, "versions")


def test_document_outbox_model_fields():
    outbox_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    outbox = DocumentOutbox(
        id=outbox_id,
        document_id=doc_id,
        status="pending",
        retry_count=0,
        next_retry_at=now,
        created_at=now,
    )
    assert outbox.id == outbox_id
    assert outbox.document_id == doc_id
    assert outbox.status == "pending"
    assert outbox.retry_count == 0


def test_assistant_deletion_status():
    asst = Assistant(
        user_id=uuid.uuid4(),
        name="RH Bot",
        slug="rh-bot",
        openrouter_api_key_encrypted="encrypted-test-key",
        deletion_status="active",
    )
    assert asst.deletion_status == "active"


def test_document_chunk_has_document_id():
    chunk = DocumentChunk(
        document_id=uuid.uuid4(),
        chunk_index=0,
        content="Texto de exemplo",
    )
    assert hasattr(chunk, "document_id")
    assert not hasattr(chunk, "document_version_id")


def test_document_response_schema():
    doc_id = uuid.uuid4()
    asst_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    resp = DocumentResponse(
        id=doc_id,
        assistant_id=asst_id,
        name="contrato.pdf",
        upload_status="uploaded",
        file_size=2048,
        mime_type="application/pdf",
        processing_status="indexed",
        processing_error=None,
        created_at=now,
        updated_at=now,
    )
    assert resp.upload_status == "uploaded"
    assert resp.processing_status == "indexed"
    assert not hasattr(resp, "current_version_number")

