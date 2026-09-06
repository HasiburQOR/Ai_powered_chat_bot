"""LLM provider adapters — thin HTTP wrappers, no heavy frameworks.

Adding a new *OpenAI-compatible* provider never requires code changes —
just a new LLMConfig row with a different api_base_url.
"""
import requests


class LLMAdapter:
    def send(self, messages: list[dict], config) -> str:
        """messages: [{"role": ..., "content": ...}, ...]. Returns assistant text."""
        raise NotImplementedError


class OpenAICompatibleAdapter(LLMAdapter):
    """Works for OpenAI, DeepSeek, Groq, OpenRouter, Together.ai, local Ollama,
    and anything else exposing a /chat/completions endpoint in the OpenAI shape."""

    def send(self, messages, config):
        resp = requests.post(
            f"{config.api_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {config.api_key}"},
            json={
                "model": config.model_name,
                "messages": messages,
                "temperature": config.temperature,
                "max_tokens": config.max_tokens,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


class AnthropicAdapter(LLMAdapter):
    def send(self, messages, config):
        resp = requests.post(
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
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["content"][0]["text"]


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
