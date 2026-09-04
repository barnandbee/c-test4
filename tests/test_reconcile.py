"""Tests for the safe-reconciliation rule: a bad crawl must not close listings."""
from app.ingest.reconcile import is_suspect, plan_reconciliation


class TestIsSuspect:
    def test_first_run_never_suspect(self):
        assert is_suspect(prev_count=0, new_count=0, min_ratio=0.5) is False

    def test_zero_after_many_is_suspect(self):
        assert is_suspect(prev_count=40, new_count=0, min_ratio=0.5) is True

    def test_big_drop_is_suspect(self):
        assert is_suspect(prev_count=40, new_count=5, min_ratio=0.5) is True

    def test_small_drop_is_fine(self):
        assert is_suspect(prev_count=40, new_count=38, min_ratio=0.5) is False

    def test_growth_is_fine(self):
        assert is_suspect(prev_count=40, new_count=45, min_ratio=0.5) is False


class TestPlan:
    def test_healthy_run_closes_missing(self):
        plan = plan_reconciliation(
            existing_open_ids={"a", "b", "c"},
            seen_ids={"a", "b"},
            prev_count=3, new_count=2, min_ratio=0.5,
        )
        assert plan.suspect is False
        assert plan.upsert is True
        assert plan.close_ids == ["c"]

    def test_suspect_run_closes_nothing(self):
        plan = plan_reconciliation(
            existing_open_ids={"a", "b", "c", "d", "e"},
            seen_ids=set(),
            prev_count=5, new_count=0, min_ratio=0.5,
        )
        assert plan.suspect is True
        assert plan.close_ids == []
        assert plan.upsert is False  # nothing to upsert

    def test_suspect_partial_still_upserts_but_no_close(self):
        plan = plan_reconciliation(
            existing_open_ids={"a", "b", "c", "d"},
            seen_ids={"a"},
            prev_count=4, new_count=1, min_ratio=0.5,
        )
        assert plan.suspect is True
        assert plan.upsert is True     # the 1 real record still refreshes
        assert plan.close_ids == []    # but nothing gets closed
