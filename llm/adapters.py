"""LLM provider adapters — thin HTTP wrappers, no heavy frameworks.

Adding a new *OpenAI-compatible* provider never requires code changes —
just a new LLMConfig row with a different api_base_url.

Reliability/performance notes:
- A module-level requests.Session keeps connections (and TLS handshakes)
  alive across calls; opening a fresh connection per reply used to add
  100-300ms to every single LLM call.
- The timeout is env-tunable (LLM_TIMEOUT_SECONDS, default 45) because
  reasoning models (deepseek-reasoner, o1-style) routinely exceed 30s —
  the #1 production cause of the old "I'm not sure about that" fallback.
- An HTTP 200 with EMPTY content is treated as a failure (LLMEmptyResponseError),
  not silently returned: reasoning models sometimes leave `content` null and
  put everything in `reasoning_content`, which used to fall through as "" and
  read as the bot ignoring the visitor.
"""
import os
import threading

import requests
from requests.adapters import HTTPAdapter

# (connect, read) timeout in seconds for provider calls. Generous default: a
# slow-but-successful reply beats a timeout that ends in a fallback message.
TIMEOUT = int(os.environ.get("LLM_TIMEOUT_SECONDS", "45"))

_session_lock = threading.Lock()
_session: requests.Session | None = None


def _session() -> requests.Session:
    """Shared HTTP session (created lazily, safe under concurrency)."""
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                session = requests.Session()
                adapter = HTTPAdapter(pool_connections=4, pool_maxsize=16)
                session.mount("https://", adapter)
                session.mount("http://", adapter)
                _session = session
    return _session


class LLMProviderError(RuntimeError):
    """Provider call failed (HTTP error status, timeout, connection...)."""


class LLMEmptyResponseError(LLMProviderError):
    """Provider answered HTTP 200 but with empty/missing content."""


def _error_body(resp: requests.Response) -> str:
    """First 300 chars of the provider's error body — auth/rate-limit/quota
    problems are only diagnosable from this text, and raise_for_status()
    alone throws it away."""
    try:
        return (resp.text or "").strip()[:300]
    except Exception:
        return "<unreadable body>"


class LLMAdapter:
    def send(self, messages: list[dict], config) -> str:
        """messages: [{"role": ..., "content": ...}, ...]. Returns assistant text."""
        raise NotImplementedError


class OpenAICompatibleAdapter(LLMAdapter):
    """Works for OpenAI, DeepSeek, Groq, OpenRouter, Together.ai, local Ollama,
    and anything else exposing a /chat/completions endpoint in the OpenAI shape."""

    def send(self, messages, config):
        try:
            resp = _session().post(
                f"{config.api_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {config.api_key}"},
                json={
                    "model": config.model_name,
                    "messages": messages,
                    "temperature": config.temperature,
                    "max_tokens": config.max_tokens,
                },
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            raise LLMProviderError(f"{type(exc).__name__} calling {config.model_name}: {exc}") from exc
        if not resp.ok:
            raise LLMProviderError(
                f"HTTP {resp.status_code} from {config.model_name}: {_error_body(resp)}")
        try:
            content = resp.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError(
                f"Unrecognised response shape from {config.model_name}: "
                f"{resp.text[:300]}") from exc
        if not (isinstance(content, str) and content.strip()):
            raise LLMEmptyResponseError(
                f"{config.model_name} returned empty content "
                f"(reasoning models sometimes do this — check the model name).")
        return content.strip()


class AnthropicAdapter(LLMAdapter):
    def send(self, messages, config):
        try:
            resp = _session().post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": config.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": config.model_name,
                    "max_tokens": config.max_tokens,
                    "messages": messages,
                },
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            raise LLMProviderError(f"{type(exc).__name__} calling {config.model_name}: {exc}") from exc
        if not resp.ok:
            raise LLMProviderError(
                f"HTTP {resp.status_code} from {config.model_name}: {_error_body(resp)}")
        try:
            content = resp.json()["content"][0]["text"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError(
                f"Unrecognised response shape from {config.model_name}: "
                f"{resp.text[:300]}") from exc
        if not (isinstance(content, str) and content.strip()):
            raise LLMEmptyResponseError(f"{config.model_name} returned empty content.")
        return content.strip()


ADAPTERS = {
    "openai_compatible": OpenAICompatibleAdapter,
    "anthropic": AnthropicAdapter,
}


def get_adapter(llm_config) -> LLMAdapter:
    try:
        adapter_cls = ADAPTERS[llm_config.provider]
    except KeyError:
        raise ValueError(f"Unknown LLM provider: {llm_config.provider!r}")
    return adapter_cls()
