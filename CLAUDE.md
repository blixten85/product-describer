# product-describer — Claude Code Guide

Generates Swedish product descriptions using a local LLM via Ollama. Accepts a CSV file (Site, Product, Price, Link) and outputs the same CSV with added `Beskrivning` and `Varför` columns.

## Tech Stack

- Python 3, Flask (web UI), Gunicorn
- Ollama (local LLM, default model: `llama3.1:8b`)
- Docker / Docker Compose

## Dev Commands

```bash
pip install -r requirements.txt
python app.py               # Start Flask dev server (web UI)
python main.py run products.csv   # CLI batch mode
```

## Docker

```bash
docker compose up -d
# Pull model once (~5 GB):
docker exec product-describer-ollama ollama pull llama3.1:8b
# Open http://your-server:5000
```

## Modes

- **CSV mode** — drag-and-drop CSV in web UI, or `python main.py run <file>`
- **Sync mode** — pull from scraper API, generate descriptions, write back; started via Docker Compose profile

## Conventions

- All config (Ollama URL, scraper API URL) via environment variables
- Never hardcode server addresses or credentials
- Keep prompts in a single location so they are easy to tune
