#!/usr/bin/env python3
import functools
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.exceptions import HTTPException

import auth
import provider_config
from extractors import SUPPORTED_EXTENSIONS, extract_rows
from github_report import report_error_to_github
from main import (
    SCRAPER_URL,
    _process_one,
    fetch_products_missing_description,
    generate_description,
    push_description,
)
from providers import AllProvidersExhausted, PROVIDER_LABELS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("describer")

UPLOAD_DIR = Path("uploads")
OUTPUT_DIR = Path("outputs")
JOBS_FILE = OUTPUT_DIR / "jobs.json"
RESUME_CHECK_INTERVAL = int(os.getenv("RESUME_CHECK_INTERVAL", "120"))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB
app.secret_key = os.environ["FLASK_SECRET_KEY"]
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

_FORMULA_PREFIX = re.compile(r"^[=+\-@\t\r]")


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if "account_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Inte inloggad"}), 401
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.errorhandler(Exception)
def handle_unexpected_error(exc):
    """Make sure every error reaches the frontend as JSON.

    Without this, an unhandled exception (e.g. a missing
    PROVIDER_CONFIG_MASTER_KEY) falls through to Flask's HTML error page,
    which the settings UI's fetch().json() can't parse — so a real
    server-side misconfiguration shows up to the user as a confusing
    "unexpected token" error that looks like a rejected API key.
    """
    if isinstance(exc, HTTPException):
        return exc
    log.exception("Unhandled error handling %s %s", request.method, request.path)
    report_error_to_github(
        "blixten85/product-describer",
        f"Oväntat fel: {request.method} {request.path}",
        exc,
        context={"method": request.method, "path": request.path},
    )
    return jsonify({"error": "Internt serverfel. Se serverloggen för detaljer."}), 500


def _safe_csv(value: str) -> str:
    """Prevent CSV formula injection by prefixing dangerous leading characters."""
    return "'" + value if _FORMULA_PREFIX.match(value) else value

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


def _get_jobs(account_id: str) -> list[dict]:
    with _lock:
        return sorted(
            (j for j in _jobs.values() if j.get("account_id") == account_id),
            key=lambda j: j["created_at"],
            reverse=True,
        )


_load_from_disk()


def _rows_path(job_id: str) -> Path:
    return OUTPUT_DIR / f"{job_id}_rows.json"


def _partial_path(job_id: str) -> Path:
    return OUTPUT_DIR / f"{job_id}_partial.json"


def _save_rows(job_id: str, rows: list[dict], fieldnames: list[str]) -> None:
    with open(_rows_path(job_id), "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "fieldnames": fieldnames}, f)


def _load_rows(job_id: str) -> tuple[list[dict], list[str]] | None:
    path = _rows_path(job_id)
    if not path.is_file():
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data["rows"], data["fieldnames"]


def _save_partial(job_id: str, results: dict[int, dict]) -> None:
    with open(_partial_path(job_id), "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in results.items()}, f)


def _load_partial(job_id: str) -> dict[int, dict]:
    path = _partial_path(job_id)
    if not path.is_file():
        return {}
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return {int(k): v for k, v in raw.items()}


def _pause_job(job: dict, resume_at: datetime) -> None:
    job["status"] = "paused"
    job["resume_at"] = resume_at.isoformat()


def _process(job_id: str) -> None:
    import concurrent.futures

    with _lock:
        job = _jobs[job_id]
        job["status"] = "processing"
        job["resume_at"] = None
        workers = job["workers"]
        options = job.get("options") or {}
        custom_direction = job.get("custom_direction", "")
        account_id = job["account_id"]
    _save()

    chain = provider_config.build_chain(account_id)
    if chain is None:
        with _lock:
            job["status"] = "error"
            job["error"] = "Ingen AI-leverantör konfigurerad."
        _save()
        return

    try:
        cached = _load_rows(job_id)
        if cached is None:
            rows, fieldnames = extract_rows(job["input_path"], chain)
            _save_rows(job_id, rows, fieldnames)
        else:
            rows, fieldnames = cached
        with _lock:
            job["total"] = len(rows)
    except AllProvidersExhausted as e:
        with _lock:
            _pause_job(job, e.resume_at)
        _save()
        return
    except Exception as e:
        with _lock:
            job["status"] = "error"
            job["error"] = str(e)
        _save()
        return

    results = _load_partial(job_id)
    pending = [(i, r) for i, r in enumerate(rows) if i not in results]

    if not pending:
        _finish_job(job_id, job, rows, fieldnames, results)
        return

    exhausted_at: datetime | None = None
    save_counter = 0

    def do(idx_row):
        idx, row = idx_row
        try:
            parts = generate_description(
                chain,
                row.get("Site", ""),
                row.get("Product", ""),
                row.get("Price (SEK)", ""),
                options=options,
                custom_direction=custom_direction,
            )
            if not parts.get("beskrivning"):
                log.warning("job=%s row=%s empty beskrivning for %r", job_id, idx, row.get("Product", "")[:60])
            return idx, parts, None
        except AllProvidersExhausted as e:
            return idx, None, e
        except Exception as e:
            log.exception("job=%s row=%s failed for %r", job_id, idx, row.get("Product", "")[:60])
            return idx, {"beskrivning": "", "varför": ""}, f"{type(e).__name__}: {e}"

    log.info("job=%s starting %d/%d rows with workers=%d", job_id, len(pending), len(rows), workers)
    errors: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(do, idx_row): idx_row[0] for idx_row in pending}
        for fut in concurrent.futures.as_completed(futures):
            idx, parts, err = fut.result()
            if isinstance(err, AllProvidersExhausted):
                exhausted_at = exhausted_at or err.resume_at
                continue
            results[idx] = parts
            if err and len(errors) < 10:
                errors.append(err)
            save_counter += 1
            with _lock:
                job["succeeded"] = sum(1 for v in results.values() if v.get("beskrivning"))
                job["provider"] = chain.current_provider_name()
                if errors:
                    job["last_errors"] = errors
            if save_counter % 5 == 0:
                _save_partial(job_id, results)
                _save()

    _save_partial(job_id, results)

    if exhausted_at is not None and len(results) < len(rows):
        with _lock:
            _pause_job(job, exhausted_at)
        _save()
        log.info("job=%s paused — providers exhausted, resuming at %s", job_id, exhausted_at.isoformat())
        return

    _finish_job(job_id, job, rows, fieldnames, results)


def _finish_job(job_id: str, job: dict, rows: list[dict], fieldnames: list[str], results: dict[int, dict]) -> None:
    import csv

    output_path = OUTPUT_DIR / f"{job_id}_med_beskrivning.csv"
    out_fields = fieldnames + ["Beskrivning", "Varför"]
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields)
        writer.writeheader()
        for i, row in enumerate(rows):
            parts = results.get(i, {"beskrivning": "", "varför": ""})
            row = dict(row)
            row["Beskrivning"] = _safe_csv(parts.get("beskrivning", ""))
            row["Varför"] = _safe_csv(parts.get("varför", ""))
            writer.writerow(row)

    with _lock:
        job["output_file"] = str(output_path)
        job["status"] = "done"
        job["succeeded"] = sum(1 for v in results.values() if v.get("beskrivning"))
    _save()
    log.info("job=%s done — %d/%d succeeded", job_id, job["succeeded"], len(rows))
    _partial_path(job_id).unlink(missing_ok=True)


