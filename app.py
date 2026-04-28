#!/usr/bin/env python3
import csv
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from flask import Flask, jsonify, render_template, request, send_file

from main import MODEL, SYSTEM_PROMPT, build_requests, collect_descriptions, load_csv

UPLOAD_DIR = Path("uploads")
OUTPUT_DIR = Path("outputs")
JOBS_FILE = Path("jobs.json")

app = Flask(__name__)
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)


def load_jobs() -> list:
    if JOBS_FILE.exists():
        with open(JOBS_FILE) as f:
            return json.load(f)
    return []


def save_jobs(jobs: list) -> None:
    with open(JOBS_FILE, "w") as f:
        json.dump(jobs, f, indent=2)


def find_job(jobs: list, job_id: str) -> dict | None:
    return next((j for j in jobs if j["id"] == job_id), None)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "Ingen fil bifogad"}), 400
    f = request.files["file"]
    if not f.filename or not f.filename.lower().endswith(".csv"):
        return jsonify({"error": "Måste vara en CSV-fil"}), 400

    job_id = str(uuid.uuid4())[:8]
    safe_name = Path(f.filename).name
    input_path = UPLOAD_DIR / f"{job_id}_{safe_name}"
    f.save(input_path)

    try:
        rows, _ = load_csv(str(input_path))
    except Exception as e:
        input_path.unlink(missing_ok=True)
        return jsonify({"error": f"Kunde inte läsa CSV: {e}"}), 400

    if not rows:
        input_path.unlink(missing_ok=True)
        return jsonify({"error": "Filen är tom"}), 400

    if len(rows) > 100_000:
        input_path.unlink(missing_ok=True)
        return jsonify({"error": "Max 100 000 rader per batch"}), 400

    try:
        client = anthropic.Anthropic()
        batch = client.messages.batches.create(requests=build_requests(rows))
    except Exception as e:
        input_path.unlink(missing_ok=True)
        return jsonify({"error": f"API-fel: {e}"}), 500

    job = {
        "id": job_id,
        "batch_id": batch.id,
        "filename": safe_name,
        "input_path": str(input_path),
        "total": len(rows),
        "status": "processing",
        "succeeded": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "output_file": None,
    }
    jobs = load_jobs()
    jobs.insert(0, job)
    save_jobs(jobs)

    return jsonify({"job_id": job_id, "batch_id": batch.id, "total": len(rows)})


@app.route("/api/jobs")
def list_jobs():
    return jsonify(load_jobs())


@app.route("/api/jobs/<job_id>/refresh")
def refresh_job(job_id: str):
    jobs = load_jobs()
    job = find_job(jobs, job_id)
    if not job:
        return jsonify({"error": "Hittar inte jobbet"}), 404

    if job["status"] in ("done", "error"):
        return jsonify(job)

    client = anthropic.Anthropic()
    try:
        batch = client.messages.batches.retrieve(job["batch_id"])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    job["succeeded"] = batch.request_counts.succeeded

    if batch.processing_status == "ended":
        try:
            rows, fieldnames = load_csv(job["input_path"])
            descriptions = collect_descriptions(client, job["batch_id"])
            output_path = OUTPUT_DIR / f"{job_id}_med_beskrivning.csv"
            out_fields = fieldnames + ["Beskrivning"]
            with open(output_path, "w", newline="", encoding="utf-8") as out:
                writer = csv.DictWriter(out, fieldnames=out_fields)
                writer.writeheader()
                for i, row in enumerate(rows):
                    row["Beskrivning"] = descriptions.get(str(i), "")
                    writer.writerow(row)
            job["output_file"] = str(output_path)
            job["status"] = "done"
        except Exception as e:
            job["status"] = "error"
            job["error"] = str(e)

    save_jobs(jobs)
    return jsonify(job)


@app.route("/api/jobs/<job_id>/download")
def download_job(job_id: str):
    jobs = load_jobs()
    job = find_job(jobs, job_id)
    if not job or not job.get("output_file"):
        return jsonify({"error": "Ingen fil att ladda ner"}), 404
    stem = Path(job["filename"]).stem
    return send_file(
        job["output_file"],
        as_attachment=True,
        download_name=f"{stem}_med_beskrivning.csv",
        mimetype="text/csv",
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
