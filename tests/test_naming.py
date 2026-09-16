"""Unit tests for Lakebase / CDF object naming.

These guard a shared schema holding ~296 students' tables, where a naming mistake cannot
be undone cleanly: CDF auto-suffixes on collision silently, and renaming a Postgres table
orphans its history table.
"""

import pytest

from fleetguard.naming import (
    LAKEBASE_TABLES,
    PG_IDENTIFIER_LIMIT,
    PREFIX,
    UnsafeTableName,
    assert_project_table,
    cdf_history_table,
    is_collision_suffixed,
)


class TestTableInventory:
    def test_twelve_current_operational_tables(self):
        assert len(LAKEBASE_TABLES) == 12

    def test_all_carry_the_project_prefix(self):
        assert all(t.startswith(PREFIX) for t in LAKEBASE_TABLES)

    def test_names_are_unique(self):
        assert len(set(LAKEBASE_TABLES)) == len(LAKEBASE_TABLES)

    def test_every_history_name_fits_the_postgres_identifier_limit(self):
        for table in LAKEBASE_TABLES:
            history = cdf_history_table(table).rsplit(".", 1)[-1]
            assert len(history) <= PG_IDENTIFIER_LIMIT, f"{history} is {len(history)} chars"


class TestHistoryNaming:
    def test_follows_the_documented_pattern(self):
        assert (
            cdf_history_table("fleetguard_depot")
            == "bootcamp_students.bootcamp_cdc.lb_fleetguard_depot_history"
        )

    def test_matches_the_name_observed_live(self):
        """Verified end to end in I-038 — exact name, no collision suffix."""
        produced = cdf_history_table("fleetguard_depot").rsplit(".", 1)[-1]
        assert produced == "lb_fleetguard_depot_history"
        assert not is_collision_suffixed(produced)


class TestGuard:
    @pytest.mark.parametrize("name", LAKEBASE_TABLES)
    def test_accepts_project_tables(self, name):
        assert assert_project_table(name) == name

    @pytest.mark.parametrize(
        "name",
        [
            "vehicle",  # generic — would collide in a shared schema
            "audit_log",
            "ai_query_cache_someone_else",  # another student's table
            "fg_vehicle",  # the rejected shorter prefix
            "public_summary",
        ],
    )
    def test_refuses_anything_outside_the_project_prefix(self, name):
        with pytest.raises(UnsafeTableName):
            assert_project_table(name)

    def test_refuses_a_name_whose_history_table_would_overflow(self):
        too_long = PREFIX + "x" * 60
        with pytest.raises(UnsafeTableName, match="over the"):
            assert_project_table(too_long)


class TestCollisionDetection:
    """105 of 256 lb_* tables in the shared destination are auto-suffixed orphans."""

    @pytest.mark.parametrize(
        "name",
        [
            "lb_fleetguard_depot_history_1",
            "lb_ai_suggestion_feedback_ajwaters_history_2",
            "bootcamp_students.bootcamp_cdc.lb_fleetguard_vehicle_history_10",
        ],
    )
    def test_detects_suffixed_names(self, name):
        assert is_collision_suffixed(name)

    @pytest.mark.parametrize(
        "name",
        [
            "lb_fleetguard_depot_history",
            "bootcamp_students.bootcamp_cdc.lb_fleetguard_agent_action_history",
            "silver_complaint",
        ],
    )
    def test_accepts_clean_names(self, name):
        assert not is_collision_suffixed(name)
