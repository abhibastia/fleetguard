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
        # Passing an empty tuple still makes psycopg parse placeholders, so a literal
        # LIKE 'pg_%' raises "only '%s','%b','%t' are allowed as placeholders".
        # Omit params entirely when there are none.
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        rows = cur.fetchall()
        cols = [d.name for d in cur.description]
    # persist as text so the result is readable from outside the notebook UI
    for r in rows[:40]:
        FINDINGS.append((title, " | ".join(f"{c}={'' if v is None else str(v)[:60]}"
                                           for c, v in zip(cols, r, strict=False))))
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

# Are `users` / `student` LOGIN users or group roles? In Postgres these are the same
# object; a "user" is simply a role with rolcanlogin=true. This decides whether
# GRANT "users" TO me is even a coherent request.
q("""
SELECT rolname, rolcanlogin, rolinherit, rolsuper, rolcreaterole,
       (SELECT COUNT(*) FROM pg_auth_members m WHERE m.roleid = r.oid) AS members
FROM pg_roles r
WHERE rolname IN ('users','student','databricks_writer_16406') OR rolname = current_user
ORDER BY rolname
""", title="ROLE TYPES: are users/student login users or groups")

# Every non-login (group) role, and whether it actually confers CREATE on the schema.
# These are the memberships worth asking for.
q("""
SELECT r.rolname AS group_role,
       has_schema_privilege(r.rolname, 'bootcamp_students', 'CREATE') AS grants_create,
       (SELECT COUNT(*) FROM pg_auth_members m WHERE m.roleid = r.oid) AS members
FROM pg_roles r
WHERE r.rolcanlogin = false AND r.rolname NOT LIKE 'pg_%'
ORDER BY grants_create DESC, members DESC
""", title="GROUP ROLES conferring CREATE on bootcamp_students")

# What already exists under our prefix, and who owns it — so a re-run of the create
# notebook cannot clobber anything a manual test left behind.
q("""
SELECT tablename, tableowner,
       (SELECT c.relreplident FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = t.schemaname AND c.relname = t.tablename) AS replica_identity
FROM pg_tables t
WHERE schemaname = 'bootcamp_students' AND tablename LIKE 'fleetguard%'
ORDER BY tablename
""", title="EXISTING fleetguard_* tables in Postgres")

# COMMAND ----------

conn.close()

from pyspark.sql.types import StringType, StructField, StructType

schema = StructType([StructField("check", StringType()), StructField("detail", StringType())])
(spark.createDataFrame(FINDINGS, schema=schema)
 .write.mode("overwrite").option("overwriteSchema", "true")
 .saveAsTable("bootcamp_students.fleetguard.ops_pg_privilege_diagnostic"))
print(f"persisted {len(FINDINGS)} findings — no DDL executed against Postgres")
