# product-describer

[![Docker](https://github.com/blixten85/product-describer/actions/workflows/docker.yml/badge.svg)](https://github.com/blixten85/product-describer/actions/workflows/docker.yml)
[![CodeQL](https://github.com/blixten85/product-describer/actions/workflows/codeql.yml/badge.svg)](https://github.com/blixten85/product-describer/actions/workflows/codeql.yml)
[![Release](https://img.shields.io/github/v/release/blixten85/product-describer)](https://github.com/blixten85/product-describer/releases)
[![Image](https://ghcr-badge.egpl.dev/blixten85/product-describer/size?color=blue&label=image)](https://github.com/blixten85/product-describer/pkgs/container/product-describer)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Generates Swedish product descriptions using a local LLM via [Ollama](https://ollama.com) — completely free, runs on your own server.

**Input:** CSV file with columns `Site, Product, Price (SEK), Link`  
**Output:** Same CSV with two extra columns — `Beskrivning` (description) and `Varför` (why you'd want it)

Two modes are supported:

- **CSV** — drag and drop in the web UI, or run `python main.py run products.csv`.
- **Sync** — pull products directly from the [scraper](https://github.com/blixten85/scraper) API, generate descriptions, and write them back. Started with a docker-compose profile (see below).

## Getting started

```bash
# Place compose.yml on your server and run:
docker compose up -d

# Pull the model once (~5 GB, only needed once)
docker exec product-describer-ollama ollama pull llama3.1:8b
```

Open **http://your-server:5000**

## Usage

1. Drag and drop a CSV file
2. Select model and number of workers
3. Click **Generera** — processing runs locally in the background
4. Download the CSV when complete

**Tip:** Want to refine descriptions that aren't quite right?  
Upload the finished CSV to [Claude.ai](https://claude.ai) and ask it to improve selected rows — included in your Pro subscription.

## Models

| Model | Size | Quality | Swedish |
|-------|------|---------|---------|
| `llama3.1:8b` | 5 GB | Good | Good |
| `qwen2.5:7b` | 4.7 GB | Good | Very good |
| `mistral:7b` | 4.1 GB | OK | OK |

```bash
docker exec product-describer-ollama ollama pull qwen2.5:7b
```

## Hardware (Ryzen 5 7430U, 16 GB RAM)

The server has an **integrated AMD Barcelo GPU** (Vega/GCN-5) sharing system memory with the CPU.
ROCm does not officially support iGPUs, but can be attempted with `HSA_OVERRIDE_GFX_VERSION=9.0.0` — uncomment
the relevant lines in `compose.yml`. Without GPU, the model runs on CPU (AVX2):

| Model | Speed (CPU) |
|-------|-------------|
| `llama3.1:8b` | ~3–5 tok/s |
| `qwen2.5:7b` | ~4–6 tok/s |

With 2–4 workers at ~3 sec/item, 8,000 items takes roughly 3–4 hours. Best run overnight.

## GPU (optional)

See the comments in `compose.yml` for AMD ROCm (iGPU workaround) and NVIDIA.

## Sync mode (scraper integration)

Set `SYNC_ENABLED=true` and the main container also runs a background
worker that polls the [scraper](https://github.com/blixten85/scraper) API
for products without descriptions, generates them via Ollama, and writes
them back. No extra container needed.

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

The worker reads the scraper's API key from
`${DOCKER}/scraper/credentials/api_key` (mounted read-only). For a
one-shot run from the CLI:

```bash
docker compose exec product-describer python main.py sync --limit 50
```
