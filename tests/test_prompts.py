from prompts import build_system_prompt


class TestBuildSystemPrompt:
    def test_default_includes_base_and_variation(self):
        prompt = build_system_prompt()
        assert "beskrivning" in prompt
        assert "Variera stilen" in prompt

    def test_known_tone_overrides_default_variation(self):
        prompt = build_system_prompt({"tone": "humoristisk"})
        assert "humoristisk" in prompt
        assert "Variera stilen" not in prompt

    def test_unknown_tone_falls_back_to_variation(self):
        prompt = build_system_prompt({"tone": "okänd"})
        assert "Variera stilen" in prompt

    def test_length_instruction_included(self):
        prompt = build_system_prompt({"length": "kort"})
        assert "extra kort" in prompt

    def test_audience_included(self):
        prompt = build_system_prompt({"audience": "tonåringar"})
        assert "tonåringar" in prompt

    def test_custom_direction_included(self):
        prompt = build_system_prompt(custom_direction="Fokusera på hållbarhet")
        assert "hållbarhet" in prompt

    def test_empty_audience_and_direction_ignored(self):
        prompt = build_system_prompt({"audience": "  "}, custom_direction="   ")
        assert "Anpassa motiveringen" not in prompt
        assert "Extra instruktion" not in prompt
