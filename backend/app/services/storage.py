import uuid
import httpx
from fastapi import HTTPException, status
from app.config import settings


def validate_storage_path(storage_path: str) -> bool:
    """
    Path Traversal Guard:
    Strictly verifies that storage_path follows the pattern:
    {assistant_id}/{document_id}/{filename}
    where assistant_id and document_id are valid UUIDs, and filename has no directory traversal characters.
    """
    if not storage_path or ".." in storage_path or "\\" in storage_path:
        return False
    parts = storage_path.split("/")
    if len(parts) != 3:
        return False
    assistant_id_str, doc_id_str, filename = parts
    try:
        uuid.UUID(assistant_id_str)
        uuid.UUID(doc_id_str)
    except (ValueError, TypeError):
        return False
    if not filename or "/" in filename:
        return False
    return True


class SupabaseStorageService:
    """
    Manages document storage in Supabase Storage.
    - Endpoints called by user requests rely on the user's bearer token.
    - Asynchronous workers use SUPABASE_SECRET_KEY for background tasks (with Path Traversal Guard).
    """

    def __init__(self):
        self.base_url = f"{settings.SUPABASE_URL.rstrip('/')}/storage/v1"
        self.bucket = settings.SUPABASE_STORAGE_BUCKET

    def _headers(self, user_token: str) -> dict:
        apikey = settings.SUPABASE_PUBLISHABLE_KEY or settings.SUPABASE_SECRET_KEY
        headers = {
            "Authorization": f"Bearer {user_token}",
        }
        if apikey:
            headers["apikey"] = apikey
        return headers

    def _admin_headers(self) -> dict:
        if not settings.SUPABASE_SECRET_KEY:
            raise RuntimeError(
                "SUPABASE_SECRET_KEY is not configured. Administrative worker and storage operations "
                "require SUPABASE_SECRET_KEY. Fallback to publishable key is forbidden."
            )
        return {
            "Authorization": f"Bearer {settings.SUPABASE_SECRET_KEY}",
            "apikey": settings.SUPABASE_SECRET_KEY,
        }

    async def upload_file(
        self,
        user_token: str,
        storage_path: str,
        file_bytes: bytes,
        mime_type: str = "application/octet-stream",
    ) -> str:
        """
        Uploads an original document to the private bucket.
        """
        if not validate_storage_path(storage_path):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid storage path format.",
            )

        url = f"{self.base_url}/object/{self.bucket}/{storage_path}"
        headers = self._headers(user_token)
        headers["Content-Type"] = mime_type

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, content=file_bytes, headers=headers)
            if resp.status_code not in (200, 201):
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Storage upload error: {resp.text}",
                )
        return storage_path

    async def download_file(self, user_token: str, storage_path: str) -> bytes:
        """
        Downloads a private document from the storage bucket using user token.
        """
        if not validate_storage_path(storage_path):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid storage path format.",
            )

        url = f"{self.base_url}/object/{self.bucket}/{storage_path}"
        headers = self._headers(user_token)

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Could not retrieve file from storage: {resp.text}",
                )
            return resp.content

    async def download_file_admin(self, storage_path: str) -> bytes:
        """
        Downloads a document for the background worker using administrative credentials.
        Protected by Path Traversal Guard.
        """
        if not validate_storage_path(storage_path):
            raise ValueError(f"Path Traversal Guard: Invalid storage path '{storage_path}'")

        url = f"{self.base_url}/object/{self.bucket}/{storage_path}"
        headers = self._admin_headers()

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Storage worker download failed (status {resp.status_code}): {resp.text}"
                )
            return resp.content

    async def delete_file(self, user_token: str, storage_path: str) -> bool:
        """
        Deletes a document from the storage bucket using user token.
        """
        if not validate_storage_path(storage_path):
            return False

        url = f"{self.base_url}/object/{self.bucket}/{storage_path}"
        headers = self._headers(user_token)

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.delete(url, headers=headers)
            return resp.status_code in (200, 204)

    async def delete_file_admin(self, storage_path: str) -> bool:
        """
        Deletes a document using administrative credentials (used for dual-write rollback compensation
        and orphan reconciliation). Protected by Path Traversal Guard.
        Returns True if deleted or already absent (HTTP 200, 204, 404).
        """
        if not validate_storage_path(storage_path):
            raise ValueError(f"Path Traversal Guard: Invalid storage path '{storage_path}'")

        url = f"{self.base_url}/object/{self.bucket}/{storage_path}"
        headers = self._admin_headers()

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.delete(url, headers=headers)
            # 200/204: deleted, 404: already absent/cleaned
            return resp.status_code in (200, 204, 404)


storage_service = SupabaseStorageService()
