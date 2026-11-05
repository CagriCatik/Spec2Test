"""Live test against a real Ollama endpoint. Disabled by default.

Enable explicitly, because a real call costs real usage:

    SPEC2TEST_LIVE=1 pytest tests/test_live_cloud.py -v

    SPEC2TEST_LIVE=1                      required switch
    SPEC2TEST_LIVE_MODE=ollama_cloud       or ollama_local (default: ollama_cloud)
    SPEC2TEST_LIVE_MODEL=gpt-oss:120b      model name for that mode
    SPEC2TEST_LIVE_HOST=...                optional host override

Only synthetic requirements are used; never a real company document. No
assertion is made on the model's wording - only on structural validity.
"""
from __future__ import annotations

import os

import pytest

from spec2test.adapters.ollama.client import (
    API_KEY_ENV,
    MODE_CLOUD,
    MODE_LOCAL,
    MissingApiKeyError,
    OllamaChatClient,
    OllamaSettings,
)
from spec2test.adapters.ollama.models import capabilities_for
from spec2test.application.generate import GenerationSettings, generate_drafts
from spec2test.domain.models import Requirement, ReviewStatus, RunStatus, SourceSpan

pytestmark = pytest.mark.live

LIVE_ENABLED = os.environ.get("SPEC2TEST_LIVE") == "1"
MODE = os.environ.get("SPEC2TEST_LIVE_MODE", MODE_CLOUD)
MODEL = os.environ.get("SPEC2TEST_LIVE_MODEL", "gpt-oss:120b")
HOST = os.environ.get("SPEC2TEST_LIVE_HOST") or None

SYNTHETIC_TEXT = (
    "SYN-REQ-0001: The controller shall limit the output current to 2.5 A when "
    "the measured temperature exceeds 85 deg and shall report the limitation "
    "within 50 ms."
)


def _requirement() -> Requirement:
    return Requirement(
        requirement_id="REQ-LIVE-1",
        external_id="SYN-REQ-0001",
        text=SYNTHETIC_TEXT,
        spans=(
            SourceSpan(
                document_id="synthetic-live",
                char_start=0,
                char_end=len(SYNTHETIC_TEXT),
                quote=SYNTHETIC_TEXT,
                section="live test",
            ),
        ),
    )


def _settings() -> OllamaSettings:
    return OllamaSettings(
        model=MODEL,
        mode=MODE,
        host=HOST,
        connect_timeout_s=15.0,
        read_timeout_s=240.0,
        max_retries=1,
        temperature=0.2,
        max_tokens=4096,
    )


skip_unless_live = pytest.mark.skipif(
    not LIVE_ENABLED, reason="set SPEC2TEST_LIVE=1 to run the live test"
)


@skip_unless_live
def test_the_endpoint_lists_models():
    client = OllamaChatClient(_settings())
    names = client.check_connection()
    assert isinstance(names, list)
    print(f"\n{len(names)} model(s) at {_settings().resolved_host}: {names[:10]}")
    if names and MODEL not in names:
        print(f"note: {MODEL!r} is not in the list; the call may fail with 404")


@skip_unless_live
def test_a_draft_is_generated_from_a_synthetic_requirement():
    client = OllamaChatClient(_settings())
    caps = capabilities_for(MODEL, MODE)
    assert caps.supports_schema_enforcement is False, (
        "schema enforcement must not be assumed; local validation stays mandatory"
    )

    run = generate_drafts(
        [_requirement()],
        client,
        settings=GenerationSettings(
            max_cases_per_unit=2, max_repair_attempts=2, job_timeout_s=300.0
        ),
        source_hash="synthetic-live",
        log=lambda line: print(line),
    )

    print("\n".join(run.report_lines()))
    for unit in run.units:
        for attempt in unit.attempts:
            print(
                f"attempt {attempt.attempt} repair={attempt.is_repair} "
                f"issues={[i.code for i in attempt.issues]}"
            )
            print(attempt.raw_response[:1500])

    assert run.status in (RunStatus.COMPLETED, RunStatus.PARTIAL), (
        f"live run ended as {run.status.value}: {run.report_lines()}"
    )
    assert run.test_cases, "no draft was produced"
    for case in run.test_cases:
        assert case.review_status is ReviewStatus.DRAFT
        assert case.requirement_ids == ("REQ-LIVE-1",)
        assert case.steps and all(step.expectations for step in case.steps)
        assert case.source_spans
    assert run.endpoint.endswith("/api/chat")
    assert run.units[0].attempts[0].raw_response


@skip_unless_live
@pytest.mark.skipif(MODE != MODE_CLOUD, reason="cloud mode only")
def test_cloud_mode_without_a_key_fails_before_any_request(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    client = OllamaChatClient(OllamaSettings(model=MODEL, mode=MODE_CLOUD))
    with pytest.raises(MissingApiKeyError):
        client.check_connection()


@skip_unless_live
@pytest.mark.skipif(MODE != MODE_LOCAL, reason="local mode only")
def test_local_mode_needs_no_api_key():
    settings = OllamaSettings(model=MODEL, mode=MODE_LOCAL, host=HOST)
    assert "Authorization" not in OllamaChatClient(settings)._headers(settings.endpoint)
