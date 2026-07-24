"""Normalisation unit tests — the part most likely to be done badly, per the brief."""
from app.normalise.classify import (
    classify_discipline,
    classify_role_family,
    parse_salary,
    parse_time_fraction,
    parse_work_type,
)
from app.normalise.core import RawJob, make_id, normalise_record
from app.normalise.levels import classify_level


class TestLevelBands:
    def test_academic_levels_map_to_ordered_bands(self):
        assert classify_level("Academic Level A")["level_band"] == "early-career"
        assert classify_level("Academic Level B")["level_band"] == "mid"
        assert classify_level("Academic Level C")["level_band"] == "senior"
        assert classify_level("Academic Level D")["level_band"] == "leadership"
        assert classify_level("Academic Level E")["level_band"] == "leadership"

    def test_academic_scale_recorded(self):
        result = classify_level("Academic Level B")
        assert result["level_scale"] == "academic"
        assert result["level_label"] == "Academic Level B"

    def test_hew_professional_levels(self):
        assert classify_level("HEW 3")["level_band"] == "entry"
        assert classify_level("HEW 5")["level_band"] == "early-career"
        assert classify_level("HEW 7")["level_band"] == "mid"
        assert classify_level("HEW 9")["level_band"] == "senior"
        assert classify_level("HEW 10")["level_band"] == "leadership"

    def test_hew_aliases(self):
        assert classify_level("HEO 7")["level_band"] == "mid"
        assert classify_level("Professional Level 8")["level_band"] == "senior"

    def test_senior_executive_marker(self):
        assert classify_level("Above HEW 10")["level_band"] == "leadership"

    def test_keyword_fallback_when_no_scale_token(self):
        assert classify_level(None, "Professor of Physics")["level_band"] == "leadership"
        assert classify_level(None, "Associate Lecturer")["level_band"] == "early-career"
        assert classify_level(None, "Lecturer in History")["level_band"] == "mid"

    def test_unclassifiable_returns_none(self):
        assert classify_level(None, "Xyzzy widget")["level_band"] is None


class TestRoleFamily:
    def test_research_beats_academic(self):
        assert classify_role_family("Postdoctoral Research Fellow") == "research"

    def test_academic(self):
        assert classify_role_family("Lecturer in Computer Science") == "academic"

    def test_executive(self):
        assert classify_role_family("Deputy Vice-Chancellor (Research)") == "executive"

    def test_technical(self):
        assert classify_role_family("Laboratory Technician") == "technical"

    def test_professional_default(self):
        assert classify_role_family("HR Business Partner") == "professional"

    def test_teaching_only(self):
        assert classify_role_family("Teaching-Focused Lecturer") == "teaching-only"


class TestDiscipline:
    def test_health(self):
        assert classify_discipline("Lecturer in Nursing") == "Health & Medicine"

    def test_computing(self):
        assert classify_discipline("Senior Lecturer in Data Science") == "Information & Computing"

    def test_professional_function(self):
        assert classify_discipline("Library Officer", role_family="professional") == \
            "Library & Information Services"


class TestWorkTypeAndTime:
    def test_continuing(self):
        assert parse_work_type("Full time, Continuing") == "continuing"

    def test_fixed_term(self):
        assert parse_work_type("Fixed term appointment") == "fixed-term"

    def test_casual(self):
        assert parse_work_type("Casual / sessional") == "casual"

    def test_time_fraction(self):
        assert parse_time_fraction("Part time, Fixed term") == "part-time"
        assert parse_time_fraction("Full time, Continuing") == "full-time"


class TestSalary:
    def test_range(self):
        result = parse_salary("$110,596 - $131,032 per annum")
        assert result["salary_min"] == 110596
        assert result["salary_max"] == 131032

    def test_single_value(self):
        result = parse_salary("$98,000 per annum")
        assert result["salary_min"] == 98000
        assert result["salary_max"] == 98000

    def test_no_salary(self):
        result = parse_salary("Salary not disclosed")
        assert result["salary_min"] is None
        assert result["salary_raw"] is None


class TestMakeIdAndRecord:
    def test_id_is_stable(self):
        assert make_id("uwa", "12345") == make_id("uwa", "12345")
        assert make_id("uwa", "12345") != make_id("anu", "12345")

    def test_normalise_record_full(self):
        uni = {"slug": "sydney", "name": "University of Sydney", "state": "NSW"}
        raw = RawJob(
            source_job_id="R001",
            title="Senior Lecturer in Marine Biology",
            url="https://example.edu.au/job/R001",
            location="Remote / Hybrid",
            classification_raw="Academic Level C",
            work_type_raw="Full time, Continuing",
            salary_raw="$130,000 - $150,000",
            excerpt="A great role " * 60,
        )
        rec = normalise_record(raw, uni)
        assert rec["level_band"] == "senior"
        assert rec["role_family"] == "academic"  # "Lecturer" -> academic (no research keyword present)
        assert rec["discipline"] == "Natural & Physical Sciences"
        assert rec["work_type"] == "continuing"
        assert rec["time_fraction"] == "full-time"
        assert rec["remote_flag"] is True
        assert rec["salary_min"] == 130000
        assert len(rec["excerpt"]) <= 301  # excerpt truncated, never full description
        assert rec["university"] == "University of Sydney"
