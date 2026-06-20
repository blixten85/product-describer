import json

import pytest
from cryptography.fernet import Fernet

import provider_config


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(provider_config, "CREDENTIALS_DIR", tmp_path / "credentials")
    monkeypatch.setattr(provider_config, "ORDER_FILE", tmp_path / "provider_order.json")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv(provider_config.MASTER_KEY_ENV_VAR, raising=False)


@pytest.fixture
def master_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv(provider_config.MASTER_KEY_ENV_VAR, key)
    return key


class TestApiKeyRoundTrip:
    def test_set_then_get(self, master_key):
        provider_config.set_api_key("anthropic", "sk-ant-test")
        assert provider_config.get_api_key("anthropic") == "sk-ant-test"

    def test_strips_whitespace(self, master_key):
        provider_config.set_api_key("openai", "  sk-test  ")
        assert provider_config.get_api_key("openai") == "sk-test"

    def test_unset_key_is_empty_string(self, master_key):
        assert provider_config.get_api_key("openai") == ""

    def test_env_var_overrides_stored_key(self, master_key, monkeypatch):
        provider_config.set_api_key("openai", "stored-key")
        monkeypatch.setenv("OPENAI_API_KEY", "env-key")
        assert provider_config.get_api_key("openai") == "env-key"


class TestMissingMasterKey:
    def test_set_api_key_raises_clear_error_without_master_key(self):
        with pytest.raises(RuntimeError, match=provider_config.MASTER_KEY_ENV_VAR):
            provider_config.set_api_key("openai", "sk-test")

    def test_legacy_plaintext_file_still_readable_without_master_key(self):
        provider_config.CREDENTIALS_DIR.mkdir(parents=True)
        (provider_config.CREDENTIALS_DIR / "openai_api_key").write_text("plain-legacy-key")
        assert provider_config.get_api_key("openai") == "plain-legacy-key"


class TestExtraFields:
    def test_azure_requires_endpoint_and_deployment_to_be_ready(self, master_key):
        provider_config.set_api_key("azure_openai", "azure-key")
        assert not provider_config.is_provider_ready("azure_openai")
        assert "azure_openai" not in provider_config.configured_providers()

        provider_config.set_provider_config(
            "azure_openai",
            {"api_key": "azure-key", "endpoint": "https://x.openai.azure.com", "deployment": "gpt4"},
        )
        assert provider_config.is_provider_ready("azure_openai")
        assert "azure_openai" in provider_config.configured_providers()

    def test_updating_key_preserves_previously_saved_extra_fields(self, master_key):
        provider_config.set_provider_config(
            "azure_openai",
            {"api_key": "old-key", "endpoint": "https://x.openai.azure.com", "deployment": "gpt4"},
        )
        provider_config.set_api_key("azure_openai", "new-key")
        config = provider_config.get_provider_config("azure_openai")
        assert config["api_key"] == "new-key"
        assert config["endpoint"] == "https://x.openai.azure.com"
        assert config["deployment"] == "gpt4"

    def test_providers_without_extra_fields_are_ready_with_just_a_key(self, master_key):
        provider_config.set_api_key("gemini", "gemini-key")
        assert provider_config.is_provider_ready("gemini")


class TestConfiguredProvidersAndOrder:
    def test_configured_providers_lists_only_ready_ones(self, master_key):
        provider_config.set_api_key("anthropic", "a-key")
        provider_config.set_api_key("azure_openai", "azure-key")  # missing endpoint/deployment
        assert provider_config.configured_providers() == ["anthropic"]

    def test_get_order_falls_back_to_configured_providers(self, master_key):
        provider_config.set_api_key("openai", "o-key")
        provider_config.set_api_key("anthropic", "a-key")
        order = provider_config.get_order()
        assert {entry["provider"] for entry in order} == {"openai", "anthropic"}

    def test_get_order_filters_saved_order_to_configured(self, master_key):
        provider_config.set_api_key("anthropic", "a-key")
        provider_config.set_order([
            {"provider": "anthropic", "model": "claude-sonnet-4-6"},
            {"provider": "openai", "model": "gpt-4.1-mini"},
        ])
        order = provider_config.get_order()
        assert order == [{"provider": "anthropic", "model": "claude-sonnet-4-6"}]

    def test_get_order_appends_configured_provider_missing_from_saved_order(self, master_key):
        provider_config.set_api_key("anthropic", "a-key")
        provider_config.set_order([{"provider": "anthropic", "model": "claude-sonnet-4-6"}])
        provider_config.set_api_key("gemini", "g-key")  # configured after order was saved
        order = provider_config.get_order()
        assert [entry["provider"] for entry in order] == ["anthropic", "gemini"]
        assert order[1]["model"] == provider_config.DEFAULT_MODELS["gemini"]


class TestRemoveProviderConfig:
    def test_remove_deletes_key_and_drops_provider_from_configured(self, master_key):
        provider_config.set_api_key("openai", "o-key")
        assert "openai" in provider_config.configured_providers()
        provider_config.remove_provider_config("openai")
        assert provider_config.get_api_key("openai") == ""
        assert "openai" not in provider_config.configured_providers()

    def test_remove_drops_provider_from_get_order(self, master_key):
        provider_config.set_api_key("anthropic", "a-key")
        provider_config.set_api_key("openai", "o-key")
        provider_config.remove_provider_config("openai")
        order = provider_config.get_order()
        assert [entry["provider"] for entry in order] == ["anthropic"]

    def test_remove_is_a_noop_for_unconfigured_provider(self, master_key):
        provider_config.remove_provider_config("openai")  # should not raise


class TestBuildChain:
    def test_returns_none_when_nothing_configured(self, master_key):
        assert provider_config.build_chain() is None

    def test_builds_chain_with_configured_providers_only(self, master_key):
        provider_config.set_api_key("anthropic", "a-key")
        provider_config.set_provider_config(
            "azure_openai",
            {"api_key": "azure-key", "endpoint": "https://x.openai.azure.com", "deployment": "gpt4"},
        )
        chain = provider_config.build_chain()
        assert chain is not None
        names = [spec.provider.name for spec in chain._specs]
        assert names == ["anthropic", "azure_openai"]
