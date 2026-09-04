# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — technician roster (`fleetguard_technician`)
# MAGIC
# MAGIC The assign-to-technician feature needs a real roster to assign against, not a free-text
# MAGIC field on `fleetguard_work_order.assigned_to` — free text has no referential integrity and
# MAGIC can't be validated against the work order's own depot. This creates that roster: one real
# MAGIC table, seeded against the *actual* depot IDs already in `fleetguard_depot` (queried live,
# MAGIC not assumed), ~2 technicians per depot.
# MAGIC
# MAGIC No Delta/gold-layer round-trip for this one — unlike vehicles/depots, a technician roster
# MAGIC has no NHTSA or analytical purpose, it is purely an operational Lakebase concept, so it is
# MAGIC generated directly here. Names come from a small local list (no `faker` in this venv), not
# MAGIC because realism doesn't matter but because inventing a new dependency for ~120 rows of
# MAGIC label text is not proportionate.

# COMMAND ----------

import os
import random

if os.getenv("DATABRICKS_RUNTIME_VERSION"):
    os.environ.setdefault("PSYCOPG_IMPL", "python")  # I-045, see 16_add_work_order_status_check.py

import psycopg  # noqa: E402
from databricks.sdk import WorkspaceClient  # noqa: E402

PROJECT = "projects/summer-bootcamp-2026-v2"
ENDPOINT = f"{PROJECT}/branches/production/endpoints/primary"
PG_SCHEMA = "bootcamp_students"
PG_DB = "databricks_postgres"

w = WorkspaceClient()
host = w.postgres.get_endpoint(name=ENDPOINT).status.hosts.host
owner_token = w.postgres.generate_database_credential(endpoint=ENDPOINT).token
owner = w.current_user.me().user_name

conn = psycopg.connect(
    host=host, user=owner, password=owner_token, dbname=PG_DB, sslmode="require", autocommit=False
)
print(f"connected to {PG_DB} as {owner}")

# COMMAND ----------

with conn.cursor() as cur:
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {PG_SCHEMA}.fleetguard_technician (
            technician_id TEXT PRIMARY KEY,
            name          TEXT NOT NULL,
            depot_id      TEXT NOT NULL,
            active        BOOLEAN NOT NULL DEFAULT true,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    cur.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_fg_technician_depot
        ON {PG_SCHEMA}.fleetguard_technician (depot_id)
    """)
    cur.execute(f"ALTER TABLE {PG_SCHEMA}.fleetguard_technician REPLICA IDENTITY FULL")
conn.commit()
print("table + index created, REPLICA IDENTITY FULL set (CDF will pick this table up)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Seed — against the real depot list, queried live
# MAGIC
# MAGIC Idempotent: `INSERT ... ON CONFLICT DO NOTHING` keyed on `technician_id`, so re-running
# MAGIC this notebook after depots change only adds technicians for genuinely new depots.

# COMMAND ----------

with conn.cursor() as cur:
    cur.execute(f"SELECT depot_id FROM {PG_SCHEMA}.fleetguard_depot ORDER BY depot_id")
    depot_ids = [r[0] for r in cur.fetchall()]
assert depot_ids, "fleetguard_depot returned no rows — check PG_SCHEMA/table before seeding"
print(f"{len(depot_ids)} depots found, e.g. {depot_ids[:3]}")

FIRST_NAMES = [
    "James",
    "Maria",
    "Robert",
    "Linda",
    "Michael",
    "Patricia",
    "David",
    "Barbara",
    "Carlos",
    "Nancy",
    "Kevin",
    "Susan",
    "Brian",
    "Karen",
    "Jose",
    "Angela",
    "Anthony",
    "Michelle",
    "Mark",
    "Laura",
]
LAST_NAMES = [
    "Nguyen",
    "Smith",
    "Garcia",
    "Johnson",
    "Patel",
    "Williams",
    "Rodriguez",
    "Brown",
    "Martinez",
    "Davis",
    "Lopez",
    "Wilson",
    "Gonzalez",
    "Anderson",
    "Perez",
    "Taylor",
    "Sanchez",
    "Thomas",
    "Ramirez",
    "Moore",
]

random.seed(20260904)  # reproducible roster across re-runs, not cryptographic
TECHS_PER_DEPOT = 2

rows = []
for depot_id in depot_ids:
    for n in range(1, TECHS_PER_DEPOT + 1):
        name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
        rows.append((f"TECH-{depot_id}-{n}", name, depot_id))

print(f"prepared {len(rows)} technicians across {len(depot_ids)} depots")

# COMMAND ----------

with conn.cursor() as cur:
    cur.executemany(
        f"""INSERT INTO {PG_SCHEMA}.fleetguard_technician (technician_id, name, depot_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (technician_id) DO NOTHING""",
        rows,
    )
conn.commit()

with conn.cursor() as cur:
    cur.execute(f"SELECT COUNT(*) FROM {PG_SCHEMA}.fleetguard_technician")
    total = cur.fetchone()[0]
    cur.execute(f"SELECT COUNT(DISTINCT depot_id) FROM {PG_SCHEMA}.fleetguard_technician")
    depots_covered = cur.fetchone()[0]
print(f"committed: {total} technicians across {depots_covered} depots")
assert depots_covered == len(depot_ids), (
    f"only {depots_covered} of {len(depot_ids)} depots have a technician — "
    "check for a depot_id typo or a conflicting prior seed"
)

conn.close()
