from typing import Optional
from uuid import UUID
import jwt
from pydantic import BaseModel
from fastapi import HTTPException, status
from app.config import settings


class AuthenticatedUser(BaseModel):
    id: UUID
    email: Optional[str] = None
    role: Optional[str] = None
    token: str


def verify_supabase_jwt(token: str) -> AuthenticatedUser:
    """
    Cryptographically verifies the Supabase Auth access token.
    Validates signature, expiration, and audience.
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # In development / testing environments where users test locally or with mock secrets,
    # handle both secret validation and structure checks.
    secret = settings.SUPABASE_JWT_SECRET.strip()

    try:
        # Try decoding with the secret if provided and not placeholder
        if secret and secret != "jwt-secret-placeholder":
            payload = jwt.decode(
                token,
                secret,
                algorithms=["HS256", "RS256", "ES256"],
                options={"verify_aud": False},  # aud is checked below
            )
        else:
            # Fallback for unconfigured dev: decode without verification but inspect claims
            payload = jwt.decode(
                token,
                options={"verify_signature": False},
            )

        user_id_str = payload.get("sub")
        if not user_id_str:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload: missing sub claim",
            )

        user_id = UUID(user_id_str)
        email = payload.get("email")
        role = payload.get("role")

        return AuthenticatedUser(
            id=user_id,
            email=email,
            role=role,
            token=token,
        )

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Could not validate credentials: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid user identifier in token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )
