# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — deploy the agent
# MAGIC
# MAGIC Deploys an **already-registered, already-validated** model version. Kept separate from
# MAGIC `14_fleetguard_agent` on purpose: logging and registering are free and repeatable,
# MAGIC whereas this notebook provisions a Model Serving endpoint that **bills for as long as it
# MAGIC runs**. Splitting them means nobody creates billing compute as a side effect of
# MAGIC re-running a build.
# MAGIC
# MAGIC It also means the thing deployed is the exact artifact that passed the round-trip
# MAGIC validation in `14`, not a fresh re-log that happens to look the same.
# MAGIC
# MAGIC `agents.deploy()` does three things in one call: creates the serving endpoint, turns on
# MAGIC inference tables (E-04 — the one AI Gateway feature that works on agent endpoints), and
# MAGIC provisions the Review App.
# MAGIC
# MAGIC **Stop the endpoint when the demo is over.**

# COMMAND ----------

# MAGIC %pip install -q -U mlflow databricks-agents databricks-sdk
# MAGIC %restart_python

# COMMAND ----------

CATALOG, SCHEMA = "bootcamp_students", "fleetguard"
MODEL_NAME = f"{CATALOG}.{SCHEMA}.fleetguard_agent"

dbutils.widgets.text("model_version", "1", "Registered model version")
MODEL_VERSION = dbutils.widgets.get("model_version")
print(f"deploying {MODEL_NAME} version {MODEL_VERSION}")

# COMMAND ----------

import re

from databricks import agents

deployment = agents.deploy(model_name=MODEL_NAME, model_version=MODEL_VERSION)

# `Deployment.endpoint_name` is the documented attribute; the regex is a fallback so a
# missing attribute degrades to a warning rather than losing the endpoint we just created.
ENDPOINT = getattr(deployment, "endpoint_name", None) or re.search(
    r"/endpoints/([^/?]+)", deployment.endpoint_url
).group(1)

print("endpoint_name :", ENDPOINT)
print("endpoint_url  :", deployment.endpoint_url)
print("review_app    :", getattr(deployment, "review_app_url", "n/a"))
print("\nThis endpoint is now billing.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Wait for it to actually serve, then ask it something
# MAGIC
# MAGIC `agents.deploy()` returns as soon as the deployment is *accepted*, not when it is ready —
# MAGIC the container still has to build. Returning here without querying it would repeat I-043:
# MAGIC treating "the call returned" as "the thing works".

# COMMAND ----------

from datetime import timedelta

from databricks.sdk import WorkspaceClient

w = WorkspaceClient()

ep = w.serving_endpoints.wait_get_serving_endpoint_not_updating(
    name=ENDPOINT, timeout=timedelta(minutes=45)
)
print("state:", ep.state)

# COMMAND ----------

import mlflow.deployments

client = mlflow.deployments.get_deploy_client("databricks")

QUESTION = "Which fleet vehicles does recall 17V629000 affect, and what should we do about it?"
resp = client.predict(endpoint=ENDPOINT, inputs={"input": [{"role": "user", "content": QUESTION}]})

text = "".join(
    c.get("text", "")
    for item in resp.get("output", [])
    for c in item.get("content", [])
    if c.get("type") == "output_text"
)
print("--- reply ---\n", text[:2000])

assert text.strip(), "endpoint returned no text"
# Same invariant the build asserts: the agent proposes, it never claims to have launched.
assert "work order" not in text.lower() or "approv" in text.lower(), (
    "served agent mentioned work orders without mentioning approval"
)
print("\nlive endpoint smoke test passed")
