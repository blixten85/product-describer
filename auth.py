"""Kontohantering — email+lösenord, Flask-sessioner.

Varje konto har sina egna AI-leverantörsnycklar och jobb (se
provider_config.py:s account_id-parameter och app.py:s jobbfiltrering) —
operatören blir aldrig betalningsansvarig för andra användares
API-användning, samma princip som politiker-webapp.

Lagring: SQLite (CONFIG_DIR/accounts.db) — ingen extern databas behövs för
den här skalan. Lösenord hashas med werkzeug.security (redan ett Flask-
beroende, scrypt under huven), aldrig i klartext.

Engångsmigrering: det första kontot som skapas (databasen är tom) ärver
all redan existerande global konfiguration (API-nycklar, failover-ordning,
jobb utan account_id) från innan kontosystemet fanns, så operatörens
befintliga uppsättning inte går förlorad.
"""

import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "config"))
DB_PATH = CONFIG_DIR / "accounts.db"

# Brute-force-spärr på login. In-memory räcker: Gunicorn kör en enda
# process (--workers 1 --threads 8), samma antagande som resume-watchern.
LOGIN_MAX_ATTEMPTS = int(os.getenv("LOGIN_MAX_ATTEMPTS", "5"))
LOGIN_WINDOW_SECONDS = int(os.getenv("LOGIN_WINDOW_SECONDS", "900"))
_login_failures: dict[str, list[float]] = {}
_login_lock = threading.Lock()


def _recent_failures(key: str, now: float) -> list[float]:
    return [t for t in _login_failures.get(key, []) if now - t < LOGIN_WINDOW_SECONDS]


def login_blocked(key: str) -> bool:
    now = time.time()
    with _login_lock:
        attempts = _recent_failures(key, now)
        _login_failures[key] = attempts
        return len(attempts) >= LOGIN_MAX_ATTEMPTS


def record_login_failure(key: str) -> None:
    now = time.time()
    with _login_lock:
        attempts = _recent_failures(key, now)
        attempts.append(now)
        _login_failures[key] = attempts


def reset_login_failures(key: str) -> None:
    with _login_lock:
        _login_failures.pop(key, None)


@contextmanager
def _db():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS accounts ("
        "id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, "
        "password_hash TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def account_count() -> int:
    with _db() as conn:
        return conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]


def create_account(email: str, password: str) -> tuple[str | None, str | None]:
    """Returnerar (account_id, None) vid lyckad skapelse, (None, felmeddelande) annars."""
    email = email.strip().lower()
    if not email or "@" not in email:
        return None, "Ogiltig e-postadress"
    if len(password) < 8:
        return None, "Lösenordet måste vara minst 8 tecken"

    account_id = str(uuid.uuid4())[:12]
    password_hash = generate_password_hash(password)

    # BEGIN IMMEDIATE tar ett skrivlås innan COUNT-koll + INSERT, så att två
    # samtidiga första-registreringar inte båda tror sig vara först och
    # kör migreringen dubbelt (eller mot ett konto som inte "vann").
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        is_first_account = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
        try:
            conn.execute(
                "INSERT INTO accounts (id, email, password_hash, created_at) VALUES (?, ?, ?, datetime('now'))",
                (account_id, email, password_hash),
            )
        except sqlite3.IntegrityError:
            conn.rollback()
            return None, "E-postadressen är redan registrerad"

    if is_first_account:
        _migrate_legacy_data(account_id)

    return account_id, None


def verify_login(email: str, password: str) -> str | None:
    """Returnerar account_id vid korrekt inloggning, annars None."""
    email = email.strip().lower()
    with _db() as conn:
        row = conn.execute("SELECT id, password_hash FROM accounts WHERE email = ?", (email,)).fetchone()
    if not row or not check_password_hash(row[1], password):
        return None
    return row[0]


def get_email(account_id: str) -> str | None:
    with _db() as conn:
        row = conn.execute("SELECT email FROM accounts WHERE id = ?", (account_id,)).fetchone()
    return row[0] if row else None


def _migrate_legacy_data(account_id: str) -> None:
    """Flyttar förkontosystem-data (en enda delad uppsättning) till det
    första kontot som skapas, så operatörens befintliga nycklar/jobb inte
    försvinner när kontosystemet aktiveras."""
    import json
    import shutil

    import provider_config

    legacy_credentials = CONFIG_DIR / "credentials"
    legacy_order = CONFIG_DIR / "provider_order.json"

    if legacy_credentials.is_dir():
        # Läs+skriv via provider_config:s vanliga väg istället för att bara
        # flytta filerna rakt av, så en legacy PLAINTEXT-nyckel blir krypterad
        # i samma veva, inte fortsatt klartext på den nya platsen.
        for item in legacy_credentials.iterdir():
            provider_name = next(
                (name for name, filename in provider_config._KEY_FILENAMES.items() if filename == item.name),
                None,
            )
            if provider_name is None:
                continue
            config = provider_config._parse_config_blob(provider_config._decrypt_stored_value(item.read_text()))
            if config.get("api_key"):
                provider_config.set_provider_config(account_id, provider_name, config)
            item.unlink()
        legacy_credentials.rmdir()

    if legacy_order.is_file():
        new_order = provider_config.order_file(account_id)
        new_order.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy_order), str(new_order))

    jobs_file = Path(os.getenv("OUTPUT_DIR", "outputs")) / "jobs.json"
    if jobs_file.is_file():
        with open(jobs_file) as f:
            jobs = json.load(f)
        changed = False
        for job in jobs:
            if "account_id" not in job:
                job["account_id"] = account_id
                changed = True
        if changed:
            with open(jobs_file, "w") as f:
                json.dump(jobs, f, indent=2)
