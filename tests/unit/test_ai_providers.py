"""The model provider layer.

Every test here runs against a mock transport. A test suite that needs a live
API key cannot run on a pull request from a fork, cannot run offline, quietly
bills somebody on every CI run, and is not deterministic. The request shapes
asserted below are the real ones each API documents, so the code is proven
against the contract without spending anything.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from verity_ai import (
    AiCassette,
    AiCassetteMissError,
    AiCassetteMode,
    Budget,
    BudgetExceededError,
    CassetteProvider,
    Completion,
    FallbackProvider,
    NullProvider,
    ProviderError,
    build_chain,
    build_provider,
    extract_json,
)

ANSWER = {"workflow_name": "invoice_to_po", "suggested": []}


def _transport(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ------------------------------------------------------------------ parsing

@pytest.mark.parametrize(
    "raw",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        '```\n{"a": 1}\n```',
        'Here is the JSON you asked for:\n{"a": 1}',
        'Sure!\n```json\n{"a": 1}\n```\nLet me know if you need more.',
    ],
)
def test_json_is_recovered_from_however_the_model_wrapped_it(raw: str) -> None:
    """Models wrap JSON in prose no matter how firmly they are told not to."""
    assert extract_json(raw) == {"a": 1}


@pytest.mark.parametrize("raw", ["not json at all", "[1, 2, 3]", ""])
def test_unusable_responses_are_rejected(raw: str) -> None:
    with pytest.raises(ProviderError):
        extract_json(raw)


# ---------------------------------------------------------------- providers

def test_openrouter_sends_the_openai_chat_shape() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["title"] = request.headers.get("x-title")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(ANSWER)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        })

    provider = build_provider("openrouter", client=_transport(handler))
    provider.api_key = "test-key"
    result = provider.complete_json("be helpful", "suggest checks")

    assert seen["url"].endswith("/chat/completions")
    assert seen["auth"] == "Bearer test-key"
    assert seen["title"] == "Verity"
    assert seen["body"]["temperature"] == 0
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in seen["body"]["messages"]] == ["system", "user"]
    assert result.data == ANSWER


def test_gemini_sends_its_own_shape() -> None:
    """Google's API is the one that does not follow the OpenAI convention."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-goog-api-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": json.dumps(ANSWER)}]}}],
            "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50},
        })

    provider = build_provider("gemini", client=_transport(handler))
    provider.api_key = "test-key"
    result = provider.complete_json("be helpful", "suggest checks")

    assert ":generateContent" in seen["url"]
    assert seen["key"] == "test-key"
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert seen["body"]["system_instruction"]["parts"][0]["text"] == "be helpful"
    assert result.data == ANSWER
    # Gemini's per-model prices are not compiled in, so the cost is reported
    # as unknown rather than as zero. See test_an_unpriced_model_reports_...
    assert result.usd is None


def test_a_blocked_gemini_response_says_why() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})

    provider = build_provider("gemini", client=_transport(handler))
    provider.api_key = "k"
    with pytest.raises(ProviderError, match="SAFETY"):
        provider.complete_json("s", "u")


@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, "key was rejected"), (429, "rate limited"), (500, "HTTP 500")],
)
def test_http_failures_are_reported_clearly(status: int, expected: str) -> None:
    provider = build_provider(
        "openrouter", client=_transport(lambda r: httpx.Response(status, text="nope"))
    )
    provider.api_key = "k"
    with pytest.raises(ProviderError, match=expected):
        provider.complete_json("s", "u")


def test_a_model_that_ignores_the_format_is_retried_then_given_up_on() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "sorry!"}}]})

    provider = build_provider("openrouter", client=_transport(handler))
    provider.api_key = "k"
    with pytest.raises(ProviderError, match="no valid JSON"):
        provider.complete_json("s", "u")
    assert calls["n"] == 3


def test_a_missing_key_fails_before_any_request_is_made() -> None:
    provider = build_provider("openai", client=_transport(lambda r: httpx.Response(200)))
    provider.api_key = None
    assert not provider.available()
    with pytest.raises(ProviderError, match="no API key"):
        provider.complete_json("s", "u")


def test_no_provider_configured_is_a_polite_no_not_a_crash() -> None:
    provider = NullProvider()
    assert not provider.available()
    with pytest.raises(ProviderError, match="no model provider"):
        provider.complete_json("s", "u")


def test_an_unknown_provider_name_lists_the_real_ones() -> None:
    with pytest.raises(ProviderError, match="openrouter"):
        build_provider("definitely-not-a-provider")


# ------------------------------------------------------------------- budget

