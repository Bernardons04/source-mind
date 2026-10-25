from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.auth.dependencies import get_current_user
from app.auth.jwt import AuthenticatedUser
from app.models.all_models import UserPreference
from app.schemas.all_schemas import UserPreferenceUpdate, UserPreferenceResponse

router = APIRouter(prefix="/preferences", tags=["preferences"])


@router.get("", response_model=UserPreferenceResponse)
async def get_preferences(
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieves current user's UI preferences (language & theme)."""
    stmt = select(UserPreference).where(UserPreference.user_id == user.id)
    res = await db.execute(stmt)
    pref = res.scalars().first()
    if not pref:
        pref = UserPreference(user_id=user.id, language="en", theme="dark")
        db.add(pref)
        await db.commit()
        await db.refresh(pref)

    return UserPreferenceResponse(
        user_id=pref.user_id,
        language=pref.language,
        theme=pref.theme,
        updated_at=pref.updated_at,
    )


@router.put("", response_model=UserPreferenceResponse)
async def update_preferences(
    payload: UserPreferenceUpdate,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Updates user language or theme preference."""
    stmt = select(UserPreference).where(UserPreference.user_id == user.id)
    res = await db.execute(stmt)
    pref = res.scalars().first()
    if not pref:
        pref = UserPreference(
            user_id=user.id,
            language=payload.language or "en",
            theme=payload.theme or "dark",
        )
        db.add(pref)
    else:
        if payload.language is not None:
            pref.language = payload.language
        if payload.theme is not None:
            pref.theme = payload.theme

    await db.commit()
    await db.refresh(pref)
    return UserPreferenceResponse(
        user_id=pref.user_id,
        language=pref.language,
        theme=pref.theme,
        updated_at=pref.updated_at,
    )
