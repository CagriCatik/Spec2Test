"""HTTP transport, authentication and answer transport for Ollama.

Two explicit modes, no implicit switching between them:
    ollama_cloud - https://ollama.com, bearer auth with OLLAMA_API_KEY
    ollama_local - a local Ollama server, no authentication

A network failure never starts the local Ollama CLI, and the cloud API key is
neither persisted nor logged; it is sent only to the configured cloud host and
redirects are not followed.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Sequence
from urllib.parse import urlsplit

import requests

from ...application.deadline import Deadline
from ...application.ports import (
    ChatMessage,
    ModelDescriptor,
    ModelError,
    ModelReply,
)

MODE_CLOUD = "ollama_cloud"
MODE_LOCAL = "ollama_local"
MODES = (MODE_CLOUD, MODE_LOCAL)

CLOUD_HOST = "https://ollama.com"
DEFAULT_LOCAL_HOST = "http://localhost:11434"
API_KEY_ENV = "OLLAMA_API_KEY"

CHAT_PATH = "/api/chat"
TAGS_PATH = "/api/tags"

RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
MAX_RETRY_SLEEP_S = 30.0


# ----------------------------------------------------------------- errors ---
class OllamaError(ModelError):
    """Base class for all Ollama transport failures."""


class MissingApiKeyError(OllamaError):
    pass


class AuthenticationError(OllamaError):
    pass


class RateLimitError(OllamaError):
    retryable = True

    def __init__(self, message: str, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class ModelNotFoundError(OllamaError):
    """A model problem is never retried blindly."""


class TransientServerError(OllamaError):
    retryable = True


class RequestTimeoutError(OllamaError):
    retryable = True


class ConnectionFailedError(OllamaError):
    retryable = True


class RedirectNotFollowedError(OllamaError):
    """A redirect is not followed, so the API key never leaves its host."""


class ProtocolError(OllamaError):
    """The answer was transported but is not a usable Ollama chat reply."""


# --------------------------------------------------------------- settings ---
@dataclass(frozen=True)
class OllamaSettings:
    model: str
    mode: str = MODE_CLOUD
    host: str | None = None
    connect_timeout_s: float = 10.0
    read_timeout_s: float = 300.0
    max_retries: int = 2
    retry_backoff_s: float = 2.0
    temperature: float = 0.2
    top_p: float = 0.9
    top_k: int = 40
    max_tokens: int | None = 8192
    # local-only hardware options
    num_gpu: int | None = None
    main_gpu: int | None = None
    gpu_layers: int | None = None
    num_thread: int | None = None
    extra_options: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {self.mode!r}")
        if not self.model or not self.model.strip():
            raise ValueError("a model name is required")
        if self.connect_timeout_s <= 0 or self.read_timeout_s <= 0:
            raise ValueError("timeouts must be positive")

    @property
    def is_cloud(self) -> bool:
        return self.mode == MODE_CLOUD

    @property
    def resolved_host(self) -> str:
        host = self.host or (CLOUD_HOST if self.is_cloud else DEFAULT_LOCAL_HOST)
        return host.rstrip("/")

    @property
    def endpoint(self) -> str:
        return f"{self.resolved_host}{CHAT_PATH}"

    def options(self) -> dict[str, Any]:
        """Sampling options. GPU settings exist only for local operation."""
        options: dict[str, Any] = {
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
        }
        if self.max_tokens is not None:
            options["num_predict"] = self.max_tokens
        if not self.is_cloud:
            for key, value in (
                ("num_gpu", self.num_gpu),
                ("main_gpu", self.main_gpu),
                ("gpu_layers", self.gpu_layers),
                ("num_thread", self.num_thread),
            ):
                if value is not None:
                    options[key] = value
        options.update(self.extra_options)
        return options

    def for_model(self, model: str) -> "OllamaSettings":
        return replace(self, model=model)


def default_api_key_provider() -> str | None:
    key = os.environ.get(API_KEY_ENV)
    return key.strip() or None if key else None


# ----------------------------------------------------------------- client ---
class OllamaChatClient:
    """Implements :class:`ModelPort` over the Ollama chat API."""

    def __init__(
        self,
        settings: OllamaSettings,
        *,
        api_key: str | None = None,
        api_key_provider: Callable[[], str | None] | None = None,
        session: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.settings = settings
        self._session = session or requests.Session()
        self._sleep = sleep
        self._log = log
        # The key lives only in this closure: never an attribute, never stored
        # in a config file, never logged.
        if api_key is not None:
            self._api_key: Callable[[], str | None] = lambda: api_key
        else:
            self._api_key = api_key_provider or default_api_key_provider

    def __repr__(self) -> str:  # never leak credentials through a repr
        return (
            f"OllamaChatClient(mode={self.settings.mode!r}, "
            f"host={self.settings.resolved_host!r}, model={self.settings.model!r})"
        )

    # ------------------------------------------------------------ port API
    @property
    def descriptor(self) -> ModelDescriptor:
        return ModelDescriptor(
            model=self.settings.model,
            endpoint=self.settings.endpoint,
            mode=self.settings.mode,
            parameters=self.settings.options(),
            # Ollama Cloud does not offer schema-enforced structured outputs,
            # so local validation is mandatory and `format` is not assumed.
            supports_schema_enforcement=False,
        )

    def chat(
        self, messages: Sequence[ChatMessage], *, deadline: Deadline | None = None
    ) -> ModelReply:
        if not messages:
            raise ValueError("at least one chat message is required")
        payload = {
            "model": self.settings.model,
            "messages": [
                {"role": m.role, "content": m.content} for m in messages
            ],
            "stream": False,  # a stable single answer; streaming is its own work package
            "options": self.settings.options(),
        }
        started = time.perf_counter()
        body = self._post(CHAT_PATH, payload, deadline=deadline)
        return self._to_reply(body, duration_s=time.perf_counter() - started)

    def list_model_names(self, *, deadline: Deadline | None = None) -> list[str]:
        body = self._get(TAGS_PATH, deadline=deadline)
        models = body.get("models")
        if not isinstance(models, list):
            raise ProtocolError(f"{TAGS_PATH} did not return a model list")
        names: list[str] = []
        for entry in models:
            if isinstance(entry, dict):
                name = entry.get("name") or entry.get("model")
                if isinstance(name, str) and name:
                    names.append(name)
        return names

    def check_connection(self, *, deadline: Deadline | None = None) -> list[str]:
        """Verify credentials and reachability without generating anything."""
        return self.list_model_names(deadline=deadline)

    # ----------------------------------------------------------- internals
    def _headers(self, url: str) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if not self.settings.is_cloud:
            return headers  # a local server never receives the cloud key
        key = self._api_key()
        if not key:
            raise MissingApiKeyError(
                f"mode {MODE_CLOUD} requires an API key in ${API_KEY_ENV}; "
                "no request was sent and the local CLI is not used as a fallback"
            )
        if not _same_host(url, self.settings.resolved_host):
            raise OllamaError(
                f"refusing to send the API key to {urlsplit(url).netloc!r}; "
                f"configured cloud host is {self.settings.resolved_host!r}"
            )
        headers["Authorization"] = f"Bearer {key}"
        return headers

    def _redact(self, text: str) -> str:
        key = self._api_key() if self.settings.is_cloud else None
        return text.replace(key, "***") if key else text

    def _timeout(self, deadline: Deadline | None) -> tuple[float, float]:
        read = self.settings.read_timeout_s
        if deadline is not None:
            remaining = deadline.remaining_s()
            if remaining is not None:
                read = max(1.0, min(read, remaining))
        return (self.settings.connect_timeout_s, read)

    def _post(
        self, path: str, payload: dict, *, deadline: Deadline | None
    ) -> dict:
        return self._request("POST", path, payload=payload, deadline=deadline)

    def _get(self, path: str, *, deadline: Deadline | None) -> dict:
        return self._request("GET", path, payload=None, deadline=deadline)

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict | None,
        deadline: Deadline | None,
    ) -> dict:
        url = f"{self.settings.resolved_host}{path}"
        attempt = 0
        while True:
            attempt += 1
            if deadline is not None:
                deadline.check(f"{method} {path}")
            try:
                response = self._session.request(
                    method,
                    url,
                    json=payload,
                    headers=self._headers(url),
                    timeout=self._timeout(deadline),
                    allow_redirects=False,  # the key must not follow a redirect
                )
            except requests.exceptions.ConnectTimeout as exc:
                error: OllamaError = RequestTimeoutError(
                    f"connect timeout after {self.settings.connect_timeout_s}s "
                    f"for {url}: {self._redact(str(exc))}"
                )
            except requests.exceptions.ReadTimeout as exc:
                error = RequestTimeoutError(
                    f"read timeout after {self._timeout(deadline)[1]:.0f}s "
                    f"for {url}: {self._redact(str(exc))}"
                )
            except requests.exceptions.RequestException as exc:
                error = ConnectionFailedError(
                    f"request to {url} failed: {self._redact(str(exc))}"
                )
            else:
                try:
                    return self._handle_response(response, url)
                except OllamaError as exc:
                    error = exc

            if not self._retry(error, attempt, deadline):
                raise error

    def _retry(
        self, error: OllamaError, attempt: int, deadline: Deadline | None
    ) -> bool:
        if not error.retryable or attempt > self.settings.max_retries:
            return False
        wait = getattr(error, "retry_after_s", None)
        if wait is None:
            wait = self.settings.retry_backoff_s * (2 ** (attempt - 1))
        wait = min(float(wait), MAX_RETRY_SLEEP_S)
        if deadline is not None and not deadline.allows(wait + 1.0):
            return False
        if self._log:
            self._log(
                f"retrying after {type(error).__name__} in {wait:.0f}s "
                f"(attempt {attempt + 1}/{self.settings.max_retries + 1}); "
                "a repeated model call can cause additional usage"
            )
        self._sleep(wait)
        return True

    def _handle_response(self, response: Any, url: str) -> dict:
        status = int(response.status_code)

        if 300 <= status < 400:
            location = ""
            try:
                location = response.headers.get("Location", "") or ""
            except Exception:  # pragma: no cover - defensive
                pass
            raise RedirectNotFollowedError(
                f"{url} answered with a {status} redirect to {location!r}; "
                "redirects are not followed, so the API key stays on the "
                "configured host. Check the host setting."
            )

        if status in (401, 403):
            raise AuthenticationError(
                f"authentication failed ({status}) at {url}. "
                f"Check ${API_KEY_ENV} and whether the key is valid for this host. "
                f"Server said: {self._body_message(response)}"
            )

        if status == 404:
            raise ModelNotFoundError(
                f"{url} returned 404: {self._body_message(response)}. "
                f"Check the model name {self.settings.model!r} for this mode "
                "(cloud API names and local cloud aliases are not interchangeable)."
            )

        if status == 429:
            raise RateLimitError(
                f"rate limited (429) at {url}: {self._body_message(response)}",
                retry_after_s=_retry_after(response),
            )

        if status in RETRYABLE_STATUS:
            raise TransientServerError(
                f"temporary server error {status} at {url}: {self._body_message(response)}"
            )

        if status >= 400:
            raise ProtocolError(
                f"unexpected status {status} at {url}: {self._body_message(response)}"
            )

        try:
            body = response.json()
        except Exception as exc:
            raise ProtocolError(
                f"{url} returned a body that is not JSON: {self._redact(str(exc))}"
            ) from exc
        if not isinstance(body, dict):
            raise ProtocolError(
                f"{url} returned {type(body).__name__}, expected a JSON object"
            )
        return body

    def _body_message(self, response: Any) -> str:
        try:
            text = response.text or ""
        except Exception:  # pragma: no cover - defensive
            return "<no body>"
        text = text.strip()
        if not text:
            return "<empty body>"
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and "error" in parsed:
                text = str(parsed["error"])
        except ValueError:
            pass
        return self._redact(text[:400])

    def _to_reply(self, body: dict, *, duration_s: float) -> ModelReply:
        message = body.get("message")
        if not isinstance(message, dict):
            raise ProtocolError(
                "the answer contains no 'message' object "
                f"(keys: {sorted(body)}); streaming answers are not expected here"
            )
        content = message.get("content")
        if not isinstance(content, str):
            raise ProtocolError(
                f"'message.content' is {type(content).__name__}, expected a string"
            )
        done_reason = body.get("done_reason")
        done = body.get("done")
        truncated = done_reason == "length" or done is False
        return ModelReply(
            text=content,
            truncated=bool(truncated),
            done_reason=done_reason if isinstance(done_reason, str) else None,
            duration_s=duration_s,
            model=body.get("model") if isinstance(body.get("model"), str) else None,
        )


def _same_host(url: str, host: str) -> bool:
    left, right = urlsplit(url), urlsplit(host)
    return (left.scheme, left.netloc) == (right.scheme, right.netloc)


def _retry_after(response: Any) -> float | None:
    try:
        raw = response.headers.get("Retry-After")
    except Exception:  # pragma: no cover - defensive
        return None
    if not raw:
        return None
    try:
        return max(0.0, float(str(raw).strip()))
    except ValueError:
        return None
