from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from app.database import get_db
from app.auth.dependencies import get_current_user
from app.auth.jwt import AuthenticatedUser
from app.models.all_models import Assistant, Document
from app.schemas.all_schemas import AssistantCreate, AssistantUpdate, AssistantResponse
from app.services.crypto import encrypt_api_key

router = APIRouter(prefix="/assistants", tags=["assistants"])


@router.get("", response_model=List[AssistantResponse])
async def list_assistants(
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lists all assistants belonging to the authenticated user."""
    stmt = (
        select(
            Assistant,
            func.count(Document.id).label("doc_count")
        )
        .outerjoin(Document, Assistant.id == Document.assistant_id)
        .where(Assistant.user_id == user.id)
        .group_by(Assistant.id)
        .order_by(Assistant.created_at.desc())
    )
    result = await db.execute(stmt)
    rows = result.all()

    assistants = []
    for assistant, doc_count in rows:
        assistants.append(
            AssistantResponse(
                id=assistant.id,
                name=assistant.name,
                slug=assistant.slug,
                instructions=assistant.instructions or {},
                has_api_key=bool(assistant.openrouter_api_key_encrypted),
                document_count=doc_count,
                created_at=assistant.created_at,
                updated_at=assistant.updated_at,
            )
        )
    return assistants


@router.post("", response_model=AssistantResponse, status_code=status.HTTP_201_CREATED)
async def create_assistant(
    payload: AssistantCreate,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Creates a new assistant for the authenticated user."""
    # Check for existing slug for this user
    existing_stmt = select(Assistant).where(
        and_(Assistant.user_id == user.id, Assistant.slug == payload.slug)
    )
    res = await db.execute(existing_stmt)
    if res.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An assistant with URL identifier '{payload.slug}' already exists in your workspace.",
        )

    encrypted_key = encrypt_api_key(payload.openrouter_api_key)

    new_assistant = Assistant(
        user_id=user.id,
        name=payload.name.strip(),
        slug=payload.slug.strip().lower(),
        instructions=payload.instructions or {"prompt": ""},
        openrouter_api_key_encrypted=encrypted_key,
    )
    db.add(new_assistant)
    await db.commit()
    await db.refresh(new_assistant)

    return AssistantResponse(
        id=new_assistant.id,
        name=new_assistant.name,
        slug=new_assistant.slug,
        instructions=new_assistant.instructions or {},
        has_api_key=True,
        document_count=0,
        created_at=new_assistant.created_at,
        updated_at=new_assistant.updated_at,
    )


@router.get("/{slug}", response_model=AssistantResponse)
async def get_assistant(
    slug: str,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieves an assistant by slug for the authenticated user."""
    stmt = (
        select(Assistant, func.count(Document.id).label("doc_count"))
        .outerjoin(Document, Assistant.id == Document.assistant_id)
        .where(and_(Assistant.user_id == user.id, Assistant.slug == slug))
        .group_by(Assistant.id)
    )
    res = await db.execute(stmt)
    row = res.first()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assistant not found")

    assistant, doc_count = row
    return AssistantResponse(
        id=assistant.id,
        name=assistant.name,
        slug=assistant.slug,
        instructions=assistant.instructions or {},
        has_api_key=bool(assistant.openrouter_api_key_encrypted),
        document_count=doc_count,
        created_at=assistant.created_at,
        updated_at=assistant.updated_at,
    )


@router.put("/{slug}", response_model=AssistantResponse)
async def update_assistant(
    slug: str,
    payload: AssistantUpdate,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Updates assistant configuration or rotates OpenRouter API key."""
    stmt = select(Assistant).where(and_(Assistant.user_id == user.id, Assistant.slug == slug))
    res = await db.execute(stmt)
    assistant = res.scalars().first()
    if not assistant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assistant not found")

    if payload.name is not None:
        assistant.name = payload.name.strip()
    if payload.instructions is not None:
        assistant.instructions = payload.instructions
    if payload.openrouter_api_key:
        assistant.openrouter_api_key_encrypted = encrypt_api_key(payload.openrouter_api_key)

    await db.commit()
    await db.refresh(assistant)

    # Count docs
    doc_count_res = await db.execute(
        select(func.count(Document.id)).where(Document.assistant_id == assistant.id)
    )
    doc_count = doc_count_res.scalar() or 0

    return AssistantResponse(
        id=assistant.id,
        name=assistant.name,
        slug=assistant.slug,
        instructions=assistant.instructions or {},
        has_api_key=bool(assistant.openrouter_api_key_encrypted),
        document_count=doc_count,
        created_at=assistant.created_at,
        updated_at=assistant.updated_at,
    )


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_assistant(
    slug: str,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Deletes an assistant and all associated documents and chats."""
    stmt = select(Assistant).where(and_(Assistant.user_id == user.id, Assistant.slug == slug))
    res = await db.execute(stmt)
    assistant = res.scalars().first()
    if not assistant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assistant not found")

    await db.delete(assistant)
    await db.commit()
    return None
