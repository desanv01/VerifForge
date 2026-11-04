"""Portable public test: null content is legal only at a length limit.

Runs normally against the installed package after main installs the candidate, and
against the candidate overlay in the private focused run. No coordination paths.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from pydantic import SecretStr, ValidationError

import veriforge.planner.provider as provider_module
from veriforge.planner.models import (
    InvestigationLimits, ModelUsage, ProviderCompletion,
)
from veriforge.planner.provider import MetaConfig, MetaProvider
from veriforge.planner.steps import validate_completion

MODEL = "muse-spark-1.3"
LIMITS = InvestigationLimits(max_output_tokens=4096)


def completion(**overrides):
    values = {"content": None, "model": MODEL,
              "usage": ModelUsage(input_tokens=358, output_tokens=1024),
              "finish_reason": "length"}
    values.update(overrides)
    return values


def null_length_response() -> httpx.Response:
    return httpx.Response(200, json={
        "model": MODEL,
        "choices": [{"message": {"content": None}, "finish_reason": "length"}],
        "usage": {"prompt_tokens": 358, "completion_tokens": 1024},
    })


def test_null_content_with_length_is_valid_and_usage_preserved():
    value = ProviderCompletion(**completion())
    assert value.content is None
    assert value.finish_reason == "length"
    assert value.usage.input_tokens == 358
    assert value.usage.output_tokens == 1024


def test_null_content_with_stop_is_invalid():
    with pytest.raises(ValidationError):
        ProviderCompletion(**completion(finish_reason="stop"))


def test_null_content_with_other_or_missing_finish_reason_is_invalid():
    with pytest.raises(ValidationError):
        ProviderCompletion(**completion(finish_reason="content_filter"))
    missing = completion()
    del missing["finish_reason"]
    with pytest.raises(ValidationError):
        ProviderCompletion(**missing)


def test_missing_content_is_invalid():
    missing = completion()
    del missing["content"]
    with pytest.raises(ValidationError):
        ProviderCompletion(**missing)


def test_strict_usage_and_string_bounds_are_unchanged():
    with pytest.raises(ValidationError):
        ProviderCompletion(**completion(content="{}", finish_reason="stop",
                                       usage={"input_tokens": True, "output_tokens": 1}))
    with pytest.raises(ValidationError):
        ProviderCompletion(**completion(content="{}", finish_reason="stop",
                                       usage={"input_tokens": -1, "output_tokens": 1}))
    too_long = "x" * 256_001
    with pytest.raises(ValidationError):
        ProviderCompletion(content=too_long, model=MODEL, finish_reason="stop")
    value = ProviderCompletion(content="visible", model=MODEL, finish_reason="stop")
    assert "visible" not in repr(value)


def test_production_provider_preserves_null_length_usage_and_validator_rejects(monkeypatch):
    monkeypatch.setattr(provider_module, "request_payload",
                        lambda context, model, tokens: {"model": model, "messages": []})
    config = MetaConfig(model=MODEL, api_key=SecretStr("unit-test-not-a-credential"))
    provider = MetaProvider(config, transport=httpx.MockTransport(
        lambda request: null_length_response()))

    async def complete():
        return await provider.complete(object(), output_tokens=4096, timeout_seconds=5)

    completion_value = asyncio.run(complete())
    assert isinstance(completion_value, ProviderCompletion)
    assert completion_value.content is None
    assert completion_value.finish_reason == "length"
    assert completion_value.usage.input_tokens == 358
    assert completion_value.usage.output_tokens == 1024

    decision = validate_completion(completion_value, requested_model=MODEL,
                                   input_allowance=4000, limits=LIMITS)
    assert decision.outcome == "invalid_proposal"
    assert decision.decision is None
    assert completion_value.usage.output_tokens == 1024
