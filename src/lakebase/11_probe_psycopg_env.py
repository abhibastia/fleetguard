# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — diagnostic: why does `import psycopg` abort the kernel?
# MAGIC
# MAGIC `fleetguard-load-reference-from-gold` dies with SIGABRT (exit 134) inside
# MAGIC `psycopg.pq.import_from_libpq` — before any project code runs. Deterministic across
# MAGIC two runs. `fleetguard-create-remaining-tables` used the **identical** environment
# MAGIC spec (`psycopg[binary]`, `databricks-sdk>=0.89.0`, client 3) and succeeded a day
# MAGIC earlier, which points at the unpinned dependency resolving to a different build.
# MAGIC
# MAGIC This notebook is **read-only** — it opens no Postgres connection and writes nothing.
# MAGIC
# MAGIC The trick is importing `psycopg` in a **subprocess**. An abort in the notebook kernel
# MAGIC destroys the very output that would explain it; in a child process the crash is
# MAGIC contained and its stderr comes back intact.

# COMMAND ----------

import glob
import importlib.metadata as md
import subprocess
import sys

print(f"python     : {sys.version}")
print(f"executable : {sys.executable}\n")

for pkg in ("psycopg", "psycopg-binary", "psycopg-pool", "databricks-sdk"):
    try:
        print(f"  {pkg:<16} {md.version(pkg)}")
    except Exception as e:  # noqa: BLE001 - reporting, not handling
        print(f"  {pkg:<16} NOT INSTALLED ({type(e).__name__})")

# COMMAND ----------

# MAGIC %md
# MAGIC ## The bundled libpq
# MAGIC
# MAGIC `psycopg[binary]` ships its own `libpq` plus its OpenSSL dependencies. A SIGABRT at
# MAGIC load time is usually those colliding with the ones the Spark kernel already has open.

# COMMAND ----------

for pat in (
    "/local_disk0/.ephemeral_nfs/envs/*/lib/python3.*/site-packages/psycopg_binary*",
    "/local_disk0/.ephemeral_nfs/envs/*/lib/python3.*/site-packages/psycopg_binary/*.so*",
    "/local_disk0/.ephemeral_nfs/envs/*/lib/python3.*/site-packages/psycopg_binary.libs/*",
):
    hits = glob.glob(pat)
    print(f"\n{pat}\n  -> {len(hits)} match(es)")
    for h in sorted(hits)[:25]:
        print(f"     {h}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Import in a child process
# MAGIC
# MAGIC Returncode `-6` / `134` is SIGABRT. Whatever the loader printed to stderr immediately
# MAGIC before aborting is the actual diagnosis.

# COMMAND ----------

probe = (
    "import psycopg, psycopg.pq;"
    "print('IMPORT OK', psycopg.__version__, 'libpq', psycopg.pq.version())"
)
r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=180)
print(f"returncode: {r.returncode}  ({'SIGABRT' if r.returncode in (-6, 134) else ''})")
print(f"\n--- stdout ---\n{r.stdout}")
print(f"\n--- stderr (tail) ---\n{r.stderr[-4000:]}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Does the pure-Python implementation load?
# MAGIC
# MAGIC `PSYCOPG_IMPL=python` skips the compiled binding and uses `ctypes` against a system
# MAGIC `libpq`. If this succeeds while the binary one aborts, the fix is to select the
# MAGIC implementation rather than to pin a version.

# COMMAND ----------

import os

env = dict(os.environ, PSYCOPG_IMPL="python")
r2 = subprocess.run(
    [sys.executable, "-c", probe], capture_output=True, text=True, timeout=180, env=env
)
print(f"PSYCOPG_IMPL=python returncode: {r2.returncode}")
print(f"\n--- stdout ---\n{r2.stdout}")
print(f"\n--- stderr (tail) ---\n{r2.stderr[-2500:]}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Persist the findings
# MAGIC
# MAGIC A job run's printed output is **not** retrievable through the API unless the notebook
# MAGIC calls `dbutils.notebook.exit()` — `get-run-output` returns an empty `notebook_output`
# MAGIC and the diagnosis is stranded in the run page. Writing to a table makes the result
# MAGIC queryable, which is the same reason `ops_hybrid_query_test` exists.

# COMMAND ----------


def _version(pkg):
    try:
        return md.version(pkg)
    except Exception:  # noqa: BLE001 - reporting, not handling
        return "NOT INSTALLED"


report = [
    {
        "psycopg_version": _version("psycopg"),
        "psycopg_binary_version": _version("psycopg-binary"),
        "sdk_version": _version("databricks-sdk"),
        "binary_returncode": int(r.returncode),
        "binary_stdout": r.stdout[-2000:],
        "binary_stderr": r.stderr[-4000:],
        "pure_returncode": int(r2.returncode),
        "pure_stdout": r2.stdout[-2000:],
        "pure_stderr": r2.stderr[-4000:],
    }
]
(
    spark.createDataFrame(report)
    .write.mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable("bootcamp_students.fleetguard.ops_psycopg_probe")
)
print("persisted to ops_psycopg_probe")
