import httpx
from fastapi import HTTPException, status
from app.config import settings


class SupabaseStorageService:
    """
    Manages private document storage in Supabase Storage.
    Never uses service_role key; relies on the user's bearer token
    so that Supabase Storage RLS policies are strictly enforced.
    """

    def __init__(self):
        self.base_url = f"{settings.SUPABASE_URL.rstrip('/')}/storage/v1"
        self.bucket = settings.SUPABASE_STORAGE_BUCKET

    def _headers(self, user_token: str) -> dict:
        return {
            "Authorization": f"Bearer {user_token}",
            "apikey": settings.SUPABASE_ANON_KEY,
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
        url = f"{self.base_url}/object/{self.bucket}/{storage_path}"
        headers = self._headers(user_token)
        headers["Content-Type"] = mime_type
        # Allow overwriting same version if needed (upsert)
        headers["x-upsert"] = "true"

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, content=file_bytes, headers=headers)
            if resp.status_code not in (200, 201):
                # Fallback check: if bucket doesn't exist yet, try to create it or report clear message
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Storage upload error: {resp.text}",
                )
        return storage_path

    async def download_file(self, user_token: str, storage_path: str) -> bytes:
        """
        Downloads a private document from the storage bucket.
        """
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

    async def delete_file(self, user_token: str, storage_path: str) -> bool:
        """
        Deletes a document from the storage bucket.
        """
        url = f"{self.base_url}/object/{self.bucket}/{storage_path}"
        headers = self._headers(user_token)

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.delete(url, headers=headers)
            return resp.status_code in (200, 204)


storage_service = SupabaseStorageService()