def test_the_call_budget_is_a_hard_ceiling() -> None:
    budget = Budget(max_calls=2)
    budget.charge(10)
    budget.charge(10)
    with pytest.raises(BudgetExceededError, match="call budget"):
        budget.charge(10)


def test_the_cost_budget_is_a_hard_ceiling() -> None:
    budget = Budget(max_usd=0.01)
    with pytest.raises(BudgetExceededError, match="cost budget"):
        budget.charge(10, usd=0.02)


# ----------------------------------------------------------------- cassette

def test_record_then_replay_returns_the_same_answer(tmp_path: Path) -> None:
    path = tmp_path / "ai.cassette.json"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(ANSWER)}}]
        })

    live = build_provider("openrouter", client=_transport(handler))
    live.api_key = "k"
    recording = AiCassette(path, AiCassetteMode.RECORD)
    recorded = CassetteProvider(live, recording).complete_json("s", "u").data
    recording.save()

    class Unreachable:
        name, model = "openrouter", live.model

        def available(self) -> bool:
            return True

        def complete_json(self, *a: object, **k: object) -> Any:
            raise AssertionError("replay must not reach the network")

    replayed = CassetteProvider(
        Unreachable(), AiCassette(path, AiCassetteMode.REPLAY)
    ).complete_json("s", "u").data
    assert replayed == recorded == ANSWER


def test_a_cassette_miss_is_explicit(tmp_path: Path) -> None:
    provider = build_provider("openrouter", client=_transport(lambda r: httpx.Response(200)))
    provider.api_key = "k"
    wrapped = CassetteProvider(provider, AiCassette(tmp_path / "empty.json",
                                                   AiCassetteMode.REPLAY))
    with pytest.raises(AiCassetteMissError, match="no recorded exchange"):
        wrapped.complete_json("s", "u")


# ---------------------------------------------------------------- fireworks

def test_fireworks_qualifies_a_short_model_name() -> None:
    """Fireworks names models 'accounts/fireworks/models/x'.

    Its dashboard shows the long form and people paste the short one, so both
    are accepted and the long one is always sent.
    """
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(ANSWER)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        })

    provider = build_provider("fireworks", model="kimi-k3", client=_transport(handler))
    provider.api_key = "test-key"
    result = provider.complete_json("be helpful", "suggest checks")

    assert seen["url"] == "https://api.fireworks.ai/inference/v1/chat/completions"
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["model"] == "accounts/fireworks/models/kimi-k3"
    # The priority tier costs more and buys nothing here, so it is never asked for.
    assert "service_tier" not in seen["body"]
    assert result.data == ANSWER


def test_a_fully_qualified_fireworks_model_is_left_alone() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(ANSWER)}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        })

    name = "accounts/fireworks/models/glm-5p3-flash"
    provider = build_provider("fireworks", model=name, client=_transport(handler))
    provider.api_key = "test-key"
    provider.complete_json("be helpful", "suggest checks")

    assert seen["body"]["model"] == name


# -------------------------------------------------------------------- cost

def test_a_priced_model_reports_what_it_cost() -> None:
    """GLM 5.3 Flash has a published price, so the spend is real arithmetic."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(ANSWER)}}],
            "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000},
        })

    provider = build_provider(
        "fireworks",
        model="accounts/fireworks/models/glm-5p3-flash",
        client=_transport(handler),
    )
    provider.api_key = "test-key"

    assert provider.complete_json("s", "u").usd == pytest.approx(0.65)  # 0.15 + 0.50


def test_an_unpriced_model_says_unknown_rather_than_zero() -> None:
    """A printed $0.0000 that is really a charge would be a fabricated number.

    Most models here have no compiled-in price, and the honest report of an
    unknown cost is 'unknown'. The budget's call ceiling still applies.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(ANSWER)}}],
            "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000},
        })

    provider = build_provider("fireworks", model="kimi-k3", client=_transport(handler))
    provider.api_key = "test-key"

    assert provider.complete_json("s", "u").usd is None


def test_an_openrouter_free_model_is_known_to_be_free() -> None:
    provider = build_provider("openrouter", model="minimax/minimax-m3:free")
    assert (provider.input_price, provider.output_price) == (0.0, 0.0)

    paid = build_provider("openrouter", model="minimax/minimax-m3")
    assert (paid.input_price, paid.output_price) == (None, None)


def test_an_unpriced_call_is_still_capped_by_the_call_ceiling() -> None:
    budget = Budget(max_calls=2)
    budget.charge(10, None)
    budget.charge(10, None)

    assert budget.summary["usd"] is None
    with pytest.raises(BudgetExceededError):
        budget.charge(10, None)


