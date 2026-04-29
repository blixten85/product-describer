#!/usr/bin/env python3
import csv
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from main import (
    OLLAMA_MODEL,
    OLLAMA_URL,
    SCRAPER_URL,
    _process_one,
    fetch_products_missing_description,
    generate_description,
    load_csv,
    ollama_available,
    push_description,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("describer")

UPLOAD_DIR = Path("uploads")
OUTPUT_DIR = Path("outputs")
JOBS_FILE = Path("jobs.json")

app = Flask(__name__)
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _load_from_disk() -> None:
    if JOBS_FILE.exists():
        with open(JOBS_FILE) as f:
            for job in json.load(f):
                if job["id"] not in _jobs:
                    _jobs[job["id"]] = job


def _save() -> None:
    with _lock:
        jobs = list(_jobs.values())
    with open(JOBS_FILE, "w") as f:
        json.dump(jobs, f, indent=2)


def _get_jobs() -> list[dict]:
    with _lock:
        return sorted(_jobs.values(), key=lambda j: j["created_at"], reverse=True)


_load_from_disk()


def _process(job_id: str, workers: int, model: str, ollama_url: str) -> None:
    with _lock:
        job = _jobs[job_id]
        job["status"] = "processing"

    try:
        rows, fieldnames = load_csv(job["input_path"])
        with _lock:
            job["total"] = len(rows)

        results: dict[int, dict[str, str]] = {}
        errors: list[str] = []
        import concurrent.futures

        def do(idx_row):
            idx, row = idx_row
            try:
                parts = generate_description(
                    row.get("Site", ""),
                    row.get("Product", ""),
                    row.get("Price (SEK)", ""),
                    ollama_url=ollama_url,
                    model=model,
                )
                if not parts.get("beskrivning"):
                    log.warning("job=%s row=%s empty beskrivning for %r", job_id, idx, row.get("Product", "")[:60])
                return idx, parts, None
            except Exception as e:
                log.exception("job=%s row=%s failed for %r", job_id, idx, row.get("Product", "")[:60])
                return idx, {"beskrivning": "", "varför": ""}, f"{type(e).__name__}: {e}"

        log.info("job=%s starting %d rows with model=%s workers=%d", job_id, len(rows), model, workers)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(do, (i, r)): i for i, r in enumerate(rows)}
            for fut in concurrent.futures.as_completed(futures):
                idx, parts, err = fut.result()
                results[idx] = parts
                if err and len(errors) < 10:
                    errors.append(err)
                with _lock:
                    job["succeeded"] = sum(1 for v in results.values() if v.get("beskrivning"))
                    if errors:
                        job["last_errors"] = errors
                if len(results) % 50 == 0:
                    _save()
        log.info("job=%s done — %d/%d succeeded", job_id, job["succeeded"], len(rows))

        output_path = OUTPUT_DIR / f"{job_id}_med_beskrivning.csv"
        out_fields = fieldnames + ["Beskrivning", "Varför"]
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=out_fields)
            writer.writeheader()
            for i, row in enumerate(rows):
                parts = results.get(i, {"beskrivning": "", "varför": ""})
                row["Beskrivning"] = parts.get("beskrivning", "")
                row["Varför"] = parts.get("varför", "")
                writer.writerow(row)

        with _lock:
            job["output_file"] = str(output_path)
            job["status"] = "done"

    except Exception as e:
        with _lock:
            job["status"] = "error"
            job["error"] = str(e)

    _save()


@app.route("/")
def index():
    return render_template("index.html",
                           default_model=os.getenv("OLLAMA_MODEL", OLLAMA_MODEL),
                           ollama_url=os.getenv("OLLAMA_URL", OLLAMA_URL))


@app.route("/api/status")
def api_status():
    url = os.getenv("OLLAMA_URL", OLLAMA_URL)
    return jsonify({"ollama": ollama_available(url), "ollama_url": url})


@app.route("/api/models")
def api_models():
    import requests as req
    url = os.getenv("OLLAMA_URL", OLLAMA_URL)
    try:
        r = req.get(f"{url}/api/tags", timeout=3)
        models = [m["name"] for m in r.json().get("models", [])]
        return jsonify({"models": models})
    except Exception:
        return jsonify({"models": []})


