from unittest.mock import MagicMock

import pytest

from extractors import ExtractionError, extract_rows


class TestExtractRowsCsv:
    def test_parses_csv_directly(self, tmp_path):
        f = tmp_path / "products.csv"
        f.write_text("Site,Product,Price (SEK),Link\nbutik.se,Klocka,299,http://x\n", encoding="utf-8")

        rows, fieldnames = extract_rows(str(f))

        assert rows == [{"Site": "butik.se", "Product": "Klocka", "Price (SEK)": "299", "Link": "http://x"}]
        assert fieldnames == ["Site", "Product", "Price (SEK)", "Link"]


class TestExtractRowsUnstructured:
    def test_txt_requires_chain(self, tmp_path):
        f = tmp_path / "notes.txt"
        f.write_text("Klocka 299 kr", encoding="utf-8")

        with pytest.raises(ExtractionError):
            extract_rows(str(f), chain=None)

    def test_txt_uses_chain_to_extract_items(self, tmp_path):
        f = tmp_path / "notes.txt"
        f.write_text("En klocka för 299 kr på butik.se", encoding="utf-8")
        chain = MagicMock()
        chain.call.return_value = '[{"Product": "Klocka", "Site": "butik.se", "Price (SEK)": "299"}]'

        rows, fieldnames = extract_rows(str(f), chain=chain)

        assert rows == [{"Site": "butik.se", "Product": "Klocka", "Price (SEK)": "299", "Link": ""}]
        chain.call.assert_called_once()

    def test_raises_on_unparseable_ai_response(self, tmp_path):
        f = tmp_path / "notes.txt"
        f.write_text("Klocka 299 kr", encoding="utf-8")
        chain = MagicMock()
        chain.call.return_value = "inget JSON här"

        with pytest.raises(ExtractionError):
            extract_rows(str(f), chain=chain)

    def test_raises_when_no_products_found(self, tmp_path):
        f = tmp_path / "notes.txt"
        f.write_text("Bara text, inga prylar", encoding="utf-8")
        chain = MagicMock()
        chain.call.return_value = "[]"

        with pytest.raises(ExtractionError):
            extract_rows(str(f), chain=chain)


class TestExtractRowsUnsupported:
    def test_unsupported_extension_raises(self, tmp_path):
        f = tmp_path / "data.xml"
        f.write_text("<x/>", encoding="utf-8")

        with pytest.raises(ExtractionError):
            extract_rows(str(f))
