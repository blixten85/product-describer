"""Storage for provider API keys and failover order.

Keys are written to individual files under CONFIG_DIR/credentials/ rather
than into a single JSON blob, so each secret can get its own restrictive
file permissions. Never commit CONFIG_DIR — it's a mounted volume in
docker-compose.yml, the same pattern the scraper repo uses.
"""

import json
import os
import stat
from pathlib import Path

from providers import AnthropicProvider, OpenAIProvider, ProviderChain, ProviderSpec

CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "config"))
CREDENTIALS_DIR = CONFIG_DIR / "credentials"
ORDER_FILE = CONFIG_DIR / "provider_order.json"

PROVIDER_CLASSES = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
}

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4.1-mini",
}


def _validate_provider_name(provider_name: str) -> str:
    if provider_name not in PROVIDER_CLASSES:
        raise ValueError(f"Unknown provider: {provider_name}")
    return provider_name


def _key_path(provider_name: str) -> Path:
    provider_name = _validate_provider_name(provider_name)
    return CREDENTIALS_DIR / f"{provider_name}_api_key"


def get_api_key(provider_name: str) -> str:
    env_value = os.getenv(f"{provider_name.upper()}_API_KEY", "")
    if env_value:
        return env_value
    path = _key_path(provider_name)
    if path.is_file():
        return path.read_text().strip()
    return ""


def set_api_key(provider_name: str, api_key: str) -> None:
    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    path = _key_path(provider_name)
    path.write_text(api_key.strip())
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def configured_providers() -> list[str]:
    return [name for name in PROVIDER_CLASSES if get_api_key(name)]


def get_order() -> list[dict]:
    """Returns [{"provider": "anthropic", "model": "..."}], priority first."""
    if ORDER_FILE.is_file():
        with open(ORDER_FILE) as f:
            saved = json.load(f)
        configured = set(configured_providers())
        return [entry for entry in saved if entry["provider"] in configured]
    return [
        {"provider": name, "model": DEFAULT_MODELS[name]}
        for name in configured_providers()
    ]


def set_order(order: list[dict]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(ORDER_FILE, "w") as f:
        json.dump(order, f, indent=2)


def build_chain() -> ProviderChain | None:
    order = get_order()
    specs = []
    for entry in order:
        provider_name = entry["provider"]
        api_key = get_api_key(provider_name)
        if not api_key:
            continue
        provider_cls = PROVIDER_CLASSES[provider_name]
        specs.append(ProviderSpec(provider=provider_cls(api_key), model=entry["model"]))
    if not specs:
        return None
    return ProviderChain(specs)
