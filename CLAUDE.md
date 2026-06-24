# product-describer — Claude Code Guide

Generates Swedish product descriptions and "varför" justifications via the
user's own Claude (Anthropic), ChatGPT (OpenAI), Gemini (Google) and/or
Azure OpenAI Service API accounts, with automatic failover between
providers on rate limits/quota errors and automatic resume once a quota
resets. Accepts CSV, Excel, `.txt`, `.docx`, or `.pdf` and outputs a CSV
with added `Beskrivning` and `Varför` columns.

Multi-tenant: each account signs up with email+password and configures its
own provider keys — the operator never becomes financially responsible for
another account's API usage (jobs, keys, and failover order are fully
isolated per account_id). CLI mode (`main.py run`/`sync`) is unrelated to
accounts — it reads keys directly from environment variables instead.

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
auth.py             # Account signup/login (SQLite), legacy-data migration for the first account
main.py             # CLI (run / sync subcommands) — env-var keys, not account-scoped
providers.py        # Provider abstraction + ProviderChain failover engine
provider_config.py  # Per-account API key storage (config/accounts/<id>/credentials/) + failover order
prompts.py          # Builds the system prompt from tone/length/audience/custom options
extractors.py       # Turns an uploaded file into product rows (AI-assisted for unstructured formats)
templates/index.html, login.html, signup.html
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
  `config/accounts/<account_id>/credentials/` volume — never hardcoded, never committed
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
- Provider failover order lives in `config/accounts/<account_id>/provider_order.json`,
  filtered server-side to providers that currently have a key configured
- Every route except `/login`/`/signup` requires `@login_required`
  (`app.py`); `session["account_id"]` scopes provider config, jobs, and
  uploaded/output files — never read another account's job by guessing its
  id, ownership is checked on every job lookup
- The first account ever created automatically inherits any pre-existing
  global config/jobs from before the account system existed (`auth.py`'s
  `_migrate_legacy_data`) — only runs once, when the accounts table is empty
- A job's extracted rows and partial per-row results are cached to disk
  (`outputs/{job_id}_rows.json` / `_partial.json`) so a pause (provider
  exhaustion) never loses completed work, even across restarts
- Unexpected exceptions (Flask error handler, sync loop) call
  `report_error_to_github()` (`github_report.py`) — best-effort, opens a
  `@claude`-tagged GitHub issue with secrets/emails/paths redacted if
  `GITHUB_ERROR_REPORT_TOKEN` is set, no-ops otherwise
