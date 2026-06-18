from datetime import datetime, timedelta, timezone

import pytest

from providers import (
    AllProvidersExhausted,
    Provider,
    ProviderChain,
    ProviderSpec,
    RateLimitExceeded,
    parse_description_response,
)


class FakeProvider(Provider):
    def __init__(self, name, replies=None, fail_times=0):
        self.name = name
        self._replies = list(replies or [])
        self._fail_times = fail_times
        self.calls = 0

    def generate(self, system_prompt, user_message, model):
        self.calls += 1
        if self._fail_times > 0:
            self._fail_times -= 1
            raise RateLimitExceeded(self.name)
        return self._replies.pop(0) if self._replies else '{"beskrivning": "ok", "varför": "ok"}'

    def available_models(self):
        return ["fake-model"]


class TestParseDescriptionResponse:
    def test_valid_json(self):
        result = parse_description_response('{"beskrivning": "En bra produkt.", "varför": "Du behöver den."}')
        assert result["beskrivning"] == "En bra produkt."
        assert result["varför"] == "Du behöver den."

    def test_json_with_varfor_fallback(self):
        result = parse_description_response('{"beskrivning": "Bra.", "varfor": "Behövs."}')
        assert result["varför"] == "Behövs."

    def test_json_embedded_in_text(self):
        raw = 'Här är svaret: {"beskrivning": "Produkt.", "varför": "Bra."} tack.'
        result = parse_description_response(raw)
        assert result["beskrivning"] == "Produkt."

    def test_invalid_json_falls_back_to_plain_text(self):
        result = parse_description_response("Det här är en beskrivning utan JSON.")
        assert result["beskrivning"] == "Det här är en beskrivning utan JSON."
        assert result["varför"] == ""

    def test_empty_string(self):
        result = parse_description_response("")
        assert result["beskrivning"] == ""
        assert result["varför"] == ""

    def test_strips_whitespace(self):
        result = parse_description_response('  {"beskrivning": "  Fin produkt.  ", "varför": " Ja. "}  ')
        assert result["beskrivning"] == "Fin produkt."
        assert result["varför"] == "Ja."


class TestProviderChain:
    def test_uses_first_provider_when_healthy(self):
        a = FakeProvider("a")
        chain = ProviderChain([ProviderSpec(a, "fake-model")])

        content = chain.call("sys", "msg")

        assert content == '{"beskrivning": "ok", "varför": "ok"}'
        assert chain.current_provider_name() == "a"

    def test_fails_over_to_next_provider_on_rate_limit(self):
        a = FakeProvider("a", fail_times=1)
        b = FakeProvider("b", replies=['{"beskrivning": "from b", "varför": ""}'])
        chain = ProviderChain([ProviderSpec(a, "m"), ProviderSpec(b, "m")])

        content = chain.call("sys", "msg")

        assert "from b" in content
        assert chain.current_provider_name() == "b"

    def test_raises_all_providers_exhausted(self):
        a = FakeProvider("a", fail_times=1)
        b = FakeProvider("b", fail_times=1)
        chain = ProviderChain([ProviderSpec(a, "m"), ProviderSpec(b, "m")])

        with pytest.raises(AllProvidersExhausted):
            chain.call("sys", "msg")

    def test_generate_parses_result(self):
        a = FakeProvider("a", replies=['{"beskrivning": "X", "varför": "Y"}'])
        chain = ProviderChain([ProviderSpec(a, "m")])

        result = chain.generate("sys", "msg")

        assert result == {"beskrivning": "X", "varför": "Y"}

    def test_requires_at_least_one_provider(self):
        with pytest.raises(ValueError):
            ProviderChain([])

    def test_exhausted_provider_retried_after_reset(self):
        a = FakeProvider("a")
        chain = ProviderChain([ProviderSpec(a, "m")])
        chain._exhausted_until[0] = datetime.now(timezone.utc) - timedelta(seconds=1)

        content = chain.call("sys", "msg")

        assert content == '{"beskrivning": "ok", "varför": "ok"}'
