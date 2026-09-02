# Databricks notebook source
# MAGIC %md
# MAGIC # Inspect an agent evaluation run
# MAGIC
# MAGIC Reads an existing `mlflow.genai.evaluate` run and prints, per case, what each scorer
# MAGIC said and why. No agent calls, no judges — seconds, not the hour the evaluation itself
# MAGIC takes.
# MAGIC
# MAGIC **Why this exists.** The first evaluation reported
# MAGIC `HARD GATE FAILED — never_invents_a_recall: 2/10`. MLflow's own aggregate for that
# MAGIC scorer was **0.667**, i.e. 2 of the **3** applicable cases — the notebook's summary
# MAGIC helper counted `None` (not-applicable) as failure and quoted a denominator three times
# MAGIC too large. The gate fired correctly; the number attached to it did not.
# MAGIC
# MAGIC That is the same failure mode as I-050 one level up: a summary that looks precise,
# MAGIC reads plausibly, and is wrong. Aggregates come from MLflow here, never from ad-hoc
# MAGIC parsing of the results table.

# COMMAND ----------

# MAGIC %pip install -q -U mlflow
# MAGIC %restart_python

# COMMAND ----------

dbutils.widgets.text("run_id", "f9a5fa7adf2a48eba2a186f1de4bbe36", "MLflow eval run id")
RUN_ID = dbutils.widgets.get("run_id")

import mlflow

client = mlflow.MlflowClient()
run = client.get_run(RUN_ID)
print(f"run {RUN_ID} · {run.info.status}\n")

print("aggregate metrics (MLflow's own — the trustworthy ones):")
for k, v in sorted(run.data.metrics.items()):
    print(f"  {k:<34} {v:.3f}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Per-case detail
# MAGIC
# MAGIC `search_traces` returns one row per evaluated case with its assessments attached.

# COMMAND ----------

# `locations` must name the run's OWN experiment. Left unset, search_traces looks in the
# *current notebook's* experiment and fails — which is a helpful error, because the silent
# alternative would be an empty result read as "no traces, nothing to see".
traces = mlflow.search_traces(run_id=RUN_ID, locations=[run.info.experiment_id], return_type="list")
print(f"{len(traces)} traces\n")

for t in traces:
    req = str(getattr(t.info, "request_preview", "") or "")[:110].replace("\n", " ")
    print("=" * 100)
    print("Q:", req)

    for a in t.info.assessments or []:
        name = getattr(a, "name", "?")
        fb = getattr(a, "feedback", None)
        value = getattr(fb, "value", None) if fb else None
        if value is None:
            continue  # not applicable to this case — not a failure
        rationale = (getattr(a, "rationale", "") or "").replace("\n", " ")
        verdict = "PASS" if value in (True, "yes", "pass", 1) else "FAIL"
        print(f"   {verdict:<5} {name:<24} {str(value):<8} {rationale[:160]}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Only the failures
# MAGIC
# MAGIC What to fix, with the judge's stated reason — which is the part that says whether the
# MAGIC agent was wrong or the scorer was.

# COMMAND ----------

fails = 0
for t in traces:
    for a in t.info.assessments or []:
        fb = getattr(a, "feedback", None)
        value = getattr(fb, "value", None) if fb else None
        if value is None or value in (True, "yes", "pass", 1):
            continue
        fails += 1
        print("=" * 100)
        print("Q:", str(getattr(t.info, "request_preview", "") or "")[:140].replace("\n", " "))
        print("scorer:", getattr(a, "name", "?"), "→", value)
        print("why   :", (getattr(a, "rationale", "") or "")[:900])
        print()

print(f"\n{fails} failing assessments")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Return the failures as the notebook result
# MAGIC
# MAGIC `print` output is visible in the run page but is **not** retrievable through the Jobs
# MAGIC API, so a caller driving this from the CLI sees a successful run and no findings.
# MAGIC `dbutils.notebook.exit` puts the answer where the API can read it.

# COMMAND ----------

import json

report = {"run_id": RUN_ID, "metrics": run.data.metrics, "failures": []}

for t in traces:
    for a in t.info.assessments or []:
        fb = getattr(a, "feedback", None)
        value = getattr(fb, "value", None) if fb else None
        if value is None or value in (True, "yes", "pass", 1):
            continue
        report["failures"].append(
            {
                "question": str(getattr(t.info, "request_preview", "") or "")[:200],
                # The answer, not just the verdict. A scorer alleging a violation is a claim
                # about text; judging the claim without reading that text is how a false
                # positive becomes a "finding".
                "answer": str(getattr(t.info, "response_preview", "") or "")[:1200],
                "scorer": getattr(a, "name", "?"),
                "value": str(value),
                "rationale": (getattr(a, "rationale", "") or "")[:700],
            }
        )

dbutils.notebook.exit(json.dumps(report, default=str))
