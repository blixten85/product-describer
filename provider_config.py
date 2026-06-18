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

from cryptography.fernet import Fernet, InvalidToken
from providers import AnthropicProvider, OpenAIProvider, ProviderChain, ProviderSpec

CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "config"))
CREDENTIALS_DIR = CONFIG_DIR / "credentials"
ORDER_FILE = CONFIG_DIR / "provider_order.json"
MASTER_KEY_ENV_VAR = "PROVIDER_CONFIG_MASTER_KEY"

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


def _get_fernet() -> Fernet:
    key = os.getenv(MASTER_KEY_ENV_VAR, "")
    if not key:
        raise RuntimeError(
            f"Missing encryption key: set {MASTER_KEY_ENV_VAR} to a Fernet key."
        )
    return Fernet(key.encode())


def _decrypt_stored_api_key(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ""
    try:
        return _get_fernet().decrypt(raw.encode()).decode().strip()
    except (InvalidToken, ValueError):
        # Backward compatibility with legacy plaintext files.
        return raw


def get_api_key(provider_name: str) -> str:
    env_value = os.getenv(f"{provider_name.upper()}_API_KEY", "")
    if env_value:
        return env_value
    path = _key_path(provider_name)
    if path.is_file():
        return _decrypt_stored_api_key(path.read_text())
    return ""


def set_api_key(provider_name: str, api_key: str) -> None:
    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    path = _key_path(provider_name)
    encrypted = _get_fernet().encrypt(api_key.strip().encode()).decode()
    path.write_text(encrypted)
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