# ---------------------------------------------------------------- fallback

def test_a_chain_falls_back_when_the_first_provider_fails() -> None:
    """A dead model or a rate limit should cost a fallback, not the run.

    Both happened for real while this layer was built: a model was retired
    mid-session and a free endpoint returned 429.
    """
    calls: list[str] = []

    class Stub:
        def __init__(self, name: str, error: Exception | None = None) -> None:
            self.name, self.model, self._error = name, f"{name}-1", error

        def available(self) -> bool:
            return True

        def complete_json(self, system: str, user: str, *, schema_hint: str = "") -> Completion:
            calls.append(self.name)
            if self._error is not None:
                raise self._error
            return Completion(data=ANSWER, model=self.model, usd=0.0)

    chain = FallbackProvider(providers=(
        Stub("dead", ProviderError("dead: HTTP 404 -- no endpoints found")),
        Stub("busy", ProviderError("busy: rate limited (429)")),
        Stub("good"),
    ))

    assert chain.complete_json("s", "u").data == ANSWER
    assert calls == ["dead", "busy", "good"]
    # The report must name the provider that actually answered.
    assert chain.name == "good"


def test_a_chain_that_runs_out_reports_every_failure() -> None:
    class Dead:
        name, model = "dead", "dead-1"

        def available(self) -> bool:
            return True

        def complete_json(self, system: str, user: str, *, schema_hint: str = "") -> Completion:
            raise ProviderError("dead: the API key was rejected (401)")

    chain = FallbackProvider(providers=(Dead(),))
    with pytest.raises(ProviderError, match="401"):
        chain.complete_json("s", "u")


def test_a_chain_is_built_from_a_plus_separated_list(monkeypatch: Any) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "g")
    monkeypatch.setenv("OPENROUTER_API_KEY", "o")

    chain = build_chain("gemini+openrouter")

    assert isinstance(chain, FallbackProvider)
    assert [p.name for p in chain.providers] == ["gemini", "openrouter"]
    # Before anything answers, the chain names what it would try.
    assert chain.name == "gemini+openrouter"


def test_one_provider_name_still_builds_one_provider(monkeypatch: Any) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "o")
    assert not isinstance(build_chain("openrouter"), FallbackProvider)


def test_a_chosen_model_applies_only_to_the_first_provider(monkeypatch: Any) -> None:
    """A model name belongs to the provider that serves it.

    ``VERITY_LLM_MODEL`` used to reach every provider in the chain, because
    each one read the variable for itself. The fallbacks then carried a model
    their API has never heard of, so the chain would 404 at exactly the moment
    it was supposed to save the run -- and it would look like the fallback
    provider was broken rather than misconfigured.
    """
    monkeypatch.setenv("CODECRAFT_API_KEY", "cc_test")
    monkeypatch.setenv("FIREWORKS_API_KEY", "f")
    monkeypatch.setenv("GOOGLE_API_KEY", "g")
    monkeypatch.setenv("VERITY_LLM_MODEL", "gemini-3.7-flash")

    chain = build_chain("codecraft+fireworks+gemini")

    assert isinstance(chain, FallbackProvider)
    first, *rest = chain.providers
    assert first.model == "gemini-3.7-flash"
    assert [p.model for p in rest] == [
        "accounts/fireworks/models/glm-5p3-flash",
        "gemini-3.5-flash",
    ]


def test_an_openai_compatible_aggregator_is_a_provider_of_its_own(
    monkeypatch: Any,
) -> None:
    """Named, rather than reached by overriding another provider's base URL.

    A name is what lets it take a position in a fallback chain and be priced
    on its own terms.
    """
    monkeypatch.setenv("CODECRAFT_API_KEY", "cc_test")
    monkeypatch.delenv("VERITY_LLM_MODEL", raising=False)

    provider = build_chain("codecraft")

    assert provider.name == "codecraft"
    assert provider.base_url == "https://codecraftapi.com/v1"
    assert provider.available()


def test_an_unpriced_model_is_reported_as_unpriced_rather_than_guessed(
    monkeypatch: Any,
) -> None:
    """An aggregator resells upstreams whose prices move. A made-up cost is
    worse than an admitted gap."""
    monkeypatch.setenv("CODECRAFT_API_KEY", "cc_test")
    monkeypatch.setenv("VERITY_LLM_MODEL", "some-model-nobody-has-priced")

    provider = build_chain("codecraft")

    assert provider.input_price is None
    assert provider.output_price is None
