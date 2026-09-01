# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — Phase 9: semantic subdivision (replaces global HDBSCAN)
# MAGIC
# MAGIC ## Why HDBSCAN was abandoned
# MAGIC
# MAGIC Global HDBSCAN over the 205,219 narrative embeddings labelled **85.4% of them noise**
# MAGIC (`-1`), leaving 29,909 complaints in 286 clusters. An 8-configuration sweep
# MAGIC (`ops_hdbscan_sweep`) could not fix it:
# MAGIC
# MAGIC - **L2-normalising made no difference** — 85.8% → 85.4% noise on the controlled pair.
# MAGIC   The "unnormalised vectors under Euclidean distance" hypothesis was **wrong**.
# MAGIC - Noise never fell below **75%** at any setting, and the settings that got there
# MAGIC   collapsed to 9–17 clusters with a 5,506-member blob — low noise bought by having no
# MAGIC   discriminating power at all.
# MAGIC
# MAGIC The conclusion is about the data, not the parameters: complaint-narrative embeddings do
# MAGIC not form well-separated density peaks. Complaints about one component are a
# MAGIC *continuum* of phrasings, not islands.
# MAGIC
# MAGIC ## Why a partition is the right tool anyway
# MAGIC
# MAGIC HDBSCAN answers "where are the natural density clusters, and what is noise?" That was
# MAGIC never the question. We need a **grouping key finer than the component code** — a
# MAGIC partition. HDBSCAN's noise label is not a neutral outcome here, it *discards 85% of
# MAGIC the evidence*, which is strictly worse than the v2 grouping it is meant to improve on.
# MAGIC k-means assigns every complaint, and its arbitrary boundaries are acceptable because
# MAGIC the detector only needs a consistent narrower denominator.
# MAGIC
# MAGIC ## Subdivide, do not re-partition
# MAGIC
# MAGIC The measured constraint: v2 has **2,763 series** with a **median of 10 complaints**
# MAGIC over 24 months (mean 74.3 — heavily skewed), and **11,493** months reach the
# MAGIC `MIN_COUNT = 5` needed to fire. Subdividing a 10-complaint series guarantees it can
# MAGIC never fire again. So subdivision is applied **only where there is volume to
# MAGIC subdivide**; smaller series keep their v2 grouping byte-for-byte.
# MAGIC
# MAGIC This is not cherry-picking: a series that cannot reach `MIN_COUNT` never fired under
# MAGIC v2 either, so leaving it alone changes nothing about what v2 detected. And the rule is
# MAGIC a **pure function of complaint volume** — it never looks at which arm a series belongs
# MAGIC to, so it cannot advantage the real arm over the placebo.

# COMMAND ----------

import numpy as np
import pandas as pd
from pyspark.sql import functions as F
from sklearn.cluster import MiniBatchKMeans
from sklearn.preprocessing import normalize

CATALOG, SCHEMA = "bootcamp_students", "fleetguard"
spark.sql(f"USE {CATALOG}.{SCHEMA}")

MIN_SUBDIVIDE = 100  # series smaller than this keep the v2 grouping untouched
TARGET_SUB_SIZE = 50  # aim for sub-series of ~50 complaints over the 24-month window
MAX_SUBS = 12
SEED = 42

# COMMAND ----------

pdf = (
    spark.table("gold_backtest_embedding")
    .filter(F.col("embedding").isNotNull())
    .select("complaint_id", "make", "model", "comp_top", "embedding")
    .toPandas()
)
X = normalize(np.vstack(pdf["embedding"].to_numpy()).astype(np.float32))
print(f"{len(pdf):,} complaints")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Subdivide each high-volume series
# MAGIC
# MAGIC `k = clip(round(n / 50), 2, 12)`. Sub-series carry the v2 key plus a suffix, so the
# MAGIC grouping is strictly a refinement of v2 — every v3 series sits inside exactly one v2
# MAGIC series, which is what makes the two directly comparable.

# COMMAND ----------

pdf["row"] = np.arange(len(pdf))
sub_labels = np.zeros(len(pdf), dtype=int)
stats = []

