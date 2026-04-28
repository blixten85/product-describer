# product-describer

Genererar svenska produktbeskrivningar med Claude Haiku via [Batches API](https://docs.anthropic.com/en/api/creating-message-batches) — 50% billigare än standard och hanterar 8 000+ artiklar asynkront.

**Input:** CSV-fil med kolumnerna `Site, Product, Price (SEK), Link`  
**Output:** Samma CSV med en extra kolumn `Beskrivning`

## Web UI (Docker)

```bash
# Starta
ANTHROPIC_API_KEY=sk-ant-... docker compose up -d

# Öppna http://localhost:5000
```

Ladda upp CSV, klicka "Generera beskrivningar" och ladda ner resultatet när det är klart.  
Batchen körs asynkront — sidan pollar automatiskt var 15:e sekund.

## CLI

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...

# Kör allt i ett steg (väntar tills klart)
python main.py run products.csv

# Eller stegvis (för stora filer som tar länge)
python main.py submit products.csv   # Skickar batch, sparar ID
python main.py status                # Kolla framsteg
python main.py collect               # Hämta när klar
```

## Kostnad (uppskattning)

Claude Haiku 4.5 via Batches API (50% rabatt):

| Artiklar | Ungefärlig kostnad |
|----------|--------------------|
| 1 000    | ~3 kr              |
| 8 000    | ~25 kr             |
| 50 000   | ~150 kr            |
