"""Pipeline-level guards. These catch integration bugs that unit-testing a single
adapter or the normaliser in isolation would miss — e.g. the staging insert."""
from app.ingest.runner import _UPSERT_COLUMNS
from app.models import StagingListing
from app.normalise.core import RawJob, normalise_record

UNI = {"slug": "uwa", "name": "University of Western Australia", "state": "WA"}


def test_staging_row_construction_has_no_duplicate_kwargs():
    """Regression: StagingListing(run_id=..., **rec) must not double-pass
    university_slug (it's already in _UPSERT_COLUMNS). This reproduces the crash
    'got multiple values for keyword argument university_slug'."""
    rec = normalise_record(
        RawJob(source_job_id="123", title="Lecturer in Physics", url="https://x/job/123"),
        UNI,
    )
    obj = StagingListing(run_id=1, **{k: rec.get(k) for k in (*_UPSERT_COLUMNS, "id")})
    assert obj.run_id == 1
    assert obj.university_slug == "uwa"
    assert obj.id == rec["id"]
    assert obj.title == "Lecturer in Physics"


def test_upsert_columns_cover_every_staging_field_rec_provides():
    """The staging insert relies on rec carrying every column it references."""
    rec = normalise_record(
        RawJob(source_job_id="1", title="Officer", url="https://x/job/1"), UNI
    )
    for col in (*_UPSERT_COLUMNS, "id"):
        assert col in rec, f"normalise_record is missing '{col}' expected by staging insert"
