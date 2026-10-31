"""Which provider serves a model, and the cached client for each connected provider."""

import os

from auth.chatgpt import get_chatgpt_account
from llm.anthropic_client import AnthropicClient
from llm.chatgpt_client import ChatGPTClient
from llm.openrouter import OPENROUTER_BASE, OpenRouterClient
from secret_store import get_secret

PROVIDERS = ("chatgpt", "anthropic", "openrouter")
PROVIDER_NAMES = {"chatgpt": "ChatGPT", "anthropic": "Anthropic", "openrouter": "OpenRouter"}
# provider -> (secret name, key prefix) for the providers that sign in with an API key.
KEY_PROVIDERS: dict[str, tuple[str, str]] = {
    "openrouter": ("openrouter_api_key", "sk-or-"),
    "anthropic": ("anthropic_api_key", "sk-ant-"),
}

_clients: dict[str, object] = {}


def parse_model(ref: str) -> tuple[str, str]:
    """Split "provider:model" into its parts; a bare id belongs to OpenRouter."""
    provider, _, model_id = ref.partition(":")
    if provider in PROVIDERS and model_id:
        return provider, model_id
    return "openrouter", ref


def model_ref(provider: str, model_id: str) -> str:
    return model_id if provider == "openrouter" else f"{provider}:{model_id}"


def _new_client(provider: str):
    if provider == "openrouter":
        api_key = get_secret(KEY_PROVIDERS[provider][0])
        # OPENROUTER_BASE_URL points the sidecar at a proxy or a local mock (used by the E2E test).
        return OpenRouterClient(api_key, base_url=os.environ.get("OPENROUTER_BASE_URL", OPENROUTER_BASE)) if api_key else None
    if provider == "anthropic":
        api_key = get_secret(KEY_PROVIDERS[provider][0])
        return AnthropicClient(api_key, base_url=os.environ.get("ANTHROPIC_BASE_URL")) if api_key else None
    if provider == "chatgpt":
        account = get_chatgpt_account()
        status = account.status()
        if status["connected"] and status["plan_enabled"]:
            return ChatGPTClient(account, base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    return None


def get_provider_client(provider: str):
    """The cached client for a provider, or None when it is not connected."""
    if provider not in _clients:
        client = _new_client(provider)
        if client is None:
            return None
        _clients[provider] = client
    return _clients[provider]


def reset_provider_clients() -> None:
    _clients.clear()


def connected_providers() -> list[str]:
    return [provider for provider in PROVIDERS if get_provider_client(provider)]
