"""Skills extraction — the reviewable, deterministic taxonomy match."""
from app.normalise.skills import extract_skills, skill_category


def test_extracts_multiple_skills():
    s = extract_skills("Postdoctoral Fellow using Python and machine learning for data analysis")
    assert "Python" in s
    assert "Machine learning" in s
    assert "Data analysis" in s


def test_teaching_and_research_terms():
    s = extract_skills("Lecturer — teaching and curriculum development in data science")
    assert "Teaching" in s
    assert "Curriculum development" in s
    assert "Data science" in s


def test_management_terms():
    s = extract_skills("Manager: project management, stakeholder engagement and budgeting")
    assert {"Project management", "Stakeholder engagement", "Budget management"} <= set(s)


def test_clinical_terms():
    s = extract_skills("Registered Nurse — AHPRA registration and patient care essential")
    assert "Nursing" in s
    assert "AHPRA registration" in s
    assert "Clinical practice" in s


def test_word_boundaries_avoid_false_positives():
    # 'airport' must not trigger the AI skill; 'grand' must not trigger grants
    s = extract_skills("Airport logistics coordinator for a grand new terminal")
    assert "Artificial intelligence" not in s
    assert "Grant writing" not in s


def test_dedupe_and_empty():
    assert extract_skills("teaching teaching TEACHING") == ["Teaching"]
    assert extract_skills("", None) == []


def test_category_lookup():
    assert skill_category("Python") == "Data & software"
    assert skill_category("Nursing") == "Clinical & health"
