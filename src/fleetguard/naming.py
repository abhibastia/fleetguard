"""Object naming for the Lakebase / CDF layer.

Naming is codified rather than left to convention because **recovery from a mistake is
lossy**: Lakebase CDF auto-suffixes its destination table on collision (`lb_x_history_1`)
*silently* rather than erroring, and renaming a Postgres table orphans its history table.
105 of the 256 `lb_*` tables already in the shared destination are exactly such orphans.
See docs/ISSUES.md I-036.
"""

from __future__ import annotations

PG_SCHEMA = "bootcamp_students"
PG_DATABASE = "databricks_postgres"
CDF_DESTINATION = "bootcamp_students.bootcamp_cdc"
UC_SCHEMA = "bootcamp_students.fleetguard"

PREFIX = "fleetguard_"

#: The eleven operational tables, mapping 1:1 to proposal §4.4.
LAKEBASE_TABLES: tuple[str, ...] = (
    "fleetguard_vehicle",
    "fleetguard_depot",
    "fleetguard_defect_signal",
    "fleetguard_recall_campaign",
    "fleetguard_vehicle_exposure",
    "fleetguard_service_campaign",
    "fleetguard_work_order",
    "fleetguard_agent_action",
    "fleetguard_approval",
    "fleetguard_audit_log",
    "fleetguard_public_summary",
)

# Postgres identifiers are capped at 63 characters; the history name adds 11.
PG_IDENTIFIER_LIMIT = 63


class UnsafeTableName(ValueError):
    """Raised when a name would be unsafe to create in the shared schema."""


def cdf_history_table(pg_table: str) -> str:
    """Fully-qualified Unity Catalog name of the CDF history table for `pg_table`."""
    return f"{CDF_DESTINATION}.lb_{pg_table}_history"


def assert_project_table(name: str) -> str:
    """Guard used before any DDL. Returns the name, or raises.

    The shared Postgres schema holds tables belonging to ~296 students. Nothing outside
    the project prefix may ever be created, altered or dropped by this codebase.
    """
    if not name.startswith(PREFIX):
        raise UnsafeTableName(
            f"{name!r} does not start with {PREFIX!r} — refusing to touch a shared schema"
        )
    history = f"lb_{name}_history"
    if len(history) > PG_IDENTIFIER_LIMIT:
        raise UnsafeTableName(
            f"history table {history!r} is {len(history)} chars, over the "
            f"{PG_IDENTIFIER_LIMIT}-char limit"
        )
    return name


def is_collision_suffixed(history_table: str) -> bool:
    """True if a CDF destination name carries an auto-suffix like `_history_1`.

    A suffixed name means a silent collision occurred and the history table is orphaned
    from the source it appears to belong to. This is the check that catches it.
    """
    tail = history_table.rsplit(".", 1)[-1]
    if not tail.startswith("lb_"):
        return False
    parts = tail.rsplit("_", 1)
    return len(parts) == 2 and parts[1].isdigit() and parts[0].endswith("_history")
