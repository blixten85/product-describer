#!/usr/bin/env python3
"""
Product Description Generator — uses Ollama (local, free)

Usage:
  python main.py run products.csv [--output out.csv] [--workers 4]
  python main.py sync [--watch] [--interval 300] [--limit 50] [--workers 2]
"""

import csv
import json
import os
import re
import sys
import time
import argparse
import concurrent.futures
import logging
from pathlib import Path

import requests

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

SCRAPER_URL = os.getenv("SCRAPER_URL", "http://scraper:8000")
SCRAPER_API_KEY_FILE = os.getenv("SCRAPER_API_KEY_FILE", "")
SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY", "")

SYSTEM_PROMPT = (
    "Du är en assistent som skriver korta produktbeskrivningar på svenska. "
    "Svara ALLTID med endast giltig JSON i exakt detta format, utan kodstaket eller extra text:\n"
    '{"beskrivning": "...", "varför": "..."}\n'
    "- 'beskrivning' (1–2 meningar): kort, naturlig beskrivning av produkten.\n"
    "- 'varför' (1–2 meningar): varför någon skulle vilja eller behöva produkten.\n"
    "Variera stilen — ibland praktisk, ibland entusiastisk, ibland reflekterande. "
    "Undvik inledningar som 'Självklart!', 'Givetvis!' eller 'Absolut!'."
)


def user_message(site: str, product: str, price: str) -> str:
    return (
        f"Produkt: {product}\n"
        f"Butik: {site}\n"
        f"Pris: {price} kr"
    )


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def _parse_response(content: str) -> dict[str, str]:
    """Parse model output as JSON; fall back to plain-text in 'beskrivning'."""
    text = content.strip()
    match = _JSON_BLOCK.search(text)
    if match:
        try:
            data = json.loads(match.group(0))
            return {
                "beskrivning": str(data.get("beskrivning", "")).strip(),
                "varför": str(data.get("varför") or data.get("varfor", "")).strip(),
            }
        except json.JSONDecodeError:
            pass
    return {"beskrivning": text, "varför": ""}


def generate_description(site: str, product: str, price: str,
                          ollama_url: str = OLLAMA_URL,
                          model: str = OLLAMA_MODEL) -> dict[str, str]:
    """Generate a description+why pair. Returns dict with 'beskrivning' and 'varför'."""
    resp = requests.post(
        f"{ollama_url}/api/chat",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message(site, product, price)},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.8},
        },
        timeout=120,
    )
    resp.raise_for_status()
    return _parse_response(resp.json()["message"]["content"])


