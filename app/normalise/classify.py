"""Role-family, discipline, work-type, time-fraction and salary parsing.

All keyword tables live in config/*.yaml so they can be reviewed and tuned.
"""
from __future__ import annotations

import functools
import re

from app.config import load_disciplines, load_role_families


@functools.lru_cache
def _roles() -> dict:
    return load_role_families()


@functools.lru_cache
def _disc() -> dict:
    return load_disciplines()


def classify_role_family(title: str | None, classification_raw: str | None = None,
                         excerpt: str | None = None) -> str:
    text = " ".join(filter(None, [title, classification_raw, excerpt])).lower()
    for row in _roles()["families"]:
        for kw in row["keywords"]:
            if kw in text:
                return row["family"]
    return _roles()["default"]


def classify_discipline(title: str | None, excerpt: str | None = None,
                        role_family: str | None = None) -> str:
    text = " ".join(filter(None, [title, excerpt])).lower()
    cfg = _disc()

    # Academic disciplines first (strongest signal for academic/research roles).
    for row in cfg["disciplines"]:
        for kw in row["keywords"]:
            if kw in text:
                return row["field"]

    # Professional function fields for clearly non-academic roles.
    if role_family in {"professional", "technical", "executive", None}:
        for row in cfg["professional_functions"]:
            for kw in row["keywords"]:
                if kw in text:
                    return row["field"]

    return cfg["default"]


# --- Work type / time fraction -----------------------------------------------

_WORK_TYPE_PATTERNS = [
    ("continuing", [r"\bcontinuing\b", r"\bongoing\b", r"\bpermanent\b"]),
    ("fixed-term", [r"\bfixed[\s-]?term\b", r"\bfixed\b", r"\bterm\b", r"\btemporary\b"]),
    ("casual", [r"\bcasual\b", r"\bsessional\b", r"\bhourly\b"]),
    ("contract", [r"\bcontract\b"]),
]

_TIME_FRACTION_PATTERNS = [
    ("part-time", [r"\bpart[\s-]?time\b", r"\bp/t\b", r"\bfractional\b", r"\b0\.\d\s*fte\b"]),
    ("full-time", [r"\bfull[\s-]?time\b", r"\bf/t\b", r"\b1\.0\s*fte\b"]),
]


def parse_work_type(*fields: str | None) -> str | None:
    text = " ".join(filter(None, fields)).lower()
    for value, pats in _WORK_TYPE_PATTERNS:
        if any(re.search(p, text) for p in pats):
            return value
    return None


def parse_time_fraction(*fields: str | None) -> str | None:
    text = " ".join(filter(None, fields)).lower()
    for value, pats in _TIME_FRACTION_PATTERNS:
        if any(re.search(p, text) for p in pats):
            return value
    return None


def parse_remote(*fields: str | None) -> bool:
    text = " ".join(filter(None, fields)).lower()
    return bool(re.search(r"\bremote\b|\bwork from home\b|\bwfh\b|\bhybrid\b", text))


# --- Salary parsing ----------------------------------------------------------
# Australian HE salaries are usually published as ranges like
# "$110,596 - $131,032" or "HEW 7 ($98,000)". Be conservative: only emit numbers
# we can actually parse, and keep the raw string always.

_MONEY = r"\$?\s*([0-9]{2,3}(?:,[0-9]{3})+|[0-9]{5,6})(?:\.[0-9]{2})?"


def parse_salary(*fields: str | None) -> dict:
    """Return {'salary_min', 'salary_max', 'salary_raw'}.

    salary_raw is the first field that actually contained a dollar figure, so the
    UI can be honest about provenance. Values with no parseable figure return
    all-None (and the UI reports "salary not published").
    """
    for field in fields:
        if not field:
            continue
        matches = re.findall(_MONEY, field)
        if not matches:
            continue
        nums = sorted(float(m.replace(",", "")) for m in matches)
        # Ignore implausible values (e.g. a stray year like 2026 won't match _MONEY,
        # but guard against tiny/huge anyway).
        nums = [n for n in nums if 20000 <= n <= 600000]
        if not nums:
            continue
        return {
            "salary_min": nums[0],
            "salary_max": nums[-1] if len(nums) > 1 else nums[0],
            "salary_raw": field.strip(),
        }
    return {"salary_min": None, "salary_max": None, "salary_raw": None}
