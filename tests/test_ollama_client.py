"""AP 3: HTTP adapter tests against a mock transport. No real network."""
from __future__ import annotations

import json

import pytest
import requests

from spec2test.adapters.ollama.client import (
    API_KEY_ENV,
    CLOUD_HOST,
    MODE_CLOUD,
    MODE_LOCAL,
    AuthenticationError,
    ConnectionFailedError,
    MissingApiKeyError,
    ModelNotFoundError,
    OllamaChatClient,
    OllamaSettings,
    ProtocolError,
    RateLimitError,
    RedirectNotFollowedError,
    RequestTimeoutError,
    TransientServerError,
)
from spec2test.adapters.ollama.models import capabilities_for, check_model_choice
from spec2test.application.deadline import Deadline
from spec2test.application.ports import ChatMessage

VALID_BODY = {
    "model": "gpt-oss:120b",
    "message": {"role": "assistant", "content": '{"schema_version":"1.0"}'},
    "done": True,
    "done_reason": "stop",
}


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None, text=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.text = text if text is not None else json.dumps(body or {})

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    """Records requests and replays prepared responses or exceptions."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[dict] = []

    def request(self, method, url, *, json=None, headers=None, timeout=None,
                allow_redirects=None):
        self.requests.append(
            {
                "method": method,
                "url": url,
                "json": json,
                "headers": headers or {},
                "timeout": timeout,
                "allow_redirects": allow_redirects,
            }
        )
        item = self.responses.pop(0) if self.responses else FakeResponse(200, VALID_BODY)
        if isinstance(item, Exception):
            raise item
        return item


def cloud_settings(**kwargs) -> OllamaSettings:
    base = dict(model="gpt-oss:120b", mode=MODE_CLOUD, max_retries=0)
    base.update(kwargs)
    return OllamaSettings(**base)


def make_client(session, settings=None, api_key="test-key", **kwargs):
    return OllamaChatClient(
        settings or cloud_settings(),
        api_key=api_key,
        session=session,
        sleep=lambda _s: None,
        **kwargs,
    )


MESSAGES = [ChatMessage("system", "rules"), ChatMessage("user", "requirement")]


# ------------------------------------------------------------------ success --
def test_success_uses_chat_endpoint_real_roles_and_no_streaming():
    session = FakeSession(FakeResponse(200, VALID_BODY))
    reply = make_client(session).chat(MESSAGES)

    sent = session.requests[0]
    assert sent["url"] == f"{CLOUD_HOST}/api/chat"
    assert sent["json"]["stream"] is False
    assert sent["json"]["messages"] == [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "requirement"},
    ]
    assert sent["headers"]["Authorization"] == "Bearer test-key"
    assert sent["allow_redirects"] is False
    assert reply.text == '{"schema_version":"1.0"}'
    assert reply.truncated is False


def test_connect_and_read_timeouts_are_separate():
    session = FakeSession()
    settings = cloud_settings(connect_timeout_s=3.5, read_timeout_s=42.0)
    make_client(session, settings).chat(MESSAGES)
    assert session.requests[0]["timeout"] == (3.5, 42.0)


def test_read_timeout_is_capped_by_the_job_deadline():
    session = FakeSession()
    settings = cloud_settings(read_timeout_s=600.0)
    make_client(session, settings).chat(MESSAGES, deadline=Deadline(30.0))
    connect, read = session.requests[0]["timeout"]
    assert connect == 10.0
    assert 20.0 < read <= 30.0


# ---------------------------------------------------------------- auth/key ---
def test_missing_api_key_fails_before_any_request(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    session = FakeSession(FakeResponse(200, VALID_BODY))
    client = OllamaChatClient(cloud_settings(), session=session, sleep=lambda _s: None)

    with pytest.raises(MissingApiKeyError) as excinfo:
        client.chat(MESSAGES)

    assert API_KEY_ENV in str(excinfo.value)
    assert session.requests == [], "no request may be sent without a key"


def test_local_mode_never_sends_the_cloud_key(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, "secret-key")
    session = FakeSession(FakeResponse(200, VALID_BODY))
    settings = OllamaSettings(model="llama3", mode=MODE_LOCAL, max_retries=0)
    OllamaChatClient(settings, session=session, sleep=lambda _s: None).chat(MESSAGES)

    headers = session.requests[0]["headers"]
    assert "Authorization" not in headers
    assert "secret-key" not in json.dumps(headers)


def test_401_and_403_are_reported_understandably():
    for status in (401, 403):
        session = FakeSession(FakeResponse(status, {"error": "invalid api key"}))
        with pytest.raises(AuthenticationError) as excinfo:
            make_client(session).chat(MESSAGES)
        message = str(excinfo.value)
        assert str(status) in message
        assert API_KEY_ENV in message
        assert "invalid api key" in message


def test_the_api_key_is_redacted_from_error_messages():
    session = FakeSession(FakeResponse(500, {"error": "leaked super-secret here"}))
    client = make_client(session, api_key="super-secret")
    with pytest.raises(TransientServerError) as excinfo:
        client.chat(MESSAGES)
    assert "super-secret" not in str(excinfo.value)
    assert "***" in str(excinfo.value)


def test_repr_does_not_leak_the_key():
    assert "my-key" not in repr(make_client(FakeSession(), api_key="my-key"))


def test_redirects_are_not_followed():
    session = FakeSession(
        FakeResponse(302, None, headers={"Location": "https://evil.example/api/chat"})
    )
    with pytest.raises(RedirectNotFollowedError) as excinfo:
        make_client(session).chat(MESSAGES)
    assert "evil.example" in str(excinfo.value)


def test_key_goes_to_the_configured_host_even_if_it_is_not_ollama_com():
    session = FakeSession(FakeResponse(200, VALID_BODY))
    client = make_client(session, cloud_settings(host="https://ollama.com"))
    client.settings = cloud_settings(host="https://somewhere-else.example")
    # the guard compares the request URL against the configured cloud host
    client.chat(MESSAGES)
    assert session.requests[0]["url"].startswith("https://somewhere-else.example")
    assert session.requests[0]["headers"]["Authorization"] == "Bearer test-key"


def test_mismatched_host_is_refused(monkeypatch):
    session = FakeSession(FakeResponse(200, VALID_BODY))
    client = make_client(session)
    monkeypatch.setattr(
        type(client.settings),
        "resolved_host",
        property(lambda self: "https://ollama.com"),
    )
    # _headers() checks the actual URL against the configured host
    from spec2test.adapters.ollama import client as client_module

    monkeypatch.setattr(client_module, "_same_host", lambda *_: False)
    with pytest.raises(Exception) as excinfo:
        client.chat(MESSAGES)
    assert "refusing to send the API key" in str(excinfo.value)


# -------------------------------------------------------------- rate limit ---
def test_429_honours_retry_after_then_succeeds():
    slept: list[float] = []
    session = FakeSession(
        FakeResponse(429, {"error": "slow down"}, headers={"Retry-After": "7"}),
        FakeResponse(200, VALID_BODY),
    )
    client = OllamaChatClient(
        cloud_settings(max_retries=1),
        api_key="k",
        session=session,
        sleep=slept.append,
    )
    reply = client.chat(MESSAGES)
    assert slept == [7.0]
    assert reply.text == '{"schema_version":"1.0"}'
    assert len(session.requests) == 2


def test_429_without_retries_raises_rate_limit_error():
    session = FakeSession(FakeResponse(429, {"error": "quota"}))
    with pytest.raises(RateLimitError):
        make_client(session).chat(MESSAGES)


def test_retry_is_skipped_when_the_deadline_would_be_exceeded():
    slept: list[float] = []
    session = FakeSession(
        FakeResponse(429, {"error": "slow"}, headers={"Retry-After": "120"}),
        FakeResponse(200, VALID_BODY),
    )
    client = OllamaChatClient(
        cloud_settings(max_retries=3), api_key="k", session=session, sleep=slept.append
    )
    with pytest.raises(RateLimitError):
        client.chat(MESSAGES, deadline=Deadline(20.0))
    assert slept == []


def test_transient_server_errors_retry_with_backoff():
    slept: list[float] = []
    session = FakeSession(
        FakeResponse(503, {"error": "unavailable"}),
        FakeResponse(502, {"error": "bad gateway"}),
        FakeResponse(200, VALID_BODY),
    )
    client = OllamaChatClient(
        cloud_settings(max_retries=2, retry_backoff_s=2.0),
        api_key="k",
        session=session,
        sleep=slept.append,
    )
    client.chat(MESSAGES)
    assert slept == [2.0, 4.0]


def test_retrying_logs_that_it_can_cause_additional_usage():
    lines: list[str] = []
    session = FakeSession(
        FakeResponse(503, {"error": "unavailable"}), FakeResponse(200, VALID_BODY)
    )
    client = OllamaChatClient(
        cloud_settings(max_retries=1),
        api_key="k",
        session=session,
        sleep=lambda _s: None,
        log=lines.append,
    )
    client.chat(MESSAGES)
    assert any("additional usage" in line for line in lines)


def test_model_errors_are_not_retried_blindly():
    session = FakeSession(
        FakeResponse(404, {"error": "model 'nope' not found"}),
        FakeResponse(200, VALID_BODY),
    )
    client = OllamaChatClient(
        cloud_settings(max_retries=3), api_key="k", session=session, sleep=lambda _s: None
    )
    with pytest.raises(ModelNotFoundError):
        client.chat(MESSAGES)
    assert len(session.requests) == 1


# ---------------------------------------------------------------- timeouts ---
def test_connect_timeout_is_mapped_and_bounded():
    session = FakeSession(
        requests.exceptions.ConnectTimeout("too slow"),
        requests.exceptions.ConnectTimeout("too slow"),
    )
    client = OllamaChatClient(
        cloud_settings(max_retries=1), api_key="k", session=session, sleep=lambda _s: None
    )
    with pytest.raises(RequestTimeoutError) as excinfo:
        client.chat(MESSAGES)
    assert "connect timeout" in str(excinfo.value)
    assert len(session.requests) == 2


def test_read_timeout_is_mapped():
    session = FakeSession(requests.exceptions.ReadTimeout("no answer"))
    with pytest.raises(RequestTimeoutError) as excinfo:
        make_client(session).chat(MESSAGES)
    assert "read timeout" in str(excinfo.value)


def test_connection_error_is_mapped_and_never_starts_a_cli():
    session = FakeSession(requests.exceptions.ConnectionError("refused"))
    with pytest.raises(ConnectionFailedError):
        make_client(session).chat(MESSAGES)


# ------------------------------------------------------- invalid answers -----
def test_answer_without_message_object_is_rejected():
    session = FakeSession(FakeResponse(200, {"response": "legacy generate shape"}))
    with pytest.raises(ProtocolError) as excinfo:
        make_client(session).chat(MESSAGES)
    assert "message" in str(excinfo.value)


def test_non_string_content_is_rejected():
    session = FakeSession(FakeResponse(200, {"message": {"content": {"a": 1}}}))
    with pytest.raises(ProtocolError):
        make_client(session).chat(MESSAGES)


def test_body_that_is_not_json_is_rejected():
    session = FakeSession(FakeResponse(200, None, text="<html>gateway</html>"))
    with pytest.raises(ProtocolError):
        make_client(session).chat(MESSAGES)


def test_truncated_answer_is_flagged():
    body = dict(VALID_BODY, done_reason="length", message={"content": '{"test_cases": ['})
    reply = make_client(FakeSession(FakeResponse(200, body))).chat(MESSAGES)
    assert reply.truncated is True
    assert reply.done_reason == "length"


def test_unfinished_answer_is_flagged_via_done_false():
    body = dict(VALID_BODY, done=False, done_reason=None)
    reply = make_client(FakeSession(FakeResponse(200, body))).chat(MESSAGES)
    assert reply.truncated is True


# ------------------------------------------------------------ model list -----
def test_model_list_uses_tags_endpoint():
    body = {"models": [{"name": "gpt-oss:120b"}, {"name": "qwen3-coder:480b"}]}
    session = FakeSession(FakeResponse(200, body))
    names = make_client(session).list_model_names()
    assert session.requests[0]["method"] == "GET"
    assert session.requests[0]["url"].endswith("/api/tags")
    assert names == ["gpt-oss:120b", "qwen3-coder:480b"]


def test_gpu_options_only_exist_in_local_mode():
    cloud = cloud_settings(num_gpu=2, main_gpu=1, gpu_layers=20, num_thread=8)
    assert "num_gpu" not in cloud.options()
    local = OllamaSettings(
        model="llama3", mode=MODE_LOCAL, num_gpu=2, main_gpu=1, gpu_layers=20, num_thread=8
    )
    assert local.options()["num_gpu"] == 2
    assert local.options()["num_thread"] == 8


def test_cloud_capabilities_do_not_promise_schema_enforcement():
    caps = capabilities_for("gpt-oss:120b", MODE_CLOUD)
    assert caps.supports_schema_enforcement is False
    assert caps.supports_gpu_options is False
    assert any("structured outputs" in note for note in caps.notes)


def test_local_cloud_alias_is_not_equated_with_the_api_name():
    warnings = check_model_choice("gpt-oss:120b-cloud", ["gpt-oss:120b"], MODE_CLOUD)
    assert warnings, "an unknown name must produce a warning"
    assert any("alias" in w.lower() for w in warnings)
    caps = capabilities_for("gpt-oss:120b-cloud", MODE_CLOUD)
    assert any("not mapped automatically" in note for note in caps.notes)
    # the name itself is never rewritten
    assert caps.name == "gpt-oss:120b-cloud"


def test_a_manual_model_name_is_allowed():
    assert check_model_choice("gpt-oss:120b", ["gpt-oss:120b"], MODE_CLOUD) == []


def test_mode_must_be_explicit():
    with pytest.raises(ValueError):
        OllamaSettings(model="m", mode="guess-it")
