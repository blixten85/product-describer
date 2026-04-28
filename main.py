#!/usr/bin/env python3
"""
Product Description Generator — uses Ollama (local, free)

Usage:
  python main.py run products.csv [--output out.csv] [--workers 4]
"""

import csv
import os
import sys
import time
import argparse
import concurrent.futures
from pathlib import Path

import requests

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

SYSTEM_PROMPT = (
    "Du är en assistent som skriver korta, personliga produktbeskrivningar på svenska. "
    "Skriv 2–3 meningar om varför någon skulle vilja eller behöva den här produkten. "
    "Variera stilen – ibland praktisk, ibland entusiastisk, ibland reflekterande. "
    "Undvik inledningar som 'Självklart!', 'Givetvis!' eller 'Absolut!'. "
    "Direkt och naturlig ton, som en personlig anteckning."
)


def user_message(site: str, product: str, price: str) -> str:
    return (
        f"Produkt: {product}\n"
        f"Butik: {site}\n"
        f"Pris: {price} kr\n\n"
        "Skriv en kort beskrivning (2–3 meningar) om varför man skulle vilja ha denna produkt."
    )


def generate_description(site: str, product: str, price: str,
                          ollama_url: str = OLLAMA_URL,
                          model: str = OLLAMA_MODEL) -> str:
    resp = requests.post(
        f"{ollama_url}/api/chat",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message(site, product, price)},
            ],
            "stream": False,
            "options": {"temperature": 0.8},
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"].strip()


def load_csv(path: str) -> tuple[list[dict], list[str]]:
    with open(path, newline="", encoding="utf-8") as f:
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


def cmd_run(args) -> None:
    if not ollama_available(args.ollama_url):
        print(f"Kan inte ansluta till Ollama på {args.ollama_url}", file=sys.stderr)
        sys.exit(1)

    rows, fieldnames = load_csv(args.input)
    total = len(rows)
    print(f"Bearbetar {total} produkter med {args.model} ({args.workers} parallella)...")

    results: dict[int, str] = {}
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
        except Exception as e:
            return idx, ""

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(process, (i, r)): i for i, r in enumerate(rows)}
        done = 0
        for fut in concurrent.futures.as_completed(futures):
            idx, desc = fut.result()
            results[idx] = desc
            done += 1
            elapsed = time.time() - start
            rate = done / elapsed if elapsed > 0 else 0
            eta = int((total - done) / rate) if rate > 0 else 0
            print(f"  {done}/{total}  {rate:.1f}/s  ETA {eta}s   ", end="\r", flush=True)

    print()
    output = args.output or Path(args.input).stem + "_med_beskrivning.csv"
    out_fields = fieldnames + ["Beskrivning"]
    with open(output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields)
        writer.writeheader()
        for i, row in enumerate(rows):
            row["Beskrivning"] = results.get(i, "")
            writer.writerow(row)

    ok = sum(1 for v in results.values() if v)
    print(f"Klart! {ok}/{total} beskrivningar sparade i {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generera produktbeskrivningar med Ollama")
    parser.add_argument("input", help="CSV-fil (kolumner: Site, Product, Price (SEK), Link)")
    parser.add_argument("--output", help="Output-fil (default: <input>_med_beskrivning.csv)")
    parser.add_argument("--model", default=OLLAMA_MODEL, help=f"Ollama-modell (default: {OLLAMA_MODEL})")
    parser.add_argument("--ollama-url", default=OLLAMA_URL, help=f"Ollama-URL (default: {OLLAMA_URL})")
    parser.add_argument("--workers", type=int, default=2, help="Antal parallella förfrågningar (default: 2)")
    parser.set_defaults(func=cmd_run)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
