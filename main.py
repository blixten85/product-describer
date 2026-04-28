#!/usr/bin/env python3
"""
Product Description Generator
Generates Swedish product descriptions using Claude Haiku via Batches API (50% cheaper).

Usage:
  python main.py run products.csv [--output out.csv]   # Submit + wait + collect
  python main.py submit products.csv                    # Submit batch, save ID
  python main.py status [batch-id]                      # Check progress
  python main.py collect [--batch-id ID] [--output f]  # Download results
"""

import csv
import json
import sys
import time
import argparse
from pathlib import Path

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

MODEL = "claude-haiku-4-5"
STATE_FILE = "batch_state.json"

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


def load_csv(path: str) -> tuple[list[dict], list[str]]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    return rows, fieldnames


def build_requests(rows: list[dict]) -> list[Request]:
    return [
        Request(
            custom_id=str(i),
            params=MessageCreateParamsNonStreaming(
                model=MODEL,
                max_tokens=300,
                system=SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": user_message(
                        r.get("Site", ""),
                        r.get("Product", ""),
                        r.get("Price (SEK)", ""),
                    ),
                }],
            ),
        )
        for i, r in enumerate(rows)
    ]


def save_state(batch_id: str, input_file: str, total: int) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump({"batch_id": batch_id, "input_file": input_file, "total": total}, f, indent=2)


def load_state() -> dict:
    if not Path(STATE_FILE).exists():
        print(f"Hittar inte {STATE_FILE}. Kör 'submit' först.", file=sys.stderr)
        sys.exit(1)
    with open(STATE_FILE) as f:
        return json.load(f)


def collect_descriptions(client: anthropic.Anthropic, batch_id: str) -> dict[str, str]:
    descriptions: dict[str, str] = {}
    for result in client.messages.batches.results(batch_id):
        match result.result.type:
            case "succeeded":
                text = next(
                    (b.text for b in result.result.message.content if b.type == "text"), ""
                )
                descriptions[result.custom_id] = text.strip()
            case _:
                descriptions[result.custom_id] = ""
    return descriptions


def write_output(rows: list[dict], fieldnames: list[str], descriptions: dict[str, str], output: str) -> None:
    out_fields = fieldnames + ["Beskrivning"]
    with open(output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields)
        writer.writeheader()
        for i, row in enumerate(rows):
            row["Beskrivning"] = descriptions.get(str(i), "")
            writer.writerow(row)
    ok = sum(1 for v in descriptions.values() if v)
    print(f"Klart! {ok}/{len(rows)} beskrivningar sparade i {output}")


def cmd_submit(args) -> None:
    client = anthropic.Anthropic()
    rows, _ = load_csv(args.input)
    print(f"Laddar {len(rows)} produkter...")
    if len(rows) > 100_000:
        print("Varning: Batches API stöder max 100 000 förfrågningar per batch.", file=sys.stderr)
        sys.exit(1)

    batch = client.messages.batches.create(requests=build_requests(rows))
    print(f"Batch skapad: {batch.id}")
    save_state(batch.id, args.input, len(rows))
    print(f"Batch-ID sparat i {STATE_FILE}")
    print("Kör 'python main.py status' för att följa framsteg.")
    print("Kör 'python main.py collect' när den är klar.")


def cmd_status(args) -> None:
    client = anthropic.Anthropic()
    batch_id = args.batch_id or load_state()["batch_id"]
    batch = client.messages.batches.retrieve(batch_id)
    c = batch.request_counts
    total = c.processing + c.succeeded + c.errored + c.canceled + c.expired
    print(f"Batch:   {batch_id}")
    print(f"Status:  {batch.processing_status}")
    print(f"Klara:   {c.succeeded}/{total}")
    if c.errored:
        print(f"Fel:     {c.errored}")
    if batch.processing_status == "ended":
        print("→ Klar! Kör 'python main.py collect' för att hämta resultaten.")


def cmd_collect(args) -> None:
    client = anthropic.Anthropic()
    state = load_state()
    batch_id = args.batch_id or state["batch_id"]
    input_file = args.input or state["input_file"]

    batch = client.messages.batches.retrieve(batch_id)
    if batch.processing_status != "ended":
        print(f"Batch är inte klar än (status: {batch.processing_status})")
        sys.exit(1)

    rows, fieldnames = load_csv(input_file)
    print("Hämtar resultat...")
    descriptions = collect_descriptions(client, batch_id)
    output = args.output or Path(input_file).stem + "_med_beskrivning.csv"
    write_output(rows, fieldnames, descriptions, output)


def cmd_run(args) -> None:
    client = anthropic.Anthropic()
    rows, fieldnames = load_csv(args.input)
    print(f"Laddar {len(rows)} produkter...")

    batch = client.messages.batches.create(requests=build_requests(rows))
    print(f"Batch skapad: {batch.id}")
    save_state(batch.id, args.input, len(rows))

    print("Väntar på resultat (kan ta upp till en timme för stora batchar)...")
    while True:
        batch = client.messages.batches.retrieve(batch.id)
        c = batch.request_counts
        total = c.processing + c.succeeded + c.errored + c.canceled + c.expired
        print(f"  {c.succeeded}/{total} klara...   ", end="\r", flush=True)
        if batch.processing_status == "ended":
            break
        time.sleep(30)

    print()
    descriptions = collect_descriptions(client, batch.id)
    output = args.output or Path(args.input).stem + "_med_beskrivning.csv"
    write_output(rows, fieldnames, descriptions, output)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generera produktbeskrivningar med Claude Haiku (Batches API)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="Skicka batch, vänta och hämta resultat automatiskt")
    p.add_argument("input", help="CSV-fil (kolumner: Site, Product, Price (SEK), Link)")
    p.add_argument("--output", help="Output-fil (default: <input>_med_beskrivning.csv)")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("submit", help="Skicka batch och spara batch-ID")
    p.add_argument("input", help="CSV-fil")
    p.set_defaults(func=cmd_submit)

    p = sub.add_parser("status", help="Kolla batchens framsteg")
    p.add_argument("batch_id", nargs="?", help="Batch-ID (default: från batch_state.json)")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("collect", help="Hämta resultat och skriv output-CSV")
    p.add_argument("--batch-id", help="Batch-ID (default: från batch_state.json)")
    p.add_argument("--input", help="Original CSV (default: från batch_state.json)")
    p.add_argument("--output", help="Output-fil")
    p.set_defaults(func=cmd_collect)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
