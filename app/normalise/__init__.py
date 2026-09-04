"""Normalisation layer: turns raw ATS strings into the shared schema fields.

Public surface:
    normalise_record(raw)  -> dict ready for a Listing
    make_id(slug, source_job_id) -> stable 64-char hash
"""
from app.normalise.core import make_id, normalise_record

__all__ = ["make_id", "normalise_record"]