for (mk, md, ct), grp in pdf.groupby(["make", "model", "comp_top"], sort=False):
    n = len(grp)
    if n < MIN_SUBDIVIDE:
        continue  # keeps sub_label 0 — identical to the v2 grouping
    k = int(np.clip(round(n / TARGET_SUB_SIZE), 2, MAX_SUBS))
    rows = grp["row"].to_numpy()
    km = MiniBatchKMeans(n_clusters=k, random_state=SEED, n_init=3, batch_size=1024)
    sub_labels[rows] = km.fit_predict(X[rows])
    stats.append({"make": mk, "model": md, "comp_top": ct, "n": n, "k": k})

pdf["sub_label"] = sub_labels
sub_stats = pd.DataFrame(stats)
print(f"series subdivided : {len(sub_stats):,}")
print(
    f"complaints in them: {int(sub_stats['n'].sum()):,} ({100 * sub_stats['n'].sum() / len(pdf):.1f}%)"
)
print(f"mean k            : {sub_stats['k'].mean():.1f}" if len(sub_stats) else "")

# COMMAND ----------

out = pdf[["complaint_id", "make", "model", "comp_top", "sub_label"]].copy()
out["series_key_v2"] = out["make"] + "|" + out["model"] + "|" + out["comp_top"]
out["series_key_v3"] = out["series_key_v2"] + "|s" + out["sub_label"].astype(str)

(
    spark.createDataFrame(out)
    .write.mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable("gold_backtest_subcluster")
)
print("wrote gold_backtest_subcluster")

# COMMAND ----------

# MAGIC %md
# MAGIC ## The decisive diagnostic: firing-capable months
# MAGIC
# MAGIC A grouping is only useful if its monthly series can still reach `MIN_COUNT = 5`. v2
# MAGIC has **11,493** such months. If v3 has far fewer, subdivision has destroyed more signal
# MAGIC than it sharpened and the detector will do worse for a mechanical reason — which is
# MAGIC worth knowing *before* running the backtest, not after.

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE TEMP VIEW v3_series AS
SELECT s.complaint_id, s.series_key_v2, s.series_key_v3,
       DATE_TRUNC('MONTH', e.received_date) AS month
FROM gold_backtest_subcluster s
JOIN gold_backtest_embedding e USING (complaint_id)
""")

display(
    spark.sql("""
    SELECT 'v2  (make, model, comp_top)' AS grouping,
           COUNT(DISTINCT series_key_v2) AS series,
           (SELECT COUNT(*) FROM (SELECT series_key_v2, month, COUNT(*) c
                                  FROM v3_series GROUP BY 1,2 HAVING COUNT(*) >= 5)) AS firing_capable_months
    FROM v3_series
    UNION ALL
    SELECT 'v3  (+ semantic subdivision)',
           COUNT(DISTINCT series_key_v3),
           (SELECT COUNT(*) FROM (SELECT series_key_v3, month, COUNT(*) c
                                  FROM v3_series GROUP BY 1,2 HAVING COUNT(*) >= 5))
    FROM v3_series
""")
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Arm symmetry
# MAGIC
# MAGIC Subdivision is driven only by complaint volume, never by arm. This confirms it — the
# MAGIC proportion of each arm that got subdivided should be similar. A large gap would mean
# MAGIC the real arm was handed a sharper detector than its control, which is the I-027
# MAGIC failure mode.

# COMMAND ----------

display(
    spark.sql("""
    SELECT k.arm,
           COUNT(DISTINCT s.complaint_id) AS complaints,
           COUNT(DISTINCT s.series_key_v3) AS v3_series,
           ROUND(100.0 * SUM(CASE WHEN s.sub_label > 0 THEN 1 ELSE 0 END)
                 / COUNT(*), 1) AS pct_in_subdivided_partition
    FROM gold_backtest_subcluster s
    JOIN gold_backtest_scope k
      ON k.make = s.make AND k.model = s.model AND k.comp_top = s.comp_top
    GROUP BY k.arm ORDER BY k.arm
""")
)
