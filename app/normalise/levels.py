"""Level-band normalisation driven by config/level_bands.yaml.

Returns (level_band, level_scale, canonical_label) for a raw classification
string and/or title. Table-driven, not regex guesswork — the mapping lives in
YAML so it can be reviewed and edited without touching code.
"""
from __future__ import annotations

import functools
import re

from app.config import load_level_bands


@functools.lru_cache
def _cfg() -> dict:
    return load_level_bands()


def _academic_lookup() -> dict:
    return {row["level"]: row for row in _cfg()["academic"]}


def classify_level(classification_raw: str | None, title: str | None = None) -> dict:
    """Return {'level_band', 'level_scale', 'level_label'} — any may be None.

    Order of attempts: explicit academic token, explicit professional (HEW) token,
    senior-executive markers, then keyword fallback on the title.
    """
    cfg = _cfg()
    text = " ".join(filter(None, [classification_raw, title])) or ""
    low = text.lower()

    # 1) Academic Level A–E
    for row in cfg["academic"]:
        for alias in row["aliases"]:
            if re.search(alias, low):
                return {
                    "level_band": row["band"],
                    "level_scale": "academic",
                    "level_label": row["label"],
                }

    # 2) Professional HEW/HEO/HEP <n>
    prof = cfg["professional"]
    prefix_pat = "|".join(re.escape(p) for p in prof["prefix_aliases"])
    m = re.search(rf"(?:{prefix_pat})\s*[-:]?\s*(\d{{1,2}})", low)
    if m:
        n = int(m.group(1))
        for band_row in prof["bands"]:
            if n in band_row["levels"]:
                return {
                    "level_band": band_row["band"],
                    "level_scale": "professional",
                    "level_label": band_row["label_fmt"].format(n=n),
                }

    # 3) Senior-executive markers
    for marker in cfg["senior_executive_markers"]:
        if marker in low:
            return {
                "level_band": "leadership",
                "level_scale": "professional",
                "level_label": "Senior executive",
            }

    # 4) Keyword fallback on the title. Most-specific (longest) keyword wins, so
    #    "associate lecturer" beats "lecturer" and "senior lecturer" beats "lecturer".
    best_kw = ""
    best_band = None
    for row in cfg["keyword_fallback"]:
        for kw in row["keywords"]:
            if kw in low and len(kw) > len(best_kw):
                best_kw, best_band = kw, row["band"]
    if best_band:
        return {"level_band": best_band, "level_scale": None, "level_label": None}

    return {"level_band": None, "level_scale": None, "level_label": None}
