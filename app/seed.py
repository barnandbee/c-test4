"""Seed data so the UI is populated before the first crawl completes.

These are illustrative listings (not scraped) covering a spread of states,
universities, role families and levels so every filter has something to bite on.
They flow through the same normalisation + upsert path as real crawl data, and
are marked with source_job_id prefixed 'seed-' so a real crawl replaces them
cleanly (a real posting has a different id and the seed simply ages out / closes).
"""
from __future__ import annotations

import datetime as dt
import re

from app.config import load_universities
from app.db import SessionLocal
from app.ingest.runner import _upsert_listings, init_db
from app.models import Listing
from app.normalise.core import RawJob, normalise_record

_NOW = dt.datetime.now(dt.timezone.utc)


def _d(days_ago: int) -> dt.datetime:
    return _NOW - dt.timedelta(days=days_ago)


def _close(days_ahead: int) -> dt.datetime:
    return _NOW + dt.timedelta(days=days_ahead)


# (uni_slug, title, location, classification, work_type, salary, posted_days_ago, closes_in_days, excerpt)
_SEED = [
    # --- Victoria: academic Level B/C in the last 7 days (the brief's demo query) ---
    ("melbourne", "Lecturer in Computer Science", "Parkville, Melbourne VIC", "Academic Level B",
     "Full time, Continuing", "$110,596 - $131,032", 2, 28,
     "Contribute to teaching and research in machine learning within the School of Computing."),
    ("monash", "Senior Lecturer in Mechanical Engineering", "Clayton, Melbourne VIC", "Academic Level C",
     "Full time, Continuing", "$134,000 - $154,000", 3, 30,
     "Lead undergraduate teaching and a research program in advanced manufacturing."),
    ("latrobe", "Lecturer in Nursing", "Bundoora, Melbourne VIC", "Academic Level B",
     "Part time, Fixed term", "$108,000 - $128,000", 5, 21,
     "Teach across the Bachelor of Nursing and contribute to clinical education."),
    ("rmit", "Senior Lecturer, Data Science", "Melbourne CBD VIC", "Academic Level C",
     "Full time, Continuing", "$135,900 - $156,700", 1, 25,
     "Join the School of Computing Technologies to lead data science curriculum."),
    ("deakin", "Lecturer in Marketing", "Burwood, Melbourne VIC", "Academic Level B",
     "Full time, Fixed term", "$109,000 - $129,000", 6, 20,
     "Deliver marketing units and build an applied research profile."),
    ("swinburne", "Associate Professor, Astrophysics", "Hawthorn, Melbourne VIC", "Academic Level D",
     "Full time, Continuing", "$168,000 - $185,000", 4, 35,
     "Leadership role in the Centre for Astrophysics and Supercomputing."),
    # --- Victoria: professional ---
    ("vu", "HR Business Partner", "Footscray, Melbourne VIC", "HEW 8",
     "Full time, Continuing", "$102,000 - $112,000", 2, 18,
     "Partner with faculties on workforce planning and employee relations."),
    ("federation", "Student Services Officer", "Ballarat VIC", "HEW 5",
     "Part time, Fixed term", "$74,000 - $82,000", 8, 14,
     "Frontline student support across enrolment and wellbeing services."),
    # --- NSW ---
    ("sydney", "Postdoctoral Research Fellow, Immunology", "Camperdown, Sydney NSW", "Academic Level A",
     "Full time, Fixed term", "$102,000 - $110,000", 3, 30,
     "Investigate immune signalling within the Charles Perkins Centre."),
    ("unsw", "Professor of Quantum Physics", "Kensington, Sydney NSW", "Academic Level E",
     "Full time, Continuing", None, 10, 45,
     "Provide research leadership in quantum computing and photonics."),
    ("macquarie", "Research Assistant, Marine Biology", "North Ryde, Sydney NSW", "Academic Level A",
     "Part time, Fixed term", "$96,000", 1, 21,
     "Support coral reef ecology fieldwork; remote and hybrid options available."),
    ("uts", "Finance Manager", "Ultimo, Sydney NSW", "HEW 9",
     "Full time, Continuing", "$118,000 - $128,000", 7, 12,
     "Lead financial operations for the Faculty of Engineering and IT."),
    ("newcastle", "Lecturer in Education", "Callaghan, Newcastle NSW", "Academic Level B",
     "Full time, Continuing", "$110,000 - $130,000", 4, 26,
     "Teach initial teacher education and supervise higher degree research."),
    ("wsu", "Laboratory Technician", "Parramatta, Sydney NSW", "HEW 6",
     "Full time, Continuing", "$85,000 - $92,000", 6, 16,
     "Maintain teaching and research labs across the School of Science."),
    ("une", "Deputy Vice-Chancellor (Research)", "Armidale NSW", "Above HEW 10",
     "Full time, Fixed term", None, 14, 40,
     "Executive leadership of the University's research strategy and portfolio."),
    # --- QLD ---
    ("uq", "Senior Lecturer in Law", "St Lucia, Brisbane QLD", "Academic Level C",
     "Full time, Continuing", "$133,000 - $153,000", 2, 28,
     "Contribute to the TC Beirne School of Law across teaching and research."),
    ("qut", "Data Engineer", "Brisbane CBD QLD", "HEW 8",
     "Full time, Fixed term", "$104,000 - $114,000", 5, 15,
     "Build and maintain data pipelines for the Digital Observatory."),
    ("griffith", "Lecturer in Public Health", "Nathan, Brisbane QLD", "Academic Level B",
     "Part time, Continuing", "$109,000 - $129,000", 9, 22,
     "Teach epidemiology and biostatistics within the School of Medicine."),
    ("jcu", "Postdoctoral Fellow, Tropical Ecology", "Townsville QLD", "Academic Level A",
     "Full time, Fixed term", "$100,000", 3, 30,
     "Research reef and rainforest ecosystems in tropical North Queensland."),
    ("cqu", "Marketing Coordinator", "Rockhampton QLD", "HEW 6",
     "Full time, Continuing", "$84,000 - $91,000", 11, 10,
     "Coordinate regional student recruitment marketing campaigns."),
    # --- SA ---
    ("adelaide", "Professor of Artificial Intelligence", "Adelaide SA", "Academic Level E",
     "Full time, Continuing", None, 6, 40,
     "Lead the new Institute for Machine Learning at Adelaide University."),
    ("flinders", "Lecturer in Psychology", "Bedford Park, Adelaide SA", "Academic Level B",
     "Full time, Fixed term", "$108,000 - $128,000", 4, 24,
     "Teach clinical psychology and supervise research placements."),
    ("torrens", "Student Success Coach", "Adelaide SA", "HEW 5",
     "Part time, Fixed term", "$72,000 - $80,000", 8, 13,
     "Support student retention and employability outcomes."),
    # --- WA ---
    ("uwa", "Senior Lecturer in Mining Engineering", "Crawley, Perth WA", "Academic Level C",
     "Full time, Continuing", "$135,000 - $155,000", 3, 27,
     "Join the School of Engineering with a focus on sustainable mining."),
    ("curtin", "IT Support Officer", "Bentley, Perth WA", "HEW 5",
     "Full time, Continuing", "$73,000 - $81,000", 7, 14,
     "Provide desktop and service-desk support across campus."),
    ("ecu", "Lecturer in Cyber Security", "Joondalup, Perth WA", "Academic Level B",
     "Full time, Fixed term", "$110,000 - $130,000", 2, 29,
     "Teach and research in the Security Research Institute."),
    ("murdoch", "Veterinary Technician", "Murdoch, Perth WA", "HEW 6",
     "Full time, Continuing", "$85,000 - $93,000", 12, 9,
     "Support the veterinary teaching hospital and clinical training."),
    # --- ACT / NT / TAS ---
    ("anu", "Professor of Astronomy", "Acton, Canberra ACT", "Academic Level E",
     "Full time, Continuing", None, 5, 42,
     "Research leadership at the Research School of Astronomy and Astrophysics."),
    ("canberra", "Communications Officer", "Bruce, Canberra ACT", "HEW 6",
     "Full time, Fixed term", "$84,000 - $91,000", 9, 15,
     "Support corporate and student communications across channels."),
    ("cdu", "Lecturer in Indigenous Studies", "Casuarina, Darwin NT", "Academic Level B",
     "Full time, Continuing", "$110,000 - $130,000", 4, 25,
     "Teach and research within the Faculty of Arts and Society."),
    ("utas", "Research Fellow, Antarctic Science", "Hobart TAS", "Academic Level A",
     "Full time, Fixed term", "$101,000 - $109,000", 3, 31,
     "Join the Institute for Marine and Antarctic Studies."),
    # --- Divinity / small ---
    ("divinity", "Lecturer in Biblical Studies (New Testament)", "Parkville, Melbourne VIC", None,
     "Part time, Fixed term", None, 7, 20,
     "Trinity College Theological School seeks a Lecturer commencing February 2026."),
]


def seed(reset: bool = False) -> int:
    """Insert seed listings. Returns number inserted. Idempotent (upsert by id)."""
    init_db()
    unis = {u["slug"]: u for u in load_universities()}
    session = SessionLocal()
    try:
        if reset:
            session.query(Listing).delete()
            session.commit()
        records = []
        for (slug, title, loc, classif, wtype, salary, posted, closes, excerpt) in _SEED:
            uni = unis[slug]
            stable = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48]
            raw = RawJob(
                source_job_id=f"seed-{slug}-{stable}",
                title=title, url=uni["careers_url"], location=loc,
                classification_raw=classif, work_type_raw=wtype, salary_raw=salary,
                posted_at=_d(posted), closes_at=_close(closes), excerpt=excerpt,
            )
            records.append(normalise_record(raw, uni))
        _upsert_listings(session, records)
        session.commit()
        return len(records)
    finally:
        session.close()


if __name__ == "__main__":
    n = seed()
    print(f"seeded {n} listings")
