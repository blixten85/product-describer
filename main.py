#!/usr/bin/env python3
"""
Product Description Generator — Claude / OpenAI, with automatic failover

Usage:
  python main.py run products.csv [--output out.csv] [--workers 4]
  python main.py sync [--watch] [--interval 300] [--limit 50] [--workers 2]
"""

import csv
import logging
import os
import sys
import time
import argparse
import concurrent.futures
from pathlib import Path

import requests

import provider_config
from extractors import extract_rows
from prompts import build_system_prompt
from providers import AllProvidersExhausted, ProviderChain

SCRAPER_URL = os.getenv("SCRAPER_URL", "http://scraper:8000")
SCRAPER_API_KEY_FILE = os.getenv("SCRAPER_API_KEY_FILE", "")
SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY", "")


def user_message(site: str, product: str, price: str) -> str:
    return (
        f"Produkt: {product}\n"
        f"Butik: {site}\n"
        f"Pris: {price} kr"
    )


_log = logging.getLogger("describer.generate")


def generate_description(
    chain: ProviderChain,
    site: str,
    product: str,
    price: str,
    options: dict | None = None,
    custom_direction: str = "",
) -> dict[str, str]:
    """Generate a description+why pair using the active provider in the chain."""
    system_prompt = build_system_prompt(options, custom_direction)
    return chain.generate(system_prompt, user_message(site, product, price))


def load_csv(path: str) -> tuple[list[dict], list[str]]:
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Filen hittades inte: {path}")
    return extract_rows(str(resolved))


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


def _require_chain() -> ProviderChain:
    chain = provider_config.build_chain()
    if chain is None:
        print(
            "Ingen AI-leverantör är konfigurerad. Lägg till en API-nyckel "
            "(t.ex. ANTHROPIC_API_KEY eller via inställningarna i webbgränssnittet).",
            file=sys.stderr,
        )
        sys.exit(1)
    return chain


def cmd_run(args) -> None:
    chain = _require_chain()
    rows, fieldnames = load_csv(args.input)
    total = len(rows)
    print(f"Bearbetar {total} produkter ({args.workers} parallella)...")

    results: dict[int, dict[str, str]] = {}
    start = time.time()
    exhausted = False

    def process(idx_row):
        idx, row = idx_row
        try:
            return idx, generate_description(
                chain,
                row.get("Site", ""),
                row.get("Product", ""),
                row.get("Price (SEK)", ""),
            ), None
        except AllProvidersExhausted as e:
            return idx, None, e
        except Exception:
            return idx, {"beskrivning": "", "varför": ""}, None

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(process, (i, r)): i for i, r in enumerate(rows)}
        done = 0
        for fut in concurrent.futures.as_completed(futures):
            idx, parts, exc = fut.result()
            if exc is not None:
                exhausted = True
                print(
                    f"\nAlla konfigurerade leverantörer är uttömda. "
                    f"Försök igen efter {exc.resume_at.isoformat()}.",
                    file=sys.stderr,
                )
                continue
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
    if exhausted:
        print(
            "Obs: körningen avbröts av kvotgräns innan alla rader hann behandlas. "
            "Kör samma kommando igen senare för resten (webb-UI återupptar automatiskt).",
            file=sys.stderr,
        )
        sys.exit(2)


def _site_from_url(url: str) -> str:
    if not url:
        return ""
    try:
        return url.split("/")[2]
    except IndexError:
        return ""


def _process_one(chain: ProviderChain, product: dict):
    try:
        parts = generate_description(
            chain,
            site=_site_from_url(product.get("url", "")),
            product=product.get("title", ""),
            price=str(product.get("current_price", "")),
        )
        return product["id"], parts, None
    except AllProvidersExhausted as e:
        return product["id"], None, e
    except Exception as e:
        return product["id"], None, str(e)


def cmd_sync(args) -> None:
    log = logging.getLogger("describer.sync")
    chain = _require_chain()

    while True:
        try:
            products = fetch_products_missing_description(args.scraper_url, args.limit)
        except Exception as e:
            log.error("Kunde inte hämta från scrapern: %s", e)
            products = []

        if products:
            log.info("Hämtade %d produkter utan beskrivning", len(products))
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = [pool.submit(_process_one, chain, p) for p in products]
                for fut in concurrent.futures.as_completed(futures):
                    pid, parts, err = fut.result()
                    if isinstance(err, AllProvidersExhausted):
                        log.warning(
                            "Alla leverantörer uttömda, försöker igen efter %s",
                            err.resume_at.isoformat(),
                        )
                        continue
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
    parser = argparse.ArgumentParser(description="Generera produktbeskrivningar med Claude/OpenAI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Kör mot en fil (CSV, Excel, txt, docx, pdf)")
    p_run.add_argument("input", help="Fil med produkter")
    p_run.add_argument("--output", help="Output-fil (default: <input>_med_beskrivning.csv)")
    p_run.add_argument("--workers", type=int, default=2)
    p_run.set_defaults(func=cmd_run)

    p_sync = sub.add_parser("sync", help="Hämta produkter från scrapern och skriv tillbaka beskrivningar")
    p_sync.add_argument("--scraper-url", default=SCRAPER_URL)
    p_sync.add_argument("--limit", type=int, default=50, help="Max antal produkter per körning")
    p_sync.add_argument("--watch", action="store_true", help="Loopa istället för att köra en gång")
    p_sync.add_argument("--interval", type=int, default=300, help="Sekunder mellan loopar (med --watch)")
    p_sync.add_argument("--workers", type=int, default=2)
    p_sync.set_defaults(func=cmd_sync)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
