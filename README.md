# product-describer

Genererar svenska produktbeskrivningar med ett lokalt LLM via [Ollama](https://ollama.com) — helt gratis, körs på din egen server.

**Input:** CSV-fil med kolumnerna `Site, Product, Price (SEK), Link`  
**Output:** Samma CSV med en extra kolumn `Beskrivning`

## Starta

```bash
git clone https://github.com/blixten85/product-describer
cd product-describer
mkdir data
docker compose up -d

# Ladda ner modellen första gången (5 GB, görs bara en gång)
docker exec product-describer-ollama-1 ollama pull llama3.1:8b
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
# Byt modell
docker exec product-describer-ollama-1 ollama pull qwen2.5:7b
```

## GPU-stöd

Avkommentera `deploy`-sektionen i `compose.yml` (kräver `nvidia-container-toolkit`).

## Tiduppskattning (CPU)

| Artiklar | 1 worker | 2 workers |
|----------|----------|-----------|
| 1 000    | ~50 min  | ~25 min   |
| 8 000    | ~7 tim   | ~3.5 tim  |
