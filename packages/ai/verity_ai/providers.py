"""Concrete model providers.

Four, sharing one HTTP shape and one contract: given a system prompt and a user
prompt, return parsed JSON. Nothing here knows what Verity does with the
answer, which is why swapping providers changes no behaviour anywhere else.

Prices are per million tokens and are used only for the local budget ceiling.
They drift; they are not billing, and the budget is deliberately conservative.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .base import (
    DEFAULT_TIMEOUT,
    MAX_ATTEMPTS,
    Completion,
    ProviderError,
    env,
    extract_json,
)

JSON_INSTRUCTION = (
    "Reply with a single JSON object and nothing else. No prose, no markdown "
    "fence, no explanation outside the JSON."
)


@dataclass
class HttpProvider:
    """Shared transport. Retries on a malformed reply, not on a refusal."""

    name: str
    model: str
    base_url: str
    api_key: str | None
    timeout: float = DEFAULT_TIMEOUT
    client: httpx.Client | None = None
    input_price: float = 0.0
    output_price: float = 0.0

    def available(self) -> bool:
        return bool(self.api_key)

    # -- subclass hooks ---------------------------------------------------
    def _endpoint(self) -> str:
        raise NotImplementedError

    def _headers(self) -> dict[str, str]:
        raise NotImplementedError

    def _payload(self, system: str, user: str) -> dict[str, Any]:
        raise NotImplementedError

    def _text_from(self, payload: dict[str, Any]) -> str:
        raise NotImplementedError

    def _usage_cost(self, payload: dict[str, Any]) -> float:
        usage = payload.get("usage") or {}
        prompt = float(usage.get("prompt_tokens") or usage.get("promptTokenCount") or 0)
        completion = float(
            usage.get("completion_tokens") or usage.get("candidatesTokenCount") or 0
        )
        return (prompt * self.input_price + completion * self.output_price) / 1_000_000

    # -- the one public method -------------------------------------------
    def complete_json(self, system: str, user: str, *, schema_hint: str = "") -> Completion:
        if not self.available():
            raise ProviderError(f"{self.name}: no API key configured")

        full_user = f"{user}\n\n{JSON_INSTRUCTION}"
        if schema_hint:
            full_user += f"\n\nThe JSON must match this shape:\n{schema_hint}"

        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self._post(self._payload(system, full_user))
            except httpx.HTTPError as exc:
                raise ProviderError(f"{self.name}: {exc}") from exc

            if response.status_code == 401:
                raise ProviderError(f"{self.name}: the API key was rejected (401)")
            if response.status_code == 429:
                raise ProviderError(f"{self.name}: rate limited (429)")
            if response.status_code >= 400:
                raise ProviderError(
                    f"{self.name}: HTTP {response.status_code} -- {response.text[:200]}"
                )

            body = response.json()
            try:
                text = self._text_from(body)
                return Completion(
                    data=extract_json(text), model=self.model, raw=text,
                    usd=self._usage_cost(body),
                )
            except ProviderError as exc:
                # A model that ignored the format instruction. Worth one more
                # attempt; not worth three round trips on a paid endpoint.
                last_error = exc
                if attempt == MAX_ATTEMPTS:
                    break
                full_user += (
                    "\n\nYour previous reply was not valid JSON. Reply with the "
                    "JSON object only."
                )

        raise ProviderError(f"{self.name}: no valid JSON after {MAX_ATTEMPTS} attempts "
                            f"({last_error})")

    def _post(self, payload: dict[str, Any]) -> httpx.Response:
        if self.client is not None:
            return self.client.post(
                self._endpoint(), json=payload, headers=self._headers(), timeout=self.timeout
            )
        with httpx.Client(timeout=self.timeout) as client:
            return client.post(self._endpoint(), json=payload, headers=self._headers())


class OpenAICompatibleProvider(HttpProvider):
    """OpenAI's chat-completions shape, which most providers now speak."""

    def _endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _payload(self, system: str, user: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }

    def _text_from(self, payload: dict[str, Any]) -> str:
        choices = payload.get("choices") or []
        if not choices:
            raise ProviderError(f"{self.name}: response contained no choices")
        return str(choices[0].get("message", {}).get("content") or "")


