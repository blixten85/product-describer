# product-describer

[![CI](https://github.com/blixten85/product-describer/actions/workflows/ci.yml/badge.svg)](https://github.com/blixten85/product-describer/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/blixten85/product-describer)](https://github.com/blixten85/product-describer/releases)
[![Image](https://ghcr-badge.egpl.dev/blixten85/product-describer/size?color=blue&label=image)](https://github.com/blixten85/product-describer/pkgs/container/product-describer)
[![License](https://img.shields.io/github/license/blixten85/product-describer)](LICENSE)

Generates Swedish product descriptions via your own Claude (Anthropic),
ChatGPT (OpenAI), Gemini (Google) and/or Azure OpenAI Service API account.

These are developer API accounts, billed separately from any consumer
subscription (ChatGPT Plus, Claude Pro, Gemini Advanced, GitHub/Microsoft
Copilot) you might also have — none of those subscriptions expose an API of
their own. Gemini's API has a free tier; the others are pay-per-use.

**Input:** CSV, Excel (`.xlsx`), `.txt`, `.docx`, or `.pdf` — for unstructured
formats, the AI finds every item mentioned automatically.  
**Output:** A CSV with two extra columns — `Beskrivning` (description) and
`Varför` (why you'd want it).

Two modes are supported:

- **File upload** — drag and drop in the web UI, or run `python main.py run products.csv`.
- **Sync** — pull products directly from the [scraper](https://github.com/blixten85/scraper) API, generate descriptions, and write them back. Started with a docker-compose profile (see below).

## Getting started

```bash
# Place docker-compose.yml on your server and run:
docker compose up -d
```

Open **http://your-server:5050** and add at least one API key under
**Inställningar** (or set `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` /
`GEMINI_API_KEY` / `AZURE_OPENAI_API_KEY` as environment variables before
starting the container). Azure OpenAI also needs its endpoint URL and
deployment name, set under **Inställningar** (they aren't secrets, so
there's no environment variable for them).

Saving a key under **Inställningar** encrypts it at rest, so you **must**
set `PROVIDER_CONFIG_MASTER_KEY` to a Fernet key before starting the
container — generate one with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

If `PROVIDER_CONFIG_MASTER_KEY` isn't set, saving a key under
**Inställningar** fails with a clear error rather than silently rejecting
your key — set the variable above and restart the container.

## Usage

1. Drag and drop a file (CSV, Excel, txt, docx or pdf)
2. Optionally pick a tone/length/audience, or write a custom direction
3. Click **Generera** — processing runs in the background
4. Download the CSV when complete

## Multi-provider failover

Configure one or more providers under **Inställningar**, in priority order
(e.g. Claude first, ChatGPT second). The active provider is used until it
reports a rate limit or quota error, at which point the job automatically
switches to the next configured provider — no manual intervention needed.

If every configured provider is exhausted, the job pauses instead of
failing. A background watcher checks periodically and resumes the job
automatically the instant a provider's quota is expected to reset (or
immediately if the API returned a `Retry-After` hint). No work already done
is lost — completed descriptions are saved incrementally as the job runs, so
a pause/resume cycle (even spanning multiple days) just picks up where it
left off.

## Sync mode (scraper integration)

Set `SYNC_ENABLED=true` and the main container also runs a background
worker that polls the [scraper](https://github.com/blixten85/scraper) API
for products without descriptions, generates them via your configured
provider chain, and writes them back. No extra container needed.

```bash
# .env (or shell)
export SYNC_ENABLED=true
# Internal docker hostname; the describer joins scraper's compose network
# so it can reach the API directly without going through any reverse proxy.
export SCRAPER_URL=http://scraper:8000
export SYNC_INTERVAL=300   # seconds between polls

docker compose up -d
```

If your scraper compose stack uses a non-default network name (anything
other than `scraper_default`), find it with
`docker inspect scraper -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'`
and set `SCRAPER_NETWORK=<that-name>` in your `.env`.

Set `SCRAPER_API_KEY=<your-key>` in `.env` (or point `SCRAPER_API_KEY_FILE` to the key file). For a
one-shot run from the CLI:

```bash
docker compose exec product-describer python main.py sync --limit 50
```
