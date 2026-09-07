"""Pay-grade ordering used by the filter facet and analytics dimension."""
from app.queries import pay_grade_sort_key


def test_pay_grades_order_hew_then_academic_then_unspecified():
    labels = ["Not specified", "Academic Level B", "HEW 10", "HEW 2", "Academic Level A", "HEW 7"]
    ordered = sorted(labels, key=pay_grade_sort_key)
    assert ordered == ["HEW 2", "HEW 7", "HEW 10", "Academic Level A",
                       "Academic Level B", "Not specified"]


def test_hew_numeric_not_lexical():
    # HEW 10 must sort after HEW 9, not before HEW 2 (lexical would break this)
    assert sorted(["HEW 10", "HEW 9", "HEW 2"], key=pay_grade_sort_key) == \
        ["HEW 2", "HEW 9", "HEW 10"]
