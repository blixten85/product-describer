"""Builds the system prompt for description generation from UI options."""

BASE_PROMPT = (
    "Du är en assistent som skriver korta produktbeskrivningar på svenska. "
    "Svara ALLTID med endast giltig JSON i exakt detta format, utan kodstaket eller extra text:\n"
    '{"beskrivning": "...", "varför": "..."}\n'
    "- 'beskrivning' (1–2 meningar): kort, naturlig beskrivning av produkten.\n"
    "- 'varför' (1–2 meningar): varför någon skulle vilja eller behöva produkten.\n"
)

TONE_INSTRUCTIONS = {
    "saklig": "Håll tonen saklig och informativ.",
    "entusiastisk": "Skriv med entusiasm och energi.",
    "humoristisk": "Lägg in en lätt humoristisk touch.",
    "lyxig": "Skriv med en exklusiv, premium känsla.",
}

LENGTH_INSTRUCTIONS = {
    "kort": "Var extra kort — max en mening per fält.",
    "medel": "Använd 1–2 meningar per fält (standard).",
    "lang": "Du får använda upp till 3 meningar per fält om det behövs.",
}

DEFAULT_VARIATION = (
    "Variera stilen mellan produkter — ibland praktisk, ibland entusiastisk, ibland reflekterande. "
    "Undvik inledningar som 'Självklart!', 'Givetvis!' eller 'Absolut!'."
)


def build_system_prompt(options: dict | None = None, custom_direction: str = "") -> str:
    options = options or {}
    parts = [BASE_PROMPT]

    tone = options.get("tone")
    if tone in TONE_INSTRUCTIONS:
        parts.append(TONE_INSTRUCTIONS[tone])
    else:
        parts.append(DEFAULT_VARIATION)

    length = options.get("length")
    if length in LENGTH_INSTRUCTIONS:
        parts.append(LENGTH_INSTRUCTIONS[length])

    audience = options.get("audience", "").strip()
    if audience:
        parts.append(f"Anpassa motiveringen för målgruppen: {audience}.")

    custom_direction = (custom_direction or "").strip()
    if custom_direction:
        parts.append(f"Extra instruktion från användaren: {custom_direction}")

    return "\n".join(parts)
