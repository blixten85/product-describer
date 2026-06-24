"""Rapporterar oväntade fel automatiskt som en GitHub-issue med @claude i
texten, så att den befintliga claude.yml-automationen (issues: opened +
"@claude" i body/titel) tar hand om felet utan manuellt ingripande.

Saneringsregler innan något skickas till en publik issue:
- Värdet av varje miljövariabel vars namn innehåller KEY/TOKEN/SECRET/
  PASSWORD/PASS maskeras överallt det förekommer i texten.
- Vanliga nyckelmönster (sk-..., ghp_..., Bearer ..., AKIA...) maskeras
  som extra skyddslager utöver env-baserad sanering.
- E-postadresser maskeras.
- Hemkataloger med användarnamn (/home/<namn>/...) generaliseras bort.
- Filinnehåll skickas ALDRIG med — bara filnamn/typ/storlek om sådant
  behövs i kontexten (anroparens ansvar att inte lägga in rådata).

Avdubblering: söker efter en redan öppen issue med samma kort-fingeravtryck
i titeln innan en ny skapas, så en upprepad krasch inte spammar repot.
"""

import hashlib
import os
import re
import traceback

import requests

_SECRET_ENV_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASS")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
_HOME_PATH_RE = re.compile(r"/home/[^/\s]+")
_KEY_PATTERN_RE = re.compile(
    r"(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|"
    r"AKIA[A-Z0-9]{12,}|Bearer\s+[A-Za-z0-9._-]{10,})"
)


def _redact(text: str) -> str:
    for key, value in os.environ.items():
        if value and len(value) >= 8 and any(m in key.upper() for m in _SECRET_ENV_MARKERS):
            text = text.replace(value, "[REDACTED]")
    text = _KEY_PATTERN_RE.sub("[REDACTED]", text)
    text = _EMAIL_RE.sub("[EMAIL REDACTED]", text)
    text = _HOME_PATH_RE.sub("/home/[user]", text)
    return text


def _fingerprint(exc: BaseException) -> str:
    """Kort, stabil identifierare för feltypen + var den kastades — används
    för att hitta/undvika dubbletter, inte som hemlighet."""
    tb = traceback.extract_tb(exc.__traceback__)
    location = f"{tb[-1].filename}:{tb[-1].lineno}" if tb else "?"
    raw = f"{type(exc).__name__}@{location}"
    return hashlib.sha256(raw.encode()).hexdigest()[:10]


def report_error_to_github(repo: str, title: str, exc: BaseException, context: dict | None = None) -> str | None:
    """Skapar (eller hoppar över om en dubblett redan finns) en GitHub-issue
    för ett oväntat fel. Returnerar issue-URL:en, eller None om rapportering
    inte gick (saknad token, redan rapporterad, nätverksfel — allt 'best
    effort', ska aldrig krascha anroparen)."""
    token = os.environ.get("GITHUB_ERROR_REPORT_TOKEN")
    if not token:
        return None

    fp = _fingerprint(exc)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }

    try:
        search = requests.get(
            "https://api.github.com/search/issues",
            params={"q": f"repo:{repo} is:issue is:open in:title [{fp}]"},
            headers=headers,
            timeout=10,
        )
        if search.status_code == 200 and search.json().get("total_count", 0) > 0:
            return search.json()["items"][0]["html_url"]
    except requests.RequestException:
        pass  # avdubblering är "best effort" — fortsätt hellre rapportera än att tystna helt

    tb_text = _redact("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    context_text = ""
    if context:
        safe_context = "\n".join(f"{k}: {_redact(str(v))}" for k, v in context.items())
        context_text = f"\n\n**Kontext:**\n```\n{safe_context}\n```"

    body = (
        f"@claude Ett oväntat fel inträffade i drift.\n\n"
        f"```\n{tb_text}\n```"
        f"{context_text}\n\n"
        "_Automatiskt rapporterad av applikationen. Känslig information "
        "(API-nycklar, e-postadresser, sökvägar med användarnamn, "
        "filinnehåll) är borttagen innan denna issue skapades._"
    )

    try:
        resp = requests.post(
            f"https://api.github.com/repos/{repo}/issues",
            headers=headers,
            json={
                "title": f"[auto] {title} [{fp}]"[:250],
                "body": body,
                "labels": ["bug", "auto-reported"],
            },
            timeout=15,
        )
        if resp.status_code == 201:
            return resp.json()["html_url"]
    except requests.RequestException:
        pass
    return None