def load_csv(path: str) -> tuple[list[dict], list[str]]:
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Filen hittades inte: {path}")
    with open(resolved, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    return rows, fieldnames


def ollama_available(ollama_url: str = OLLAMA_URL) -> bool:
    try:
        requests.get(f"{ollama_url}/api/tags", timeout=3)
        return True
    except Exception:
        return False


def _read_scraper_api_key() -> str:
    if SCRAPER_API_KEY_FILE and Path(SCRAPER_API_KEY_FILE).is_file():
        return Path(SCRAPER_API_KEY_FILE).read_text().strip()
    return SCRAPER_API_KEY


def _scraper_headers() -> dict[str, str]:
    key = _read_scraper_api_key()
    if not key:
        raise RuntimeError(
            "Saknar API-nyckel för scrapern. Sätt SCRAPER_API_KEY eller SCRAPER_API_KEY_FILE."
        )
    return {"X-API-Key": key}


def fetch_products_missing_description(scraper_url: str, limit: int) -> list[dict]:
    resp = requests.get(
        f"{scraper_url}/products",
        params={"missing_description": "true", "limit": limit},
        headers=_scraper_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("products", [])


def push_description(scraper_url: str, product_id: int, beskrivning: str, varför: str) -> None:
    resp = requests.put(
        f"{scraper_url}/products/{product_id}/description",
        headers={**_scraper_headers(), "Content-Type": "application/json"},
        json={"description": beskrivning, "why": varför},
        timeout=30,
    )
    resp.raise_for_status()


def cmd_run(args) -> None:
    if not ollama_available(args.ollama_url):
        print(f"Kan inte ansluta till Ollama på {args.ollama_url}", file=sys.stderr)
        sys.exit(1)

    rows, fieldnames = load_csv(args.input)
    total = len(rows)
    print(f"Bearbetar {total} produkter med {args.model} ({args.workers} parallella)...")

    results: dict[int, dict[str, str]] = {}
    start = time.time()

    def process(idx_row):
        idx, row = idx_row
        try:
            return idx, generate_description(
                row.get("Site", ""),
                row.get("Product", ""),
                row.get("Price (SEK)", ""),
                ollama_url=args.ollama_url,
                model=args.model,
            )
        except Exception:
            return idx, {"beskrivning": "", "varför": ""}

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(process, (i, r)): i for i, r in enumerate(rows)}
        done = 0
        for fut in concurrent.futures.as_completed(futures):
            idx, parts = fut.result()
            results[idx] = parts
            done += 1
            elapsed = time.time() - start
            rate = done / elapsed if elapsed > 0 else 0
            eta = int((total - done) / rate) if rate > 0 else 0
            print(f"  {done}/{total}  {rate:.1f}/s  ETA {eta}s   ", end="\r", flush=True)

    print()
    output = args.output or Path(args.input).stem + "_med_beskrivning.csv"
    out_fields = fieldnames + ["Beskrivning", "Varför"]
    with open(output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields)
        writer.writeheader()
        for i, row in enumerate(rows):
            parts = results.get(i, {"beskrivning": "", "varför": ""})
            row["Beskrivning"] = parts.get("beskrivning", "")
            row["Varför"] = parts.get("varför", "")
            writer.writerow(row)

    ok = sum(1 for v in results.values() if v.get("beskrivning"))
    print(f"Klart! {ok}/{total} beskrivningar sparade i {output}")


def _site_from_url(url: str) -> str:
    if not url:
        return ""
    try:
        return url.split("/")[2]
    except IndexError:
        return ""


def _process_one(product: dict, ollama_url: str, model: str):
    try:
        parts = generate_description(
            site=_site_from_url(product.get("url", "")),
            product=product.get("title", ""),
            price=str(product.get("current_price", "")),
            ollama_url=ollama_url,
            model=model,
        )
        return product["id"], parts, None
    except Exception as e:
        return product["id"], None, str(e)


def cmd_sync(args) -> None:
    log = logging.getLogger("describer.sync")
    if not ollama_available(args.ollama_url):
        log.error("Kan inte ansluta till Ollama på %s", args.ollama_url)
        sys.exit(1)

    while True:
        try:
            products = fetch_products_missing_description(args.scraper_url, args.limit)
        except Exception as e:
            log.error("Kunde inte hämta från scrapern: %s", e)
            products = []

        if products:
            log.info("Hämtade %d produkter utan beskrivning", len(products))
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = [
                    pool.submit(_process_one, p, args.ollama_url, args.model) for p in products
                ]
                for fut in concurrent.futures.as_completed(futures):
                    pid, parts, err = fut.result()
                    if err or not parts or not parts.get("beskrivning"):
                        log.warning("Hoppar över produkt %s: %s", pid, err or "tomt svar")
                        continue
                    try:
                        push_description(
                            args.scraper_url, pid, parts["beskrivning"], parts.get("varför", "")
                        )
                        log.info("Beskrev produkt %s", pid)
                    except Exception as e:
                        log.error("Kunde inte spara beskrivning för %s: %s", pid, e)
        else:
            log.info("Inga produkter att beskriva just nu")

        if not args.watch:
            return
        time.sleep(args.interval)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    parser = argparse.ArgumentParser(description="Generera produktbeskrivningar med Ollama")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Kör mot en CSV-fil")
    p_run.add_argument("input", help="CSV-fil (kolumner: Site, Product, Price (SEK), Link)")
    p_run.add_argument("--output", help="Output-fil (default: <input>_med_beskrivning.csv)")
    p_run.add_argument("--model", default=OLLAMA_MODEL)
    p_run.add_argument("--ollama-url", default=OLLAMA_URL)
    p_run.add_argument("--workers", type=int, default=2)
    p_run.set_defaults(func=cmd_run)

    p_sync = sub.add_parser("sync", help="Hämta produkter från scrapern och skriv tillbaka beskrivningar")
    p_sync.add_argument("--scraper-url", default=SCRAPER_URL)
    p_sync.add_argument("--limit", type=int, default=50, help="Max antal produkter per körning")
    p_sync.add_argument("--watch", action="store_true", help="Loopa istället för att köra en gång")
    p_sync.add_argument("--interval", type=int, default=300, help="Sekunder mellan loopar (med --watch)")
    p_sync.add_argument("--model", default=OLLAMA_MODEL)
    p_sync.add_argument("--ollama-url", default=OLLAMA_URL)
    p_sync.add_argument("--workers", type=int, default=2)
    p_sync.set_defaults(func=cmd_sync)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
