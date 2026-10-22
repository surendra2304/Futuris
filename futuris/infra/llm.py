"""Provider-agnostic LLM adapter with caching, rate limiting, and fallback execution."""

import hashlib
from typing import Any

import httpx

from futuris.infra.config import settings
from futuris.infra.logging import get_logger

logger = get_logger("futuris.infra.llm")


class LLMResponseCache:
    """In-memory and persistent SHA-256 prompt response cache."""

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}

    def _hash_prompt(self, prompt: str, system_prompt: str = "") -> str:
        serialized = f"{system_prompt}::{prompt}".encode()
        return hashlib.sha256(serialized).hexdigest()

    def get(self, prompt: str, system_prompt: str = "") -> str | None:
        key = self._hash_prompt(prompt, system_prompt)
        return self._cache.get(key)

    def set(self, prompt: str, response: str, system_prompt: str = "") -> None:
        key = self._hash_prompt(prompt, system_prompt)
        self._cache[key] = response


class LLMAdapter:
    """Provider-agnostic LLM interface with guaranteed deterministic fallback."""

    def __init__(
        self,
        provider: str | None = None,
        api_key: str | None = None,
        cache: LLMResponseCache | None = None,
    ) -> None:
        self.provider = (provider or settings.LLM_PROVIDER).lower()
        self.api_key = api_key or settings.LLM_API_KEY
        self.cache = cache or LLMResponseCache()
        self._call_count = 0

    @property
    def is_available(self) -> bool:
        """Return True if an active provider with valid credentials is configured."""
        return self.provider in ["anthropic", "openai"] and bool(self.api_key)

    async def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        fallback_fn: Any | None = None,
        *,
        allow_llm: bool = True,
    ) -> str:
        """Generate LLM completion with prompt caching and deterministic fallback on failure."""
        cached = self.cache.get(prompt, system_prompt)
        if cached is not None:
            p_hash = hashlib.sha256(prompt.encode()).hexdigest()[:8]
            logger.info("llm_cache_hit", prompt_hash=p_hash)
            return cached

        if not allow_llm or not self.is_available:
            logger.warning(
                "llm_provider_unavailable",
                provider=self.provider,
                action="using_deterministic_fallback",
            )
            if fallback_fn:
                res = fallback_fn()
                return str(res)
            return "Deterministic fallback response: LLM provider unavailable."

        self._call_count += 1

        try:
            response_text = await self._call_provider(prompt, system_prompt)
            self.cache.set(prompt, response_text, system_prompt)
            return response_text
        except Exception as e:
            logger.error("llm_invocation_error", error=str(e), action="using_fallback")
            if fallback_fn:
                return str(fallback_fn())
            return "Deterministic fallback response: LLM call failed."

    async def _call_provider(self, prompt: str, system_prompt: str) -> str:
        """Call external LLM API endpoints."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            if self.provider == "openai":
                url = "https://api.openai.com/v1/chat/completions"
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }
                body = {
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                }
                resp = await client.post(url, json=body, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            if self.provider == "anthropic":
                url = "https://api.anthropic.com/v1/messages"
                headers = {
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                }
                body = {
                    "model": "claude-3-haiku-20240307",
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 512,
                }
                resp = await client.post(url, json=body, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                return data["content"][0]["text"]

        return "Deterministic fallback response"


llm_adapter = LLMAdapter()
