"""Skills extraction driven by config/skills.yaml.

Deterministic and reviewable (no ML): scan text for taxonomy terms and return the
canonical skill names present. Only tags are stored downstream — never the source
text. Works on whatever text it's given (title + excerpt today; richer requirements
text later if a detail-fetch pass is added).
"""
from __future__ import annotations

import functools
import re

from app.config import load_skills


@functools.lru_cache
def _registry() -> list[tuple[str, str, list[re.Pattern]]]:
    """[(canonical_skill, category, [compiled term patterns]), ...] in taxonomy order."""
    reg: list[tuple[str, str, list[re.Pattern]]] = []
    for category, skills in load_skills().items():
        for canonical, aliases in skills.items():
            terms = [canonical, *(aliases or [])]
            reg.append((canonical, category, [_compile(t) for t in terms]))
    return reg


@functools.lru_cache
def _category_map() -> dict[str, str]:
    return {canonical: cat for canonical, cat, _ in _registry()}


def _compile(term: str) -> re.Pattern:
    # Terms containing a backslash are treated as raw regex (e.g. "\\bai\\b");
    # everything else is matched as a whole word/phrase, case-insensitively.
    if "\\" in term:
        return re.compile(term, re.I)
    return re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", re.I)


def extract_skills(*texts: str | None) -> list[str]:
    """Return the canonical skills mentioned across the given text(s), in taxonomy
    order, de-duplicated."""
    blob = " ".join(t for t in texts if t)
    if not blob:
        return []
    found: list[str] = []
    for canonical, _cat, patterns in _registry():
        if any(p.search(blob) for p in patterns):
            found.append(canonical)
    return found


def skill_category(skill: str) -> str:
    return _category_map().get(skill, "Other")
