import logging
from typing import Optional, Protocol

import httpx

from backend.config import ANTHROPIC_API_KEY, LLM_TIMEOUT_S, OLLAMA_BASE_URL, OPENAI_API_KEY

log = logging.getLogger(__name__)


class LLMError(Exception):
    pass


class LLMClient(Protocol):
    def complete(self, system: str, user: str, *, model: str, temperature: float, max_tokens: int) -> str: ...


class OpenAIClient:
    def __init__(self, api_key: str, base_url: Optional[str] = None, timeout: float = LLM_TIMEOUT_S, client=None):
        self._api_key, self._base_url, self._timeout, self._client = api_key, base_url, timeout, client

    def _get(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self._api_key, base_url=self._base_url, timeout=self._timeout, max_retries=2)
        return self._client

    def complete(self, system: str, user: str, *, model: str, temperature: float, max_tokens: int) -> str:
        params = {"model": model, "temperature": temperature, "max_tokens": max_tokens,
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        client = self._get()
        try:
            try:
                resp = client.chat.completions.create(**params)
            except Exception as e:
                detail = str(e)
                if "max_completion_tokens" not in detail and "temperature" not in detail:
                    raise
                params["max_completion_tokens"] = params.pop("max_tokens")
                params.pop("temperature", None)
                resp = client.chat.completions.create(**params)
        except Exception as e:
            raise LLMError(f"LLM request failed ({type(e).__name__}, status {getattr(e, 'status_code', 'n/a')})") from e
        return resp.choices[0].message.content or ""


class AnthropicClient:
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str, timeout: float = LLM_TIMEOUT_S, transport: Optional[httpx.BaseTransport] = None):
        self._api_key, self._timeout, self._transport = api_key, timeout, transport

    def complete(self, system: str, user: str, *, model: str, temperature: float, max_tokens: int) -> str:
        body = {"model": model, "max_tokens": max_tokens, "temperature": temperature, "system": system,
                "messages": [{"role": "user", "content": user}]}
        headers = {"x-api-key": self._api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as http:
                resp = http.post(self.URL, json=body, headers=headers)
            resp.raise_for_status()
            return "".join(b.get("text", "") for b in resp.json().get("content", []) if b.get("type") == "text")
        except httpx.HTTPStatusError as e:
            raise LLMError(f"LLM request failed (HTTP {e.response.status_code})") from e
        except Exception as e:
            raise LLMError(f"LLM request failed ({type(e).__name__})") from e


_clients: dict[str, LLMClient] = {}


def build_client(provider: str) -> LLMClient:
    if provider in _clients:
        return _clients[provider]
    if provider == "openai":
        if not OPENAI_API_KEY:
            raise LLMError("OPENAI_API_KEY is not set")
        client: LLMClient = OpenAIClient(OPENAI_API_KEY)
    elif provider == "anthropic":
        if not ANTHROPIC_API_KEY:
            raise LLMError("ANTHROPIC_API_KEY is not set")
        client = AnthropicClient(ANTHROPIC_API_KEY)
    elif provider == "ollama":
        client = OpenAIClient("ollama", base_url=OLLAMA_BASE_URL)
    else:
        raise LLMError(f"Unsupported LLM provider '{provider}'")
    _clients[provider] = client
    return client
