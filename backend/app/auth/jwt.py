from typing import Optional
from uuid import UUID
import jwt
from jwt import PyJWKClient
from pydantic import BaseModel
from fastapi import HTTPException, status
from app.config import settings

# Cached JWKS client
_jwks_client: Optional[PyJWKClient] = None


def get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        jwks_url = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/.well-known/jwks.json"
        # Cache public keys for 1 hour
        _jwks_client = PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=3600)
    return _jwks_client


class AuthenticatedUser(BaseModel):
    id: UUID
    email: Optional[str] = None
    role: Optional[str] = None
    token: str


def verify_supabase_jwt(token: str) -> AuthenticatedUser:
    """
    Cryptographically verifies the Supabase Auth access token.
    Prioritizes asymmetric public key validation via Supabase's JWKS endpoint
    (no SUPABASE_JWT_SECRET required).
    If an HS256 token is provided, uses SUPABASE_JWT_SECRET if configured.
    Always enforces signature, audience, and expiration verification.
    """
    if not token or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    clean_token = token.strip()

    try:
        # Inspect header to determine algorithm and key id
        unverified_header = jwt.get_unverified_header(clean_token)
        alg = unverified_header.get("alg", "RS256")

        payload: dict

        if alg in ("RS256", "ES256", "EdDSA", "ES384", "ES512", "RS384", "RS512"):
            # Asymmetric verification via Supabase JWKS public keys
            jwks = get_jwks_client()
            signing_key = jwks.get_signing_key_from_jwt(clean_token)
            payload = jwt.decode(
                clean_token,
                signing_key.key,
                algorithms=[alg],
                audience="authenticated",
            )
        elif alg == "HS256":
            # Symmetric verification fallback for legacy projects with shared secret
            secret = (settings.SUPABASE_JWT_SECRET or "").strip()
            if not secret:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token signed with HS256, but SUPABASE_JWT_SECRET is not configured on the server.",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            payload = jwt.decode(
                clean_token,
                secret,
                algorithms=["HS256"],
                audience="authenticated",
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Unsupported JWT signing algorithm: {alg}",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Extract and validate sub (user UUID)
        user_id_str = payload.get("sub")
        if not user_id_str:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing subject (sub) claim",
                headers={"WWW-Authenticate": "Bearer"},
            )

        user_id = UUID(user_id_str)
        email = payload.get("email")
        role = payload.get("role")

        return AuthenticatedUser(
            id=user_id,
            email=email,
            role=role,
            token=clean_token,
        )

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidAudienceError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token audience (must be 'authenticated')",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token cryptographic verification failed: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid user UUID in token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )
