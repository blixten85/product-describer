from unittest.mock import MagicMock

import pytest

from main import _process_one, _site_from_url, generate_description, load_csv, user_message
from providers import AllProvidersExhausted


class TestUserMessage:
    def test_formats_correctly(self):
        msg = user_message("butik.se", "Klocka", "299")
        assert "Klocka" in msg
        assert "butik.se" in msg
        assert "299" in msg


class TestLoadCsv:
    def test_loads_rows_and_fieldnames(self, tmp_path):
        f = tmp_path / "products.csv"
        f.write_text("Site,Product,Price (SEK),Link\nexempel.se,Klocka,299,http://ex.se\n", encoding="utf-8")
        rows, fieldnames = load_csv(str(f))
        assert len(rows) == 1
        assert rows[0]["Product"] == "Klocka"
        assert "Site" in fieldnames
        assert "Price (SEK)" in fieldnames

    def test_raises_on_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_csv(str(tmp_path / "nonexistent.csv"))

    def test_empty_file_returns_empty_rows(self, tmp_path):
        f = tmp_path / "empty.csv"
        f.write_text("Site,Product,Price (SEK),Link\n", encoding="utf-8")
        rows, fieldnames = load_csv(str(f))
        assert rows == []
        assert "Site" in fieldnames


class TestSiteFromUrl:
    def test_extracts_domain(self):
        assert _site_from_url("https://exempel.se/produkt/123") == "exempel.se"

    def test_empty_url_returns_empty_string(self):
        assert _site_from_url("") == ""

    def test_malformed_url_returns_empty_string(self):
        assert _site_from_url("not-a-url") == ""


class TestGenerateDescription:
    def test_delegates_to_chain(self):
        chain = MagicMock()
        chain.generate.return_value = {"beskrivning": "Bra.", "varför": "Ja."}

        result = generate_description(chain, "butik.se", "Klocka", "299")

        assert result == {"beskrivning": "Bra.", "varför": "Ja."}
        chain.generate.assert_called_once()
        args, _ = chain.generate.call_args
        assert "Klocka" in args[1]


class TestProcessOne:
    def test_success(self):
        chain = MagicMock()
        chain.generate.return_value = {"beskrivning": "Bra.", "varför": "Ja."}
        product = {"id": 1, "url": "https://exempel.se/p", "title": "Klocka", "current_price": 299}

        pid, parts, err = _process_one(chain, product)

        assert pid == 1
        assert parts["beskrivning"] == "Bra."
        assert err is None

    def test_all_providers_exhausted(self):
        chain = MagicMock()
        exc = AllProvidersExhausted.__new__(AllProvidersExhausted)
        from datetime import datetime, timezone
        exc.resume_at = datetime.now(timezone.utc)
        chain.generate.side_effect = exc
        product = {"id": 2, "url": "", "title": "Klocka", "current_price": 100}

        pid, parts, err = _process_one(chain, product)

        assert pid == 2
        assert parts is None
        assert isinstance(err, AllProvidersExhausted)

    def test_other_exception_returns_error_string(self):
        chain = MagicMock()
        chain.generate.side_effect = RuntimeError("boom")
        product = {"id": 3, "url": "", "title": "Klocka", "current_price": 100}

        pid, parts, err = _process_one(chain, product)

        assert pid == 3
        assert parts is None
        assert "boom" in err
