# product-describer

Genererar svenska produktbeskrivningar med Claude Haiku via [Batches API](https://docs.anthropic.com/en/api/creating-message-batches) — 50% billigare än standard och hanterar 8 000+ artiklar asynkront.

**Input:** CSV-fil med kolumnerna `Site, Product, Price (SEK), Link`  
**Output:** Samma CSV med en extra kolumn `Beskrivning`

## Snabbstart

```bash
# Sätt API-nyckel
export ANTHROPIC_API_KEY=sk-ant-...

# Kör allt i ett steg
python main.py run products.csv

# Eller i steg (för stora filer som tar länge)
python main.py submit products.csv   # Skickar batch, sparar ID
python main.py status                # Kolla framsteg
python main.py collect               # Hämta resultat när klar
```

## Docker

```bash
# Kör med Docker (montera katalog med CSV-filen)
docker run --rm \
  -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  -v $(pwd):/data \
  ghcr.io/blixten85/product-describer \
  run /data/products.csv --output /data/products_med_beskrivning.csv
```

## Kostnad (uppskattning)

Claude Haiku 4.5 via Batches API:

| Artiklar | Ungefärlig kostnad |
|----------|--------------------|
| 1 000    | ~$0.30             |
| 8 000    | ~$2.50             |
| 50 000   | ~$15               |

Batches API är 50% billigare än realtids-API och hanterar upp till 100 000 förfrågningar per batch.
