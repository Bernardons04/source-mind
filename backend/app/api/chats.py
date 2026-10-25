import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload
from app.database import get_db
from app.auth.dependencies import get_current_user
from app.auth.jwt import AuthenticatedUser
from app.models.all_models import Assistant, Chat, Message, MessageEvent, MessageCitation, DocumentChunk
from app.schemas.all_schemas import (
    ChatCreate,
    ChatResponse,
    MessageCreate,
    MessageResponse,
    MessageCitationResponse,
    MessageEventResponse,
)
from app.services.crypto import decrypt_api_key
from app.services.openrouter import OpenRouterClient
from app.services.rag_workflow import rag_workflow

router = APIRouter(prefix="/assistants/{slug}/chats", tags=["chats"])


async def _get_user_assistant(slug: str, user: AuthenticatedUser, db: AsyncSession) -> Assistant:
    stmt = select(Assistant).where(and_(Assistant.user_id == user.id, Assistant.slug == slug))
    res = await db.execute(stmt)
    assistant = res.scalars().first()
    if not assistant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assistant not found")
    return assistant


async def _get_user_chat(chat_id: uuid.UUID, assistant_id: uuid.UUID, db: AsyncSession) -> Chat:
    stmt = select(Chat).where(and_(Chat.id == chat_id, Chat.assistant_id == assistant_id))
    res = await db.execute(stmt)
    chat = res.scalars().first()
    if not chat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    return chat


