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
from providers import (
    AnthropicProvider,
    AzureOpenAIProvider,
    GeminiProvider,
    OpenAIProvider,
    ProviderChain,
    ProviderSpec,
)

CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "config"))
ACCOUNTS_DIR = CONFIG_DIR / "accounts"
MASTER_KEY_ENV_VAR = "PROVIDER_CONFIG_MASTER_KEY"


def credentials_dir(account_id: str) -> Path:
    return ACCOUNTS_DIR / account_id / "credentials"


def order_file(account_id: str) -> Path:
    return ACCOUNTS_DIR / account_id / "provider_order.json"

PROVIDER_CLASSES = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
    "azure_openai": AzureOpenAIProvider,
}

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4.1-mini",
    "gemini": "gemini-2.5-flash",
    "azure_openai": "",
}

# Fields beyond api_key that a provider needs before it can be used. Azure
# OpenAI has no fixed model list — the caller's own Azure resource maps a
# deployment name to whichever model they provisioned there.
EXTRA_FIELDS = {
    "azure_openai": [
        {"name": "endpoint", "label": "Azure-endpoint (https://<resurs>.openai.azure.com)"},
        {"name": "deployment", "label": "Deployment-namn"},
    ],
}


_KEY_FILENAMES = {name: f"{name}_api_key" for name in PROVIDER_CLASSES}


def _key_path(account_id: str, provider_name: str) -> Path:
    if provider_name not in _KEY_FILENAMES:
        raise ValueError(f"Unknown provider: {provider_name}")
    return credentials_dir(account_id) / _KEY_FILENAMES[provider_name]


def _get_fernet() -> Fernet:
    key = os.getenv(MASTER_KEY_ENV_VAR, "")
    if not key:
        raise RuntimeError(
            f"Missing encryption key: set {MASTER_KEY_ENV_VAR} to a Fernet key."
        )
    return Fernet(key.encode())


def _decrypt_stored_value(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ""
    if not os.getenv(MASTER_KEY_ENV_VAR, ""):
        # No master key configured means this file was never encrypted by
        # set_provider_config (which requires one) — it's a legacy plaintext file.
        return raw
    try:
        return _get_fernet().decrypt(raw.encode()).decode().strip()
    except (InvalidToken, ValueError):
        # Backward compatibility with legacy plaintext files.
        return raw


def _parse_config_blob(raw: str) -> dict:
    """A stored blob is either a JSON {"api_key": ..., <extra fields>} object
    (current format) or a bare key string (format used before extra fields
    like Azure's endpoint/deployment existed). Treat anything that doesn't
    parse as a JSON object with an api_key as a bare legacy key.
    """
    if not raw:
        return {"api_key": ""}
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "api_key" in data:
            return data
    except json.JSONDecodeError:
        pass
    return {"api_key": raw}


def get_provider_config(account_id: str, provider_name: str) -> dict:
    """Returns {"api_key": str, **extra fields}, "" for anything unset."""
    path = _key_path(account_id, provider_name)
    return _parse_config_blob(_decrypt_stored_value(path.read_text())) if path.is_file() else {"api_key": ""}


def set_provider_config(account_id: str, provider_name: str, updates: dict) -> None:
    credentials_dir(account_id).mkdir(parents=True, exist_ok=True)
    config = get_provider_config(account_id, provider_name)
    config.update(updates)
    config["api_key"] = config.get("api_key", "").strip()
    path = _key_path(account_id, provider_name)
    encrypted = _get_fernet().encrypt(json.dumps(config).encode()).decode()
    path.write_text(encrypted)
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def get_api_key(account_id: str, provider_name: str) -> str:
    return get_provider_config(account_id, provider_name).get("api_key", "")


def set_api_key(account_id: str, provider_name: str, api_key: str) -> None:
    set_provider_config(account_id, provider_name, {"api_key": api_key})


def is_provider_ready(provider_name: str, config: dict) -> bool:
    if not config.get("api_key"):
        return False
    return all(config.get(field["name"]) for field in EXTRA_FIELDS.get(provider_name, []))


def configured_providers(account_id: str) -> list[str]:
    return [
        name for name in PROVIDER_CLASSES
        if is_provider_ready(name, get_provider_config(account_id, name))
    ]


def get_order(account_id: str) -> list[dict]:
    """Returns [{"provider": "anthropic", "model": "..."}], priority first.

    A configured provider that's missing from a previously saved order (e.g.
    its key was added after the order was last saved) is appended at the end
    rather than dropped.
    """
    configured = configured_providers(account_id)
    order_path = order_file(account_id)
    if order_path.is_file():
        with open(order_path) as f:
            saved = json.load(f)
        order = [entry for entry in saved if entry["provider"] in configured]
    else:
        order = []
    seen = {entry["provider"] for entry in order}
    order.extend(
        {"provider": name, "model": DEFAULT_MODELS[name]}
        for name in configured
        if name not in seen
    )
    return order


def set_order(account_id: str, order: list[dict]) -> None:
    order_path = order_file(account_id)
    order_path.parent.mkdir(parents=True, exist_ok=True)
    with open(order_path, "w") as f:
        json.dump(order, f, indent=2)


def remove_provider_config(account_id: str, provider_name: str) -> None:
    path = _key_path(account_id, provider_name)
    if path.is_file():
        path.unlink()


def build_chain(account_id: str) -> ProviderChain | None:
    order = get_order(account_id)
    specs = []
    for entry in order:
        provider_name = entry["provider"]
        config = get_provider_config(account_id, provider_name)
        if not is_provider_ready(provider_name, config):
            continue
        provider_cls = PROVIDER_CLASSES[provider_name]
        extra = {field["name"]: config[field["name"]] for field in EXTRA_FIELDS.get(provider_name, [])}
        provider = provider_cls(config["api_key"], **extra)
        specs.append(ProviderSpec(provider=provider, model=entry["model"]))
    if not specs:
        return None
    return ProviderChain(specs)


def build_chain_from_env() -> ProviderChain | None:
    """Som build_chain(), men läser nycklar direkt från miljövariabler i
    DEFAULT_MODELS-ordning istället för ett kontos sparade inställningar —
    för CLI-läget (main.py run/sync), som inte är knutet till ett
    webbgränssnitts-konto."""
    specs = []
    for name in PROVIDER_CLASSES:
        api_key = os.getenv(f"{name.upper()}_API_KEY", "")
        if not api_key:
            continue
        extra = {}
        if name == "azure_openai":
            extra = {
                "endpoint": os.getenv("AZURE_OPENAI_ENDPOINT", ""),
                "deployment": os.getenv("AZURE_OPENAI_DEPLOYMENT", ""),
            }
            if not all(extra.values()):
                continue
        provider = PROVIDER_CLASSES[name](api_key, **extra)
        specs.append(ProviderSpec(provider=provider, model=DEFAULT_MODELS[name]))
    if not specs:
        return None
    return ProviderChain(specs)
    return ProviderChain(specs)
