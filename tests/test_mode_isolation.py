"""Self-check: a cloud failure must never silently activate the local mode.

This is the question the work order asks explicitly, so it is a test.
"""
from __future__ import annotations

import pytest
import requests

from spec2test import bootstrap
from spec2test.adapters.ollama.client import (
    API_KEY_ENV,
    CLOUD_HOST,
    DEFAULT_LOCAL_HOST,
    MODE_CLOUD,
    MODE_LOCAL,
    AuthenticationError,
    ConnectionFailedError,
    MissingApiKeyError,
    OllamaChatClient,
    OllamaSettings,
)
from spec2test.application.generate import generate_drafts
from spec2test.application.ports import ChatMessage
from spec2test.domain.models import Requirement, RunStatus, SourceSpan
from tests.test_ollama_client import FakeResponse, FakeSession

MESSAGES = [ChatMessage("system", "s"), ChatMessage("user", "u")]


def test_the_mode_decides_the_host_and_nothing_else_changes_it():
    cloud = OllamaSettings(model="m", mode=MODE_CLOUD)
    local = OllamaSettings(model="m", mode=MODE_LOCAL)
    assert cloud.resolved_host == CLOUD_HOST
    assert local.resolved_host == DEFAULT_LOCAL_HOST


@pytest.mark.parametrize(
    "failure",
    [
        FakeResponse(401, {"error": "bad key"}),
        FakeResponse(500, {"error": "boom"}),
        requests.exceptions.ConnectionError("refused"),
        requests.exceptions.ReadTimeout("slow"),
    ],
)
def test_a_cloud_failure_never_contacts_a_local_endpoint(failure):
    session = FakeSession(failure)
    client = OllamaChatClient(
        OllamaSettings(model="m", mode=MODE_CLOUD, max_retries=0),
        api_key="k",
        session=session,
        sleep=lambda _s: None,
    )
    with pytest.raises(Exception):
        client.chat(MESSAGES)

    hosts = {request["url"] for request in session.requests}
    assert all(url.startswith(CLOUD_HOST) for url in hosts), hosts
    assert not any("localhost" in url or "127.0.0.1" in url for url in hosts)
    assert client.settings.mode == MODE_CLOUD, "the mode must not change itself"


def test_a_missing_key_does_not_degrade_to_local_mode(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    session = FakeSession(FakeResponse(200, {"message": {"content": "{}"}}))
    client = OllamaChatClient(
        OllamaSettings(model="m", mode=MODE_CLOUD), session=session, sleep=lambda _s: None
    )
    with pytest.raises(MissingApiKeyError):
        client.chat(MESSAGES)
    assert session.requests == []


def test_a_failing_cloud_run_is_reported_as_failed_not_silently_retried_locally():
    text = "SYS-REQ-0001: The system shall react within 100 ms."
    requirement = Requirement(
        "REQ-1", text, (SourceSpan("doc", 0, len(text), text, page=1),), "SYS-REQ-0001"
    )
    session = FakeSession(
        *[FakeResponse(401, {"error": "bad key"}) for _ in range(4)]
    )
    client = OllamaChatClient(
        OllamaSettings(model="m", mode=MODE_CLOUD, max_retries=0),
        api_key="k",
        session=session,
        sleep=lambda _s: None,
    )
    run = generate_drafts([requirement], client)

    assert run.status is RunStatus.FAILED
    assert run.mode == MODE_CLOUD
    assert run.endpoint == f"{CLOUD_HOST}/api/chat"
    codes = {issue.code for unit in run.units for issue in unit.issues}
    assert "E_MODEL_CALL_FAILED" in codes
    assert "AuthenticationError" in "\n".join(
        attempt.error or "" for unit in run.units for attempt in unit.attempts
    )


def test_bootstrap_refuses_a_mode_mismatch():
    cloud = OllamaSettings(model="m", mode=MODE_CLOUD)
    with pytest.raises(ValueError):
        bootstrap.build_model(MODE_LOCAL, cloud)
    with pytest.raises(ValueError):
        bootstrap.build_model("ollama_cloud_but_fallback_to_local", cloud)
    with pytest.raises(ValueError):
        bootstrap.build_model(MODE_CLOUD, None)


def test_bootstrap_builds_what_the_mode_asks_for():
    cloud = bootstrap.build_model(
        MODE_CLOUD, OllamaSettings(model="m", mode=MODE_CLOUD), api_key="k"
    )
    assert cloud.descriptor.mode == MODE_CLOUD
    assert cloud.descriptor.endpoint == f"{CLOUD_HOST}/api/chat"

    local = bootstrap.build_model(MODE_LOCAL, OllamaSettings(model="m", mode=MODE_LOCAL))
    assert local.descriptor.mode == MODE_LOCAL

    fake = bootstrap.build_model(bootstrap.MODE_FAKE)
    assert fake.descriptor.mode == "fake"


def test_the_legacy_agent_keeps_its_mode_too():
    import core

    agent = core.AgentLocal(model="m", ollama_url="http://localhost:11434")
    assert agent.settings.mode == MODE_LOCAL
    assert not hasattr(agent, "_cli_generate")

    cloud_agent = core.AgentLocal(
        model="m", ollama_url=CLOUD_HOST, mode=MODE_CLOUD
    )
    assert cloud_agent.settings.mode == MODE_CLOUD
    assert "num_gpu" not in cloud_agent.settings.options()
