"""Skydd mot CSV-formelinjektion — delas mellan webb-vägen (app.py) och
CLI-vägen (main.py) så båda saneringar är identiska."""

import re

_FORMULA_PREFIX = re.compile(r"^[=+\-@\t\r]")


def safe_csv(value: str) -> str:
    """Prefixar farliga inledande tecken så ett kalkylprogram inte tolkar
    cellen som en formel när CSV:en öppnas."""
    return "'" + value if _FORMULA_PREFIX.match(value) else value
