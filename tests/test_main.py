import csv
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from main import _parse_response, load_csv, ollama_available, user_message


class TestParseResponse:
    def test_valid_json(self):
        result = _parse_response('{"beskrivning": "En bra produkt.", "varför": "Du behöver den."}')
        assert result["beskrivning"] == "En bra produkt."
        assert result["varför"] == "Du behöver den."

    def test_json_with_varfor_fallback(self):
        result = _parse_response('{"beskrivning": "Bra.", "varfor": "Behövs."}')
        assert result["varför"] == "Behövs."

    def test_json_embedded_in_text(self):
        raw = 'Här är svaret: {"beskrivning": "Produkt.", "varför": "Bra."} tack.'
        result = _parse_response(raw)
        assert result["beskrivning"] == "Produkt."

    def test_invalid_json_falls_back_to_plain_text(self):
        result = _parse_response("Det här är en beskrivning utan JSON.")
        assert result["beskrivning"] == "Det här är en beskrivning utan JSON."
        assert result["varför"] == ""

    def test_empty_string(self):
        result = _parse_response("")
        assert result["beskrivning"] == ""
        assert result["varför"] == ""

    def test_strips_whitespace(self):
        result = _parse_response('  {"beskrivning": "  Fin produkt.  ", "varför": " Ja. "}  ')
        assert result["beskrivning"] == "Fin produkt."
        assert result["varför"] == "Ja."


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


class TestOllamaAvailable:
    def test_returns_true_on_ok_response(self):
        mock_resp = MagicMock()
        mock_resp.ok = True
        with patch("main.requests.get", return_value=mock_resp):
            assert ollama_available("http://localhost:11434") is True

    def test_returns_false_on_error_response(self):
        mock_resp = MagicMock()
        mock_resp.ok = False
        with patch("main.requests.get", return_value=mock_resp):
            assert ollama_available("http://localhost:11434") is False

    def test_returns_false_on_connection_error(self):
        with patch("main.requests.get", side_effect=Exception("connection refused")):
            assert ollama_available("http://localhost:11434") is False


class TestUserMessage:
    def test_formats_correctly(self):
        msg = user_message("butik.se", "Klocka", "299")
        assert "Klocka" in msg
        assert "butik.se" in msg
        assert "299" in msg
