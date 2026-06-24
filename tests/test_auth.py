import json

import pytest

import auth


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "accounts.db")
    monkeypatch.chdir(tmp_path)


class TestCreateAccount:
    def test_creates_account_with_valid_email_and_password(self):
        account_id, error = auth.create_account("user@example.com", "longenoughpw")
        assert error is None
        assert account_id is not None

    def test_rejects_invalid_email(self):
        account_id, error = auth.create_account("not-an-email", "longenoughpw")
        assert account_id is None
        assert "post" in error.lower()

    def test_rejects_short_password(self):
        account_id, error = auth.create_account("user@example.com", "short")
        assert account_id is None
        assert "tecken" in error.lower()

    def test_rejects_duplicate_email(self):
        auth.create_account("user@example.com", "longenoughpw")
        account_id, error = auth.create_account("user@example.com", "anotherpw123")
        assert account_id is None
        assert "registrerad" in error.lower()

    def test_email_is_case_insensitive(self):
        auth.create_account("User@Example.com", "longenoughpw")
        account_id, error = auth.create_account("user@example.com", "anotherpw123")
        assert account_id is None


class TestVerifyLogin:
    def test_correct_credentials_return_account_id(self):
        account_id, _ = auth.create_account("user@example.com", "longenoughpw")
        assert auth.verify_login("user@example.com", "longenoughpw") == account_id

    def test_wrong_password_returns_none(self):
        auth.create_account("user@example.com", "longenoughpw")
        assert auth.verify_login("user@example.com", "wrongpassword") is None

    def test_unknown_email_returns_none(self):
        assert auth.verify_login("nobody@example.com", "longenoughpw") is None

    def test_login_email_is_case_insensitive(self):
        account_id, _ = auth.create_account("user@example.com", "longenoughpw")
        assert auth.verify_login("User@Example.com", "longenoughpw") == account_id


class TestGetEmail:
    def test_returns_email_for_known_account(self):
        account_id, _ = auth.create_account("user@example.com", "longenoughpw")
        assert auth.get_email(account_id) == "user@example.com"

    def test_returns_none_for_unknown_account(self):
        assert auth.get_email("nonexistent") is None


class TestLegacyMigration:
    def test_first_account_inherits_legacy_credentials(self, tmp_path):
        legacy_creds = tmp_path / "credentials"
        legacy_creds.mkdir()
        (legacy_creds / "anthropic_api_key").write_text("encrypted-blob")

        account_id, _ = auth.create_account("user@example.com", "longenoughpw")

        import provider_config
        new_path = provider_config.credentials_dir(account_id) / "anthropic_api_key"
        assert new_path.is_file()
        assert new_path.read_text() == "encrypted-blob"
        assert not legacy_creds.exists()

    def test_first_account_inherits_legacy_order(self, tmp_path):
        (tmp_path / "provider_order.json").write_text('[{"provider": "anthropic", "model": "x"}]')

        account_id, _ = auth.create_account("user@example.com", "longenoughpw")

        import provider_config
        new_order = provider_config.order_file(account_id)
        assert new_order.is_file()
        assert json.loads(new_order.read_text()) == [{"provider": "anthropic", "model": "x"}]

    def test_first_account_inherits_legacy_jobs(self, tmp_path, monkeypatch):
        outputs = tmp_path / "outputs"
        outputs.mkdir()
        monkeypatch.setenv("OUTPUT_DIR", str(outputs))
        jobs_file = outputs / "jobs.json"
        jobs_file.write_text(json.dumps([{"id": "abc123", "status": "done"}]))

        account_id, _ = auth.create_account("user@example.com", "longenoughpw")

        jobs = json.loads(jobs_file.read_text())
        assert jobs[0]["account_id"] == account_id

    def test_second_account_does_not_get_legacy_data(self, tmp_path):
        legacy_creds = tmp_path / "credentials"
        legacy_creds.mkdir()
        (legacy_creds / "anthropic_api_key").write_text("encrypted-blob")

        first_id, _ = auth.create_account("first@example.com", "longenoughpw")
        second_id, _ = auth.create_account("second@example.com", "longenoughpw")

        import provider_config
        assert not (provider_config.credentials_dir(second_id) / "anthropic_api_key").is_file()
        assert (provider_config.credentials_dir(first_id) / "anthropic_api_key").is_file()
