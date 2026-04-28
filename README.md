# product-describer

Genererar svenska produktbeskrivningar med ett lokalt LLM via [Ollama](https://ollama.com) — helt gratis, körs på din egen server.

**Input:** CSV-fil med kolumnerna `Site, Product, Price (SEK), Link`  
**Output:** Samma CSV med en extra kolumn `Beskrivning`

## Starta

```bash
# Lägg compose.yml på servern och kör:
docker compose up -d

# Ladda ner modellen första gången (~5 GB, görs bara en gång)
docker exec product-describer-ollama ollama pull llama3.1:8b
```

Öppna **http://din-server:5000**

## Användning

1. Dra och släpp CSV-fil
2. Välj modell och antal workers
3. Klicka **Generera** — bearbetningen körs lokalt i bakgrunden
4. Ladda ner CSV-filen när den är klar

**Tips:** Vill du finputsa beskrivningar som inte håller måttet?  
Ladda upp den färdiga CSV-filen till [Claude.ai](https://claude.ai) och be den förbättra utvalda rader — ingår i Pro-abonnemanget.

## Modeller

| Modell | Storlek | Kvalitet | Svenska |
|--------|---------|----------|---------|
| `llama3.1:8b` | 5 GB | Bra | Bra |
| `qwen2.5:7b` | 4.7 GB | Bra | Mycket bra |
| `mistral:7b` | 4.1 GB | OK | OK |

```bash
docker exec product-describer-ollama ollama pull qwen2.5:7b
```

## Hårdvara (Ryzen 5 7430U, 16 GB RAM)

Servern har en **integrerad AMD Barcelo-GPU** (Vega/GCN-5) som delar systemminnet med CPU:n.
ROCm stöder inte officiellt iGPU, men kan provas med `HSA_OVERRIDE_GFX_VERSION=9.0.0` — avkommentera
relevanta rader i `compose.yml`. Utan GPU körs modellen på CPU (AVX2) vilket ger ungefär:

| Modell | Hastighet (CPU) |
|--------|----------------|
| `llama3.1:8b` | ~3–5 tok/s |
| `qwen2.5:7b` | ~4–6 tok/s |

Med 2–4 workers och ~3 sek/artikel tar 8 000 artiklar ungefär 3–4 timmar.
Kör med fördel över natten.

## GPU (valfritt)

Se kommentarerna i `compose.yml` för AMD ROCm (iGPU-workaround) och NVIDIA.
