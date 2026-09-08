"""Concrete model providers.

Four, sharing one HTTP shape and one contract: given a system prompt and a user
prompt, return parsed JSON. Nothing here knows what Verity does with the
answer, which is why swapping providers changes no behaviour anywhere else.

Prices are per million tokens and are used only for the local budget ceiling.
They drift; they are not billing, and the budget is deliberately conservative.

A price of ``None`` means the price for this model is not known here. That is
reported as unknown rather than as zero: a run that quietly prints $0.0000
while billing a card is worse than one that admits it cannot tell.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    input_price: float | None = 0.0
    output_price: float | None = 0.0

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

    def _usage_cost(self, payload: dict[str, Any]) -> float | None:
        if self.input_price is None or self.output_price is None:
            return None
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


class FireworksProvider(OpenAICompatibleProvider):
    """Fireworks AI. OpenAI's shape, with fully-qualified model names.

    Fireworks offers a 'priority' service tier at a higher price. It is not
    requested here: this layer makes at most a handful of calls while a person
    waits, so paying for reduced queueing would buy nothing worth the money.
    """

    def _payload(self, system: str, user: str) -> dict[str, Any]:
        payload = super()._payload(system, user)
        # Fireworks accepts the short form, but its own docs and dashboard use
        # the fully-qualified name, so accept either and send the long one.
        model = str(payload.get("model", ""))
        if model and not model.startswith("accounts/"):
            payload["model"] = f"accounts/fireworks/models/{model}"
        return payload


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

    def _usage_cost(self, payload: dict[str, Any]) -> float | None:
        if self.input_price is None or self.output_price is None:
            return None
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
        "model": "minimax/minimax-m3:free",
        "keys": ("OPENROUTER_API_KEY",),
        # Priced per model below: ':free' is free, anything else is unknown.
        "input_price": None, "output_price": None,
    },
    "fireworks": {
        "cls": FireworksProvider,
        "base_url": "https://api.fireworks.ai/inference/v1",
        "model": "accounts/fireworks/models/glm-5p3-flash",
        "keys": ("FIREWORKS_API_KEY",),
        "input_price": None, "output_price": None,
    },
    "gemini": {
        "cls": GeminiProvider,
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "model": "gemini-3.5-flash",
        "keys": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
        "input_price": None, "output_price": None,
    },
    "openai": {
        "cls": OpenAICompatibleProvider,
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "keys": ("OPENAI_API_KEY",),
        "input_price": None, "output_price": None,
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
    use_env_model: bool = True,
) -> Any:
    """Construct the configured provider, or a NullProvider if none is set.

    ``use_env_model=False`` makes the provider ignore ``VERITY_LLM_MODEL`` and
    take its own default. A fallback chain uses that for every provider after
    the first: a model name belongs to the provider that serves it, and
    passing one provider's name to the next produces a 404 at the moment the
    fallback was supposed to save the run.
    """
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
    env_model = env("VERITY_LLM_MODEL") if use_env_model else ""
    chosen_model = model or env_model or spec["model"]
    input_price, output_price = _price_of(chosen, chosen_model, spec)
    cls = spec["cls"]
    return cls(
        name=chosen,
        model=chosen_model,
        base_url=env("VERITY_LLM_BASE_URL") or spec["base_url"],
        api_key=api_key,
        client=client,
        input_price=input_price,
        output_price=output_price,
    )


#: Prices per million tokens, as published by the provider on the day they
#: were read. They drift, so a model absent here is reported as unpriced
#: rather than guessed -- a made-up cost is worse than an admitted gap.
KNOWN_PRICES: dict[tuple[str, str], tuple[float, float]] = {
    # Fireworks, standard serving tier, read 2026-09-07.
    ("fireworks", "accounts/fireworks/models/glm-5p3-flash"): (0.15, 0.50),
}


def _price_of(
    provider: str, model: str, spec: dict[str, Any]
) -> tuple[float | None, float | None]:
    """What a million tokens costs, or None when this build cannot say.

    Only two cases are known for certain without a price feed: a provider that
    runs on the user's own machine, and OpenRouter's ':free' models, which that
    service defines as free. Everything else is unknown, and saying so is the
    point -- a printed $0.0000 that is actually a charge would be a fabricated
    number, and this tool reports cost to be trusted.
    """
    if provider == "ollama":
        return 0.0, 0.0
    if provider == "openrouter" and model.endswith(":free"):
        return 0.0, 0.0
    known = KNOWN_PRICES.get((provider, model))
    if known is not None:
        return known
    return spec["input_price"], spec["output_price"]


@dataclass
class FallbackProvider:
    """Tries each provider in turn, and reports which one answered.

    Free endpoints are rate limited and models are retired without notice --
    both happened while this layer was being built. A chain means a missing
    key or a dead model costs a fallback rather than the whole run.

    A refusal is not a reason to try the next provider. Only a transport-level
    failure is: if a provider answered and the answer was unusable, the next
    provider will likely produce the same unusable answer at twice the cost.

    A fallback that answers is still a fallback. The providers that failed on
    the way are kept rather than discarded, because a chain that silently
    covers for a dead primary is how a team discovers in production that their
    first choice has been down for a month. Success is reported with its cost.
    """

    providers: tuple[Any, ...]
    #: Set to the provider that answered, so a caller can report it honestly.
    used: Any = None

    #: (provider name, error) for each provider tried before one answered.
    #: Populated on success as well as on failure.
    failures: list[tuple[str, str]] = field(default_factory=list)

    @property
    def fell_back(self) -> bool:
        """True when something earlier in the chain failed and was covered for."""
        return bool(self.failures) and self.used is not None

    @property
    def name(self) -> str:
        if self.used is not None:
            return str(self.used.name)
        return "+".join(str(p.name) for p in self.providers) or "none"

    @property
    def model(self) -> str:
        if self.used is not None:
            return str(self.used.model)
        first = self._ready()
        return str(first.model) if first is not None else "none"

    def _ready(self) -> Any:
        for provider in self.providers:
            if provider.available():
                return provider
        return None

    def available(self) -> bool:
        return self._ready() is not None

    def complete_json(self, system: str, user: str, *, schema_hint: str = "") -> Completion:
        ready = [p for p in self.providers if p.available()]
        if not ready:
            raise ProviderError(
                "no model provider is configured. Set VERITY_LLM_PROVIDER and "
                "the matching API key, or run without --ai."
            )

        self.failures = []
        self.used = None
        for provider in ready:
            try:
                completion: Completion = provider.complete_json(
                    system, user, schema_hint=schema_hint
                )
            except ProviderError as exc:
                self.failures.append((str(provider.name), str(exc)))
                continue
            self.used = provider
            return completion

        raise ProviderError(
            "; then ".join(f"{name}: {error}" for name, error in self.failures)
        )


def build_chain(
    names: str | None = None,
    *,
    model: str | None = None,
    client: httpx.Client | None = None,
) -> Any:
    """Build one provider, or a fallback chain from a '+'-separated list.

    ``VERITY_LLM_PROVIDER=gemini+openrouter`` tries Google first and falls back
    to OpenRouter. A single name behaves exactly as before, so nothing that
    names one provider changes.

    A chosen model applies to the first provider only -- whether it arrived as
    ``--ai-model`` or as ``VERITY_LLM_MODEL``. Model names are not portable
    between providers, and handing one provider's name to the next produces a
    404 at exactly the moment the fallback was supposed to save the run.
    """
    from .base import NullProvider

    raw = (names or env("VERITY_LLM_PROVIDER") or "").strip().lower()
    parts = [p for p in (piece.strip() for piece in raw.split("+")) if p]
    if len(parts) < 2:
        return build_provider(names, model=model, client=client)

    built = [
        build_provider(
            part,
            model=model if index == 0 else None,
            client=client,
            use_env_model=index == 0,
        )
        for index, part in enumerate(parts)
    ]
    usable = [p for p in built if not isinstance(p, NullProvider)]
    if not usable:
        return NullProvider()
    return FallbackProvider(providers=tuple(usable))
