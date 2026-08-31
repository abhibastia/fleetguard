# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — Postgres privilege diagnostic (READ ONLY)
# MAGIC
# MAGIC `06_create_depot_and_verify` aborted with *no CREATE privilege on bootcamp_students*.
# MAGIC That is a genuine Postgres requirement — `CREATE TABLE` needs `CREATE` on the schema.
# MAGIC
# MAGIC This runs **no DDL**. It answers: who am I in Postgres, what roles do I hold, who
# MAGIC owns the schema, what is actually granted, and how did the other 354 tables get
# MAGIC created — so the ask to whoever administers this is specific rather than vague.

# COMMAND ----------

import psycopg
from databricks.sdk import WorkspaceClient

PROJECT = "projects/summer-bootcamp-2026-v2"
ENDPOINT = f"{PROJECT}/branches/production/endpoints/primary"
PG_SCHEMA = "bootcamp_students"
PG_DB = "databricks_postgres"

w = WorkspaceClient()
host = w.postgres.get_endpoint(name=ENDPOINT).status.hosts.host
token = w.postgres.generate_database_credential(endpoint=ENDPOINT).token
user = w.current_user.me().user_name

conn = psycopg.connect(
    host=host,
    user=user,
    password=token,
    dbname=PG_DB,
    sslmode="require",
    autocommit=True,
)


FINDINGS = []


def q(sql, params=None, title=""):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        rows = cur.fetchall()
        cols = [d.name for d in cur.description]
    # persist as text so the result is readable from outside the notebook UI
    for r in rows[:40]:
        FINDINGS.append((title, " | ".join(f"{c}={'' if v is None else str(v)[:60]}"
                                           for c, v in zip(cols, r))))
    if not rows:
        FINDINGS.append((title, "(no rows)"))
    if title:
        print(f"\n--- {title} ---")
    if not rows:
        print("  (no rows)")
        return rows
    print("  " + " | ".join(cols))
    for r in rows[:40]:
        print("  " + " | ".join("" if v is None else str(v)[:52] for v in r))
    if len(rows) > 40:
        print(f"  ... {len(rows) - 40} more")
    return rows


# COMMAND ----------

q("SELECT current_user, session_user, current_database()", title="identity")

q(
    """
SELECT r.rolname AS role_i_am_member_of, r.rolcanlogin, r.rolsuper, r.rolcreatedb
FROM pg_roles r
JOIN pg_auth_members m ON m.roleid = r.oid
JOIN pg_roles me ON me.oid = m.member
WHERE me.rolname = current_user
ORDER BY 1
""",
    title="role memberships (privileges can be inherited through these)",
)

q(
    """
SELECT nspname AS schema, pg_get_userbyid(nspowner) AS owner
FROM pg_namespace WHERE nspname = %s
""",
    (PG_SCHEMA,),
    title="who owns the schema",
)

q(
    """
SELECT
  has_schema_privilege(current_user, %s, 'CREATE') AS can_create,
  has_schema_privilege(current_user, %s, 'USAGE')  AS can_use
""",
    (PG_SCHEMA, PG_SCHEMA),
    title="my effective privileges on the schema",
)

q(
    """
SELECT grantee, privilege_type
FROM information_schema.role_usage_grants
WHERE object_schema = %s
ORDER BY 1,2
""",
    (PG_SCHEMA,),
    title="usage grants recorded on the schema",
)

# The decisive question: how did the existing 354 tables get created — and by whom?
q(
    """
SELECT tableowner, COUNT(*) AS tables
FROM pg_tables WHERE schemaname = %s
GROUP BY 1 ORDER BY tables DESC
""",
    (PG_SCHEMA,),
    title="owners of existing tables in the schema",
)

# Can I create a schema of my own instead? (CDF is bound to bootcamp_students, so this
# is a fallback only — but worth knowing.)
q(
    "SELECT has_database_privilege(current_user, current_database(), 'CREATE') AS can_create_schema",
    title="can I create my own schema in this database",
)

q(
    """
SELECT nspname AS my_schemas FROM pg_namespace
WHERE pg_get_userbyid(nspowner) = current_user ORDER BY 1
""",
    title="schemas I already own",
)

# COMMAND ----------

conn.close()

from pyspark.sql.types import StringType, StructField, StructType

schema = StructType([StructField("check", StringType()), StructField("detail", StringType())])
(spark.createDataFrame(FINDINGS, schema=schema)
 .write.mode("overwrite").option("overwriteSchema", "true")
 .saveAsTable("bootcamp_students.fleetguard.ops_pg_privilege_diagnostic"))
print(f"persisted {len(FINDINGS)} findings — no DDL executed against Postgres")
