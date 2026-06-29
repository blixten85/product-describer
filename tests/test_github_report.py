import os

import github_report
from github_report import _redact, report_error_to_github


class TestRedact:
    def test_redacts_secret_like_env_var_value(self, monkeypatch):
        monkeypatch.setenv("MY_API_KEY", "supersecretvalue123")
        assert "supersecretvalue123" not in _redact("error with supersecretvalue123 in it")
        assert "[REDACTED]" in _redact("error with supersecretvalue123 in it")

    def test_does_not_redact_short_values(self, monkeypatch):
        monkeypatch.setenv("SOME_KEY", "abc")
        text = _redact("contains abc here")
        assert "abc here" in text

    def test_redacts_email(self):
        assert "[EMAIL REDACTED]" in _redact("failed for anders.eriksson@denied.se")
        assert "anders.eriksson@denied.se" not in _redact("failed for anders.eriksson@denied.se")

    def test_redacts_home_path(self):
        assert "/home/[user]/" in _redact("File \"/home/berduf/GitHub/app.py\", line 1")
        assert "berduf" not in _redact("File \"/home/berduf/GitHub/app.py\", line 1")

    def test_redacts_known_key_patterns(self):
        assert "[REDACTED]" in _redact("token=ghp_abcdefghijklmnopqrstuvwxyz0123")
        assert "ghp_" not in _redact("token=ghp_abcdefghijklmnopqrstuvwxyz0123")


class TestReportErrorToGithub:
    def test_returns_none_without_token(self, monkeypatch):
        monkeypatch.delenv("GITHUB_ERROR_REPORT_TOKEN", raising=False)
        result = report_error_to_github("blixten85/test", "title", ValueError("x"))
        assert result is None

    def test_never_raises_on_network_failure(self, monkeypatch):
        monkeypatch.setenv("GITHUB_ERROR_REPORT_TOKEN", "fake-token")
        monkeypatch.setattr(github_report, "_report_times", [])

        def boom(*args, **kwargs):
            raise github_report.requests.RequestException("network down")

        monkeypatch.setattr(github_report.requests, "get", boom)
        monkeypatch.setattr(github_report.requests, "post", boom)
        result = report_error_to_github("blixten85/test", "title", ValueError("x"))
        assert result is None

    def test_throttles_after_max_per_window(self, monkeypatch):
        monkeypatch.setenv("GITHUB_ERROR_REPORT_TOKEN", "fake-token")
        monkeypatch.setattr(github_report, "_report_times", [])
        monkeypatch.setattr(github_report, "_REPORT_MAX_PER_WINDOW", 2)
        calls = []
        monkeypatch.setattr(
            github_report.requests, "get",
            lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(github_report.requests.RequestException()),
        )
        monkeypatch.setattr(
            github_report.requests, "post",
            lambda *a, **k: (_ for _ in ()).throw(github_report.requests.RequestException()),
        )
        # De första två släpps igenom (och försöker nätverk), den tredje stoppas av spärren.
        report_error_to_github("blixten85/test", "t", ValueError("x"))
        report_error_to_github("blixten85/test", "t", ValueError("y"))
        report_error_to_github("blixten85/test", "t", ValueError("z"))
        assert len(calls) == 2