@app.route("/api/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "Ingen fil bifogad"}), 400
    f = request.files["file"]
    if not f.filename or not f.filename.lower().endswith(".csv"):
        return jsonify({"error": "Måste vara en CSV-fil"}), 400

    model = request.form.get("model", os.getenv("OLLAMA_MODEL", OLLAMA_MODEL))
    workers = int(request.form.get("workers", 2))
    ollama_url = os.getenv("OLLAMA_URL", OLLAMA_URL)

    if not ollama_available(ollama_url):
        return jsonify({"error": f"Kan inte ansluta till Ollama på {ollama_url}"}), 503

    job_id = str(uuid.uuid4())[:8]
    original_name = Path(f.filename).name
    input_path = UPLOAD_DIR / f"{job_id}.csv"
    f.save(input_path)

    try:
        rows, _ = load_csv(str(input_path))
    except Exception:
        input_path.unlink(missing_ok=True)
        return jsonify({"error": "Kunde inte läsa CSV-filen"}), 400

    if not rows:
        input_path.unlink(missing_ok=True)
        return jsonify({"error": "Filen är tom"}), 400

    job = {
        "id": job_id,
        "filename": original_name,
        "input_path": str(input_path),
        "model": model,
        "total": len(rows),
        "status": "queued",
        "succeeded": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "output_file": None,
    }
    with _lock:
        _jobs[job_id] = job
    _save()

    threading.Thread(target=_process, args=(job_id, workers, model, ollama_url), daemon=True).start()

    return jsonify({"job_id": job_id, "total": len(rows)})


@app.route("/api/jobs")
def list_jobs():
    return jsonify(_get_jobs())


@app.route("/api/jobs/<job_id>")
def get_job(job_id: str):
    with _lock:
        job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "Hittar inte jobbet"}), 404
    return jsonify(job)


@app.route("/api/jobs/<job_id>/download")
def download_job(job_id: str):
    with _lock:
        job = _jobs.get(job_id)
    if not job or not job.get("output_file"):
        return jsonify({"error": "Ingen fil att ladda ner"}), 404
    stem = Path(job["filename"]).stem
    return send_file(
        job["output_file"],
        as_attachment=True,
        download_name=f"{stem}_med_beskrivning.csv",
        mimetype="text/csv",
    )


def _sync_loop() -> None:
    import concurrent.futures
    import time

    interval = int(os.getenv("SYNC_INTERVAL", "300"))
    limit = int(os.getenv("SYNC_LIMIT", "50"))
    workers = int(os.getenv("SYNC_WORKERS", "2"))
    scraper_url = os.getenv("SCRAPER_URL", SCRAPER_URL)
    ollama_url = os.getenv("OLLAMA_URL", OLLAMA_URL)
    model = os.getenv("OLLAMA_MODEL", OLLAMA_MODEL)
    log.info("sync worker starting: scraper=%s interval=%ds workers=%d", scraper_url, interval, workers)

    while True:
        try:
            products = fetch_products_missing_description(scraper_url, limit)
        except Exception as e:
            log.error("sync: kunde inte hämta från scrapern: %s", e)
            products = []

        if products:
            log.info("sync: hämtade %d produkter utan beskrivning", len(products))
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(_process_one, p, ollama_url, model) for p in products]
                for fut in concurrent.futures.as_completed(futures):
                    pid, parts, err = fut.result()
                    if err or not parts or not parts.get("beskrivning"):
                        log.warning("sync: hoppar över produkt %s: %s", pid, err or "tomt svar")
                        continue
                    try:
                        push_description(scraper_url, pid, parts["beskrivning"], parts.get("varför", ""))
                        log.info("sync: beskrev produkt %s", pid)
                    except Exception as e:
                        log.error("sync: kunde inte spara beskrivning för %s: %s", pid, e)
        time.sleep(interval)


def _maybe_start_sync_worker() -> None:
    if os.getenv("SYNC_ENABLED", "").lower() not in ("1", "true", "yes"):
        return
    if not os.getenv("SCRAPER_URL"):
        log.warning("SYNC_ENABLED is set but SCRAPER_URL is missing — sync worker not started")
        return
    threading.Thread(target=_sync_loop, daemon=True, name="sync-worker").start()


_maybe_start_sync_worker()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
