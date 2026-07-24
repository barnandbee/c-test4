"""Pure reconciliation logic — kept separate from SQL so it can be unit-tested.

Encodes the reliability rule from the brief: a run that yields far fewer records
than the last good run is *suspect*, and a suspect run must never close existing
listings on the basis of one bad crawl.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ReconciliationPlan:
    suspect: bool
    reason: str
    close_ids: list[str]         # currently-open listing ids to mark closed
    upsert: bool                 # whether to upsert the new records at all


def is_suspect(prev_count: int, new_count: int, min_ratio: float) -> bool:
    """True if this run looks like a broken crawl rather than a real drop.

    - No previous baseline -> never suspect (first run).
    - Zero results when we previously had some -> suspect.
    - Fewer than min_ratio of the previous count -> suspect.
    """
    if prev_count <= 0:
        return False
    if new_count == 0:
        return True
    return new_count < prev_count * min_ratio


def plan_reconciliation(
    existing_open_ids: set[str],
    seen_ids: set[str],
    prev_count: int,
    new_count: int,
    min_ratio: float,
) -> ReconciliationPlan:
    """Decide what to upsert and what to close for one university's run."""
    suspect = is_suspect(prev_count, new_count, min_ratio)
    if suspect:
        return ReconciliationPlan(
            suspect=True,
            reason=(f"low yield: {new_count} vs prev {prev_count} "
                    f"(< {min_ratio:.0%}); keeping existing listings, not closing any"),
            close_ids=[],
            # Still upsert the records we *did* get — they're real and refresh
            # last_seen — but don't close anything missing.
            upsert=new_count > 0,
        )
    # Healthy run: upsert everything seen, close open listings no longer present.
    close_ids = sorted(existing_open_ids - seen_ids)
    return ReconciliationPlan(
        suspect=False,
        reason=f"ok: {new_count} records, closing {len(close_ids)} stale",
        close_ids=close_ids,
        upsert=True,
    )
