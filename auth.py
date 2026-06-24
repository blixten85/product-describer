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
import uuid
from contextlib import contextmanager
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "config"))
DB_PATH = CONFIG_DIR / "accounts.db"


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

    is_first_account = account_count() == 0
    account_id = str(uuid.uuid4())[:12]
    password_hash = generate_password_hash(password)

    with _db() as conn:
        try:
            conn.execute(
                "INSERT INTO accounts (id, email, password_hash, created_at) VALUES (?, ?, ?, datetime('now'))",
                (account_id, email, password_hash),
            )
        except sqlite3.IntegrityError:
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
        new_credentials = provider_config.credentials_dir(account_id)
        new_credentials.mkdir(parents=True, exist_ok=True)
        for item in legacy_credentials.iterdir():
            shutil.move(str(item), str(new_credentials / item.name))
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
