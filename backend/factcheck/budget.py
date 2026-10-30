"""Spending guard. Every paid call goes through here first, so a bug, a stuck
retry or a runaway shortcut can never run up a bill. Same idea as Content Files.

Limits (Railway env vars): FACTCHECK_DAILY_LIMIT, FACTCHECK_MONTHLY_BUDGET_USD,
FACTCHECK_MONTHLY_LOOKUP_LIMIT. BUDGET_OFF=1 disables the guard (local tests only).
"""
import os
import re
from datetime import datetime, timezone

from . import config, db


class BudgetError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.public = message


def _month():
    return "month-" + datetime.now(timezone.utc).strftime("%Y-%m")


def _day():
    return "day-" + datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _off():
    return os.environ.get("BUDGET_OFF") == "1"


# Dollars per million tokens. Unknown models are priced as the most expensive, so the guard errs safe.
PRICES = [(r"haiku", 1, 5), (r"sonnet", 3, 15), (r"opus", 5, 25), (r"fable|mythos", 10, 50)]


def claude_cost(model, usage):
    inp_p, out_p = next(((i, o) for pat, i, o in PRICES if re.search(pat, model or "", re.I)), (10, 50))
    u = usage or {}
    get = (lambda k: getattr(u, k, None) if not isinstance(u, dict) else u.get(k))
    inp = (get("input_tokens") or 0) + (get("cache_creation_input_tokens") or 0) * 1.25 + (get("cache_read_input_tokens") or 0) * 0.1
    return (inp * inp_p + (get("output_tokens") or 0) * out_p) / 1e6


def groq_cost(seconds):
    return max(10, seconds or 60) / 3600 * 0.04


def ensure_ai():
    if _off():
        return
    m = db.usage_get(_month())
    if m["usd"] >= config.MONTHLY_BUDGET_USD:
        raise BudgetError(f"This month's fact-check budget (${config.MONTHLY_BUDGET_USD:.2f}) is used up. "
                          "Raise FACTCHECK_MONTHLY_BUDGET_USD in Railway to keep going.")


def ensure_lookup():
    if _off():
        return
    m = db.usage_get(_month())
    if m["lookups"] >= config.MONTHLY_LOOKUP_LIMIT:
        raise BudgetError(f"This month's post-lookup limit ({config.MONTHLY_LOOKUP_LIMIT}) is used up. "
                          "Raise FACTCHECK_MONTHLY_LOOKUP_LIMIT in Railway.")


def take_check():
    """Counts one fact-check toward today's limit, refusing when it's reached."""
    if _off():
        return
    d = db.usage_get(_day())
    if d["checks"] >= config.DAILY_CHECK_LIMIT:
        raise BudgetError(f"The daily limit of {config.DAILY_CHECK_LIMIT} fact-checks is reached. It resets at midnight UTC.")
    db.usage_add(_day(), checks=1)


def record(usd=0.0, lookups=0):
    try:
        if usd or lookups:
            db.usage_add(_month(), usd=usd, lookups=lookups)
    except Exception as e:  # never let bookkeeping break a check
        print("[budget] record failed:", e)


def summary():
    m, d = db.usage_get(_month()), db.usage_get(_day())
    return {
        "month": {"usd": round(m["usd"], 3), "lookups": m["lookups"]},
        "today": {"checks": d["checks"]},
        "limits": {"usd": config.MONTHLY_BUDGET_USD, "lookups": config.MONTHLY_LOOKUP_LIMIT, "checks_per_day": config.DAILY_CHECK_LIMIT},
    }
