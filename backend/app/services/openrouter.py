import math
from typing import List, Dict, Any, Optional
import httpx
from fastapi import HTTPException, status
from app.config import settings

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterError(Exception):
    """Base exception for OpenRouter operations."""
    pass


class OpenRouterTransientError(OpenRouterError):
    """
    Transient errors that should be retried by the background worker:
    - HTTP 429 (rate/credit limit)
    - HTTP 5xx (500, 502, 503, 504 server errors)
    - Network timeouts, connect errors, socket drops
    """
    pass


class OpenRouterNonRecoverableError(OpenRouterError):
    """
    Non-recoverable client/credential errors that will not succeed upon retry:
    - HTTP 400 (bad request, invalid model or payload)
    - HTTP 401 (unauthorized / invalid API key)
    - HTTP 403 (forbidden / access denied)
    - Other 4xx status codes (except 429)
    """
    pass


class OpenRouterInvalidResponseError(OpenRouterError):
    """
    Errors indicating malformed or invalid response payloads:
    - Malformed JSON
    - Count mismatch (number of returned embeddings != number of input texts)
    - Ambiguous or corrupt item indexing
    - Dimension mismatch (dimensions != expected_dimensions)
    - Non-numeric or non-finite values (NaN, Infinity, -Infinity)
    """
    pass


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
        Enforces strict validation:
        - Classifies network/HTTP status errors into transient vs non-recoverable.
        - Verifies exact 1:1 mapping between input texts and returned embeddings.
        - Validates dimensions strictly without padding or truncation.
        - Rejects non-numeric, NaN, and infinite values.
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
        except httpx.TransportError as net_err:
            raise OpenRouterTransientError(
                f"Falha de transporte/timeout no OpenRouter ({type(net_err).__name__}): {net_err}"
            ) from net_err

        status_code = resp.status_code
        if status_code != 200:
            err_body = resp.text[:300]
            if status_code == 429 or status_code >= 500:
                raise OpenRouterTransientError(
                    f"Erro transitório do OpenRouter ({status_code}): {err_body}"
                )
            elif status_code in (400, 401, 403) or (400 <= status_code < 500):
                raise OpenRouterNonRecoverableError(
                    f"Erro não recuperável do OpenRouter ({status_code}): {err_body}"
                )
            else:
                raise OpenRouterTransientError(
                    f"Status HTTP inesperado do OpenRouter ({status_code}): {err_body}"
                )

        try:
            data = resp.json()
        except Exception as json_err:
            raise OpenRouterInvalidResponseError(
                f"Resposta do OpenRouter não é um JSON válido: {json_err}"
            ) from json_err

        if not isinstance(data, dict):
            raise OpenRouterInvalidResponseError(
                f"Estrutura da resposta do OpenRouter inválida: esperado objeto JSON, recebido {type(data).__name__}"
            )

        raw_items = data.get("data")
        if not isinstance(raw_items, list):
            raise OpenRouterInvalidResponseError(
                "Resposta do OpenRouter não contém lista 'data' de embeddings."
            )

        if len(raw_items) != len(texts):
            raise OpenRouterInvalidResponseError(
                f"Contagem incorreta de embeddings: esperado {len(texts)}, recebido {len(raw_items)}"
            )

        seen_indices = set()
        for idx, item in enumerate(raw_items):
            if not isinstance(item, dict):
                raise OpenRouterInvalidResponseError(
                    f"Item no índice {idx} não é um objeto JSON válido: {type(item).__name__}"
                )
            if "index" not in item:
                raise OpenRouterInvalidResponseError(
                    f"Item {idx} não contém o campo 'index'."
                )
            raw_idx = item["index"]
            # In Python, bool is a subclass of int (isinstance(True, int) is True).
            # We explicitly reject booleans and non-integers (such as float 0.0).
            if isinstance(raw_idx, bool) or not isinstance(raw_idx, int):
                raise OpenRouterInvalidResponseError(
                    f"Índice do item {idx} não é um inteiro estrito: {type(raw_idx).__name__} ({raw_idx})"
                )
            if raw_idx in seen_indices:
                raise OpenRouterInvalidResponseError(
                    f"Índice duplicado detectado na resposta do OpenRouter: {raw_idx}"
                )
            seen_indices.add(raw_idx)

        expected_indices_set = set(range(len(texts)))
        if seen_indices != expected_indices_set:
            raise OpenRouterInvalidResponseError(
                f"Conjunto de índices do OpenRouter inválido ou fora do intervalo: esperado {expected_indices_set}, recebido {seen_indices}"
            )

        embeddings: List[List[float]] = []
        sorted_items = sorted(raw_items, key=lambda x: x["index"])
        for idx, item in enumerate(sorted_items):
            emb = item.get("embedding")
            if not isinstance(emb, list):
                raise OpenRouterInvalidResponseError(
                    f"Embedding no índice {idx} não é uma lista: {type(emb).__name__}"
                )

            if len(emb) != dimensions:
                raise OpenRouterInvalidResponseError(
                    f"Dimensão incorreta no embedding {idx}: esperado {dimensions}, recebido {len(emb)}"
                )

            # Strictly validate every element is a finite number
            for comp_idx, val in enumerate(emb):
                if not isinstance(val, (int, float)) or isinstance(val, bool):
                    raise OpenRouterInvalidResponseError(
                        f"Embedding {idx}, componente {comp_idx} não é numérico ({type(val).__name__}: {val})"
                    )
                if math.isnan(val) or math.isinf(val):
                    raise OpenRouterInvalidResponseError(
                        f"Embedding {idx}, componente {comp_idx} contém valor não finito ({val})"
                    )

            embeddings.append(emb)

        return embeddings

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
