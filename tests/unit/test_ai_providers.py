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
    NullProvider,
    ProviderError,
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
    assert result.usd > 0


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