class OpenRouterProvider(OpenAICompatibleProvider):
    """OpenRouter. One key, many models, and a free tier worth using here."""

    def _headers(self) -> dict[str, str]:
        return {
            **super()._headers(),
            # OpenRouter asks callers to identify themselves.
            "HTTP-Referer": "https://github.com/akashdeb2105-art/Verity",
            "X-Title": "Verity",
        }


class GeminiProvider(HttpProvider):
    """Google AI Studio. A different request shape from everyone else."""

    def _endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/models/{self.model}:generateContent"

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self.api_key or "", "Content-Type": "application/json"}

    def _payload(self, system: str, user: str) -> dict[str, Any]:
        return {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        }

    def _text_from(self, payload: dict[str, Any]) -> str:
        candidates = payload.get("candidates") or []
        if not candidates:
            blocked = (payload.get("promptFeedback") or {}).get("blockReason")
            raise ProviderError(
                f"{self.name}: no candidates"
                + (f" (blocked: {blocked})" if blocked else "")
            )
        parts = (candidates[0].get("content") or {}).get("parts") or []
        return "".join(str(p.get("text", "")) for p in parts)

    def _usage_cost(self, payload: dict[str, Any]) -> float:
        usage = payload.get("usageMetadata") or {}
        prompt = float(usage.get("promptTokenCount") or 0)
        completion = float(usage.get("candidatesTokenCount") or 0)
        return (prompt * self.input_price + completion * self.output_price) / 1_000_000


class OllamaProvider(OpenAICompatibleProvider):
    """A local model. No key, no cost, and nothing leaves the machine."""

    def available(self) -> bool:
        return True

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}


#: Defaults chosen for this job: cheap, fast, good at structured output.
PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "openrouter": {
        "cls": OpenRouterProvider,
        "base_url": "https://openrouter.ai/api/v1",
        "model": "google/gemini-2.0-flash-exp:free",
        "keys": ("OPENROUTER_API_KEY",),
        "input_price": 0.0, "output_price": 0.0,
    },
    "gemini": {
        "cls": GeminiProvider,
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "model": "gemini-2.0-flash",
        "keys": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
        "input_price": 0.10, "output_price": 0.40,
    },
    "openai": {
        "cls": OpenAICompatibleProvider,
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "keys": ("OPENAI_API_KEY",),
        "input_price": 0.15, "output_price": 0.60,
    },
    "ollama": {
        "cls": OllamaProvider,
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "llama3.1",
        "keys": (),
        "input_price": 0.0, "output_price": 0.0,
    },
}


def build_provider(
    name: str | None = None,
    *,
    model: str | None = None,
    client: httpx.Client | None = None,
) -> Any:
    """Construct the configured provider, or a NullProvider if none is set."""
    from .base import NullProvider

    chosen = (name or env("VERITY_LLM_PROVIDER") or "").strip().lower()
    if not chosen or chosen in ("none", "off"):
        return NullProvider()

    spec = PROVIDER_DEFAULTS.get(chosen)
    if spec is None:
        raise ProviderError(
            f"unknown provider '{chosen}'. Choose one of: "
            + ", ".join(sorted(PROVIDER_DEFAULTS))
        )

    api_key = env(*spec["keys"]) if spec["keys"] else "local"
    cls = spec["cls"]
    return cls(
        name=chosen,
        model=model or env("VERITY_LLM_MODEL") or spec["model"],
        base_url=env("VERITY_LLM_BASE_URL") or spec["base_url"],
        api_key=api_key,
        client=client,
        input_price=spec["input_price"],
        output_price=spec["output_price"],
    )