def _resume_watcher() -> None:
    while True:
        time.sleep(RESUME_CHECK_INTERVAL)
        now = datetime.now(timezone.utc)
        with _lock:
            due = [
                j["id"] for j in _jobs.values()
                if j["status"] == "paused" and j.get("resume_at")
                and datetime.fromisoformat(j["resume_at"]) <= now
            ]
        for job_id in due:
            log.info("job=%s resuming automatically", job_id)
            threading.Thread(target=_process, args=(job_id,), daemon=True).start()


threading.Thread(target=_resume_watcher, daemon=True, name="resume-watcher").start()


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "GET":
        return render_template("signup.html", error=None)
    email = request.form.get("email", "")
    password = request.form.get("password", "")
    account_id, error = auth.create_account(email, password)
    if error:
        return render_template("signup.html", error=error), 400
    session["account_id"] = account_id
    return redirect(url_for("index"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html", error=None)
    email = request.form.get("email", "")
    password = request.form.get("password", "")
    account_id = auth.verify_login(email, password)
    if not account_id:
        return render_template("login.html", error="Fel e-postadress eller lösenord"), 401
    session["account_id"] = account_id
    return redirect(url_for("index"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    return render_template(
        "index.html",
        providers=provider_config.PROVIDER_CLASSES.keys(),
        email=auth.get_email(session["account_id"]),
    )


@app.route("/api/status")
@login_required
def api_status():
    configured = provider_config.configured_providers(session["account_id"])
    return jsonify({"configured": configured, "ready": bool(configured)})


@app.route("/api/settings", methods=["GET"])
@login_required
def get_settings():
    account_id = session["account_id"]
    order = provider_config.get_order(account_id)
    configured = set(provider_config.configured_providers(account_id))
    extra_values = {
        name: {
            field["name"]: provider_config.get_provider_config(account_id, name).get(field["name"], "")
            for field in fields
        }
        for name, fields in provider_config.EXTRA_FIELDS.items()
    }
    return jsonify({
        "configured": sorted(configured),
        "order": order,
        "available_models": {
            name: cls(api_key="").available_models()
            for name, cls in provider_config.PROVIDER_CLASSES.items()
        },
        "labels": PROVIDER_LABELS,
        "extra_fields": provider_config.EXTRA_FIELDS,
        "extra_values": extra_values,
    })


@app.route("/api/settings/key", methods=["POST"])
@login_required
def set_settings_key():
    data = request.get_json(silent=True) or {}
    provider = data.get("provider")
    api_key = data.get("api_key", "")
    if provider not in provider_config.PROVIDER_CLASSES:
        return jsonify({"error": "Okänd leverantör"}), 400
    if not api_key.strip():
        return jsonify({"error": "Nyckel saknas"}), 400

    extra = {}
    for field in provider_config.EXTRA_FIELDS.get(provider, []):
        value = (data.get(field["name"]) or "").strip()
        if not value:
            return jsonify({"error": f'Fältet "{field["label"]}" krävs'}), 400
        extra[field["name"]] = value

    try:
        provider_config.set_provider_config(session["account_id"], provider, {"api_key": api_key, **extra})
    except RuntimeError:
        log.exception("Failed to save provider configuration for provider '%s'", provider)
        return jsonify({"error": "Ett internt fel uppstod"}), 500
    return jsonify({"ok": True})


@app.route("/api/settings/key/<provider>", methods=["DELETE"])
@login_required
def delete_settings_key(provider):
    if provider not in provider_config.PROVIDER_CLASSES:
        return jsonify({"error": "Okänd leverantör"}), 400
    provider_config.remove_provider_config(session["account_id"], provider)
    return jsonify({"ok": True})


@app.route("/api/settings/order", methods=["POST"])
@login_required
def set_settings_order():
    data = request.get_json(silent=True) or {}
    order = data.get("order", [])
    for entry in order:
        if entry.get("provider") not in provider_config.PROVIDER_CLASSES:
            return jsonify({"error": f"Okänd leverantör: {entry.get('provider')}"}), 400
    provider_config.set_order(session["account_id"], order)
    return jsonify({"ok": True})


@app.route("/api/upload", methods=["POST"])
@login_required
def upload():
    if "file" not in request.files:
        return jsonify({"error": "Ingen fil bifogad"}), 400
    f = request.files["file"]
    suffix = Path(f.filename or "").suffix.lower()
    if not f.filename or suffix not in SUPPORTED_EXTENSIONS:
        return jsonify({"error": f"Filtyp måste vara en av: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"}), 400
    allowed_suffixes = {ext.lower(): ext.lower() for ext in SUPPORTED_EXTENSIONS}
    safe_suffix = allowed_suffixes[suffix]

    account_id = session["account_id"]
    if not provider_config.configured_providers(account_id):
        return jsonify({"error": "Ingen AI-leverantör är konfigurerad. Lägg till en API-nyckel i inställningarna."}), 400

    workers = max(1, min(8, int(request.form.get("workers", 2))))
    options = {
        "tone": request.form.get("tone", ""),
        "length": request.form.get("length", ""),
        "audience": request.form.get("audience", ""),
    }
    custom_direction = request.form.get("custom_direction", "")

    job_id = str(uuid.uuid4())[:8]
    original_name = Path(f.filename).name
    input_path = UPLOAD_DIR / f"{job_id}{safe_suffix}"
    f.save(input_path)

    job = {
        "id": job_id,
        "account_id": account_id,
        "filename": original_name,
        "input_path": str(input_path),
        "options": options,
        "custom_direction": custom_direction,
        "workers": workers,
        "total": 0,
        "status": "queued",
        "succeeded": 0,
        "provider": None,
        "resume_at": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "output_file": None,
    }
    with _lock:
        _jobs[job_id] = job
    _save()

    threading.Thread(target=_process, args=(job_id,), daemon=True).start()

    return jsonify({"job_id": job_id})


@app.route("/api/jobs")
@login_required
def list_jobs():
    return jsonify(_get_jobs(session["account_id"]))


@app.route("/api/jobs/<job_id>")
@login_required
def get_job(job_id: str):
    with _lock:
        job = _jobs.get(job_id)
    if not job or job.get("account_id") != session["account_id"]:
        return jsonify({"error": "Hittar inte jobbet"}), 404
    return jsonify(job)


@app.route("/api/jobs/<job_id>/download")
@login_required
def download_job(job_id: str):
    with _lock:
        job = _jobs.get(job_id)
    if not job or job.get("account_id") != session["account_id"] or not job.get("output_file"):
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

    interval = int(os.getenv("SYNC_INTERVAL", "300"))
    limit = int(os.getenv("SYNC_LIMIT", "50"))
    workers = int(os.getenv("SYNC_WORKERS", "2"))
    scraper_url = os.getenv("SCRAPER_URL", SCRAPER_URL)
    log.info("sync worker starting: scraper=%s interval=%ds workers=%d", scraper_url, interval, workers)

    while True:
        chain = provider_config.build_chain_from_env()
        if chain is None:
            log.warning("sync: ingen AI-leverantör konfigurerad, väntar")
            time.sleep(interval)
            continue

        try:
            products = fetch_products_missing_description(scraper_url, limit)
        except Exception as e:
            log.error("sync: kunde inte hämta från scrapern: %s", e)
            products = []

        if products:
            log.info("sync: hämtade %d produkter utan beskrivning", len(products))
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(_process_one, chain, p) for p in products]
                for fut in concurrent.futures.as_completed(futures):
                    pid, parts, err = fut.result()
                    if isinstance(err, AllProvidersExhausted):
                        log.warning("sync: leverantörer uttömda, försöker igen efter %s", err.resume_at.isoformat())
                        continue
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
    app.run(host="0.0.0.0", port=5050, threaded=True)