@router.get("", response_model=List[ChatResponse])
async def list_chats(
    slug: str,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lists chats belonging to the assistant."""
    assistant = await _get_user_assistant(slug, user, db)
    stmt = select(Chat).where(Chat.assistant_id == assistant.id).order_by(Chat.updated_at.desc())
    res = await db.execute(stmt)
    chats = res.scalars().all()
    return [
        ChatResponse(
            id=c.id,
            assistant_id=c.assistant_id,
            title=c.title,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
        for c in chats
    ]


@router.post("", response_model=ChatResponse, status_code=status.HTTP_201_CREATED)
async def create_chat(
    slug: str,
    payload: ChatCreate,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Creates a new conversation session for the assistant."""
    assistant = await _get_user_assistant(slug, user, db)
    new_chat = Chat(
        assistant_id=assistant.id,
        title=payload.title or "New Chat",
    )
    db.add(new_chat)
    await db.commit()
    await db.refresh(new_chat)
    return ChatResponse(
        id=new_chat.id,
        assistant_id=new_chat.assistant_id,
        title=new_chat.title,
        created_at=new_chat.created_at,
        updated_at=new_chat.updated_at,
    )


@router.get("/{chat_id}/messages", response_model=List[MessageResponse])
async def get_chat_messages(
    slug: str,
    chat_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieves chronological messages of a chat with citations and execution events."""
    assistant = await _get_user_assistant(slug, user, db)
    chat = await _get_user_chat(chat_id, assistant.id, db)

    stmt = (
        select(Message)
        .where(Message.chat_id == chat.id)
        .options(
            selectinload(Message.citations),
            selectinload(Message.events),
        )
        .order_by(Message.created_at.asc())
    )
    res = await db.execute(stmt)
    messages = res.scalars().all()

    result = []
    for m in messages:
        citations = [
            MessageCitationResponse(
                id=c.id,
                quote=c.quote,
                document_chunk_id=c.document_chunk_id,
                document_name=c.metadata_.get("document_name", "Document"),
                metadata=c.metadata_ or {},
                created_at=c.created_at,
            )
            for c in m.citations
        ]
        events = [
            MessageEventResponse(
                id=e.id,
                sequence_number=e.sequence_number,
                event_type=e.event_type,
                payload=e.payload or {},
                created_at=e.created_at,
            )
            for e in m.events
        ]
        result.append(
            MessageResponse(
                id=m.id,
                chat_id=m.chat_id,
                role=m.role,
                content=m.content,
                created_at=m.created_at,
                citations=citations,
                events=events,
            )
        )
    return result


@router.post("/{chat_id}/messages", response_model=MessageResponse)
async def send_message(
    slug: str,
    chat_id: uuid.UUID,
    payload: MessageCreate,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Sends a question, executes the LangGraph RAG workflow with hybrid search,
    persists citations and execution events, and returns the grounded answer.
    """
    assistant = await _get_user_assistant(slug, user, db)
    chat = await _get_user_chat(chat_id, assistant.id, db)

    # 1. Save User Message
    user_msg = Message(
        chat_id=chat.id,
        role="user",
        content=payload.content.strip(),
    )
    db.add(user_msg)

    # If first message or default title, rename chat intelligently
    if chat.title in ("New Chat", "Untitled Chat"):
        chat.title = payload.content.strip()[:45]

    await db.commit()
    await db.refresh(user_msg)

    # 2. Retrieve recent message history
    history_stmt = (
        select(Message)
        .where(Message.chat_id == chat.id)
        .order_by(Message.created_at.desc())
        .limit(6)
    )
    history_res = await db.execute(history_stmt)
    recent_msgs = list(reversed(history_res.scalars().all()))
    chat_history = [{"role": m.role, "content": m.content} for m in recent_msgs]

    # 3. Prepare OpenRouter client with decrypted assistant key
    plain_key = decrypt_api_key(assistant.openrouter_api_key_encrypted)
    openrouter = OpenRouterClient(api_key=plain_key)

    # 4. Initialize LangGraph State and invoke workflow
    initial_state = {
        "query": payload.content.strip(),
        "assistant_id": str(assistant.id),
        "assistant_name": assistant.name,
        "instructions": assistant.instructions or {},
        "chat_history": chat_history,
        "db": db,
        "openrouter": openrouter,
        "sequence": 0,
        "events": [],
        "analyzed_keywords": [],
        "retrieved_chunks": [],
        "is_sufficient": False,
        "has_contradiction": False,
        "abstention_reason": None,
        "response_text": "",
        "citations": [],
    }

    final_state = await rag_workflow.ainvoke(initial_state)

    # 5. Persist Assistant Message
    assistant_msg = Message(
        chat_id=chat.id,
        role="assistant",
        content=final_state.get("response_text", ""),
    )
    db.add(assistant_msg)
    await db.commit()
    await db.refresh(assistant_msg)

    # 6. Persist Citations
    created_citations = []
    for c in final_state.get("citations", []):
        try:
            chunk_uuid = uuid.UUID(c["chunk_id"])
            citation_metadata = {
                **(c.get("metadata") or {}),
                "document_name": c.get("document_name", "Document"),
            }
            db_cit = MessageCitation(
                message_id=assistant_msg.id,
                document_chunk_id=chunk_uuid,
                quote=c["quote"],
                metadata_=citation_metadata,
            )
            db.add(db_cit)
            created_citations.append(db_cit)
        except Exception as e:
            print(f"Citation persistence note: {e}")

    # 7. Persist Structured Execution Events
    created_events = []
    for ev in final_state.get("events", []):
        db_ev = MessageEvent(
            message_id=assistant_msg.id,
            sequence_number=ev["sequence_number"],
            event_type=ev["event_type"],
            payload=ev["payload"],
        )
        db.add(db_ev)
        created_events.append(db_ev)

    await db.commit()

    # Re-fetch for clean response
    return MessageResponse(
        id=assistant_msg.id,
        chat_id=assistant_msg.chat_id,
        role=assistant_msg.role,
        content=assistant_msg.content,
        created_at=assistant_msg.created_at,
        citations=[
            MessageCitationResponse(
                id=c.id,
                quote=c.quote,
                document_chunk_id=c.document_chunk_id,
                document_name=c.metadata_.get("document_name", "Document"),
                metadata=c.metadata_ or {},
                created_at=c.created_at,
            )
            for c in created_citations
        ],
        events=[
            MessageEventResponse(
                id=e.id,
                sequence_number=e.sequence_number,
                event_type=e.event_type,
                payload=e.payload or {},
                created_at=e.created_at,
            )
            for e in created_events
        ],
    )


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(
    slug: str,
    chat_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Deletes a chat and its messages, citations, and events."""
    assistant = await _get_user_assistant(slug, user, db)
    chat = await _get_user_chat(chat_id, assistant.id, db)
    await db.delete(chat)
    await db.commit()
    return None
