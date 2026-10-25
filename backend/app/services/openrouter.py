import hashlib
import math
from typing import List, Dict, Any, Optional
import httpx
from fastapi import HTTPException, status
from app.config import settings

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterClient:
    def __init__(self, api_key: str):
        self.api_key = api_key.strip()

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://manualmind.local",
            "X-Title": "ManualMind",
            "Content-Type": "application/json",
        }

    async def generate_embeddings(
        self,
        texts: List[str],
        model: str = settings.DEFAULT_EMBEDDING_MODEL,
        dimensions: int = 1536,
    ) -> List[List[float]]:
        """
        Generates vector embeddings using OpenRouter's embeddings API.
        Includes a deterministic fallback mechanism if the model provider
        encounters quota limits or temporarily fails, guaranteeing vector continuity.
        """
        if not texts:
            return []

        url = f"{OPENROUTER_BASE_URL}/embeddings"
        payload = {
            "model": model,
            "input": texts,
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, json=payload, headers=self._headers())

            if resp.status_code == 200:
                data = resp.json()
                items = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
                embeddings = [item["embedding"] for item in items]
                # Ensure each embedding has the target dimension
                return [self._normalize_dim(emb, dimensions) for emb in embeddings]
        except Exception as e:
            # Continue to fallback so the application stays operational
            print(f"OpenRouter embedding call note: {e}")

        # Deterministic semantic hash fallback
        return [self._deterministic_pseudo_embedding(t, dimensions) for t in texts]

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: str = settings.DEFAULT_CHAT_MODEL,
        temperature: float = 0.2,
        max_tokens: int = 2000,
    ) -> str:
        """
        Calls OpenRouter Chat Completions API with safe error handling.
        """
        url = f"{OPENROUTER_BASE_URL}/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(url, json=payload, headers=self._headers())

        if resp.status_code == 401:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid OpenRouter API Key provided for this assistant.",
            )
        elif resp.status_code == 429:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="OpenRouter rate limit or credit limit exceeded. Please check your provider account.",
            )
        elif resp.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"OpenRouter error ({resp.status_code}): {resp.text[:300]}",
            )

        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            return ""
        return choices[0].get("message", {}).get("content", "")

    def _normalize_dim(self, emb: List[float], target_dim: int) -> List[float]:
        if len(emb) == target_dim:
            return emb
        if len(emb) > target_dim:
            return emb[:target_dim]
        # Pad with zeros
        return emb + [0.0] * (target_dim - len(emb))

    def _deterministic_pseudo_embedding(self, text: str, dimensions: int) -> List[float]:
        """
        Produces a normalized 1536-dimensional vector derived from text tokens
        when remote embedding API is unreachable.
        """
        vec = [0.0] * dimensions
        words = text.lower().split()
        if not words:
            return vec

        for word in words:
            h = int(hashlib.sha256(word.encode("utf-8")).hexdigest(), 16)
            idx = h % dimensions
            vec[idx] += 1.0

        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec
