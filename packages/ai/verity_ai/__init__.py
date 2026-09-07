"""Model providers for the authoring side of Verity.

A model proposes; deterministic code decides. Everything this package returns
is a suggestion that must survive validation before it reaches a contract, and
every value it produces is labelled ``INFERRED``.

The verifier does not import this package. Verification is deterministic and
costs nothing per run, which is what makes continuous checking affordable --
that property is worth more than any convenience a model could add there.
"""

from .base import (
    Budget,
    BudgetExceededError,
    Completion,
    NullProvider,
    Provider,
    ProviderError,
    env,
    extract_json,
)
from .cassette import (
    AiCassette,
    AiCassetteMissError,
    AiCassetteMode,
    CassetteProvider,
)
from .providers import (
    PROVIDER_DEFAULTS,
    FallbackProvider,
    FireworksProvider,
    GeminiProvider,
    HttpProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    OpenRouterProvider,
    build_chain,
    build_provider,
)

__all__ = [
    "PROVIDER_DEFAULTS",
    "AiCassette",
    "AiCassetteMissError",
    "AiCassetteMode",
    "Budget",
    "BudgetExceededError",
    "CassetteProvider",
    "Completion",
    "FallbackProvider",
    "FireworksProvider",
    "GeminiProvider",
    "HttpProvider",
    "NullProvider",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "OpenRouterProvider",
    "Provider",
    "ProviderError",
    "build_chain",
    "build_provider",
    "env",
    "extract_json",
]
