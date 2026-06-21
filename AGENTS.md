# product-describer — AI Agent Guide

Generates Swedish product descriptions and "varför" justifications via the
user's own Claude (Anthropic), ChatGPT (OpenAI), Gemini (Google) and/or
Azure OpenAI Service API accounts, with automatic failover between
providers on rate limits/quota errors and automatic resume once a quota
resets. Accepts CSV, Excel, `.txt`, `.docx`, or `.pdf` and outputs a CSV
with added `Beskrivning` and `Varför` columns.

Note: none of the supported providers can be authenticated via a consumer
subscription (ChatGPT Plus, Claude Pro, Gemini Advanced, Copilot) — those
are billed and authenticated completely separately from the developer
API tier, by design on the providers' end. Gemini's API has a free tier;
the others are pay-per-use regardless of any subscription also held.

## Tech Stack

- Python 3, Flask (web UI), Gunicorn
- Anthropic, OpenAI and Google Gen AI SDKs (no local/self-hosted model)
- Docker / Docker Compose

## File Overview

```
app.py              # Flask web UI + job runner with pause/auto-resume
main.py             # CLI (run / sync subcommands)
providers.py        # Provider abstraction + ProviderChain failover engine
provider_config.py  # API key storage (config/credentials/) + failover order
prompts.py          # Builds the system prompt from tone/length/audience/custom options
extractors.py       # Turns an uploaded file into product rows (AI-assisted for unstructured formats)
templates/index.html
```

## Dev Commands

```bash
pip install -r requirements.txt
python app.py               # Start Flask dev server (web UI)
python main.py run products.csv   # CLI batch mode
pytest                       # Run tests
```

## Docker

```bash
docker compose up -d
# Open http://your-server:5050 and add an API key under Inställningar
```

## Modes

- **File upload** — drag-and-drop CSV/Excel/txt/docx/pdf in the web UI, or `python main.py run <file>`
- **Sync mode** — pull from scraper API, generate descriptions, write back; started via Docker Compose profile

## Conventions

- All config (API keys, scraper API URL) via environment variables or the
  `config/credentials/` volume — never hardcoded, never committed
- API keys (and, for Azure OpenAI, the endpoint/deployment that go with one)
  are saved via the web UI as a single encrypted-at-rest (Fernet) JSON blob
  per provider, using `PROVIDER_CONFIG_MASTER_KEY`; without it, saving a new
  key returns a clear error but reading a pre-existing legacy plaintext key
  file still works
- Adding a provider: a `Provider` subclass in `providers.py`, an entry in
  `PROVIDER_CLASSES`/`DEFAULT_MODELS` in `provider_config.py`, and a label
  in `PROVIDER_LABELS` — the settings UI picks it up with no HTML changes.
  If it needs config beyond an api_key (like Azure's endpoint/deployment),
  add it to `EXTRA_FIELDS` too
- Keep prompts in `prompts.py` so they're easy to tune in one place
- Provider failover order lives in `config/provider_order.json`, filtered
  server-side to providers that currently have a key configured
- A job's extracted rows and partial per-row results are cached to disk
  (`outputs/{job_id}_rows.json` / `_partial.json`) so a pause (provider
  exhaustion) never loses completed work, even across restarts

## Allowed
- Create branches
- Modify code
- Run tests
- Open PRs

## Forbidden
- Push directly to main/master
- Merge PRs
- Delete branches
- Disable workflows
- Modify secrets
- Change GitHub org settings

## Requirements
- All tests must pass
- Keep PRs focused
- Never include unrelated changes
- Never commit credentials
- Never force push
