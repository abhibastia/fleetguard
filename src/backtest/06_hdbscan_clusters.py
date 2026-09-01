# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — Phase 9: HDBSCAN over the narrative embeddings
# MAGIC
# MAGIC Assigns a semantic cluster to each of the 205,219 embedded complaints. v3's detector
# MAGIC then groups by `(make, model, cluster)` where v2 grouped by `(make, model, comp_top)`.
# MAGIC
# MAGIC ## The mechanism being tested
# MAGIC
# MAGIC A component code like `SERVICE BRAKES, HYDRAULIC` is a broad bucket. A specific defect
# MAGIC ramp — one failing part, one failure mode — is a *fraction* of that bucket's monthly
# MAGIC volume, so its rise is damped by everything else in the same code. The claim is that a
# MAGIC semantic cluster isolates the failure mode, shrinking the denominator and letting the
# MAGIC same z-score detector fire earlier. **This is a signal-to-noise argument, not a
# MAGIC cleverness argument**, and it can fail: clusters finer than `comp_top` mean fewer
# MAGIC complaints per monthly series, and a series that cannot reach `MIN_COUNT = 5` can
# MAGIC never fire at all. Fragmentation is the thing to watch.
# MAGIC
# MAGIC ## Why one global clustering, not per (make, model)
# MAGIC
# MAGIC Clustering within each `(make, model)` would fit a **different model per group**, and
# MAGIC the real and placebo arms do not have identical group-size distributions — the placebo
# MAGIC was volume-matched at *series* level, not at `(make, model)` level. Different models
# MAGIC of differing quality across arms is precisely the asymmetry that manufactured the
# MAGIC discarded 160× artefact (I-027). One global model treats both arms identically by
# MAGIC construction, and defect modes genuinely cross models anyway (a supplier's part ships
# MAGIC in many vehicles).
# MAGIC
# MAGIC ## PCA before HDBSCAN
# MAGIC
# MAGIC HDBSCAN degrades in high dimensions — Euclidean distances concentrate and density
# MAGIC becomes meaningless. 1024 → 50 dims first. PCA is fitted on a sample and applied in
# MAGIC batches so the driver never holds 205,219 × 1024 floats at once.

# COMMAND ----------

import numpy as np
from pyspark.sql import functions as F
from sklearn.cluster import HDBSCAN
from sklearn.decomposition import PCA

CATALOG, SCHEMA = "bootcamp_students", "fleetguard"
spark.sql(f"USE {CATALOG}.{SCHEMA}")

PCA_DIMS = 50
PCA_FIT_SAMPLE = 50_000
MIN_CLUSTER_SIZE = 25  # a defect mode smaller than this cannot trip MIN_COUNT=5 monthly
MIN_SAMPLES = 5
SEED = 42

# COMMAND ----------

pdf = (
    spark.table("gold_backtest_embedding")
    .filter(F.col("embedding").isNotNull())
    .select("complaint_id", "embedding")
    .toPandas()
)
print(f"loaded {len(pdf):,} embeddings")

X = np.vstack(pdf["embedding"].to_numpy()).astype(np.float32)
print(f"matrix: {X.shape}  ({X.nbytes / 1e6:.0f} MB)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Reduce
# MAGIC
# MAGIC Explained variance is printed because it is the check that 50 dims is not throwing the
# MAGIC signal away. If it is very low, the clustering below is operating on noise and any
# MAGIC lead-time result would be meaningless.

# COMMAND ----------

rng = np.random.default_rng(SEED)
sample_idx = rng.choice(len(X), size=min(PCA_FIT_SAMPLE, len(X)), replace=False)

pca = PCA(n_components=PCA_DIMS, random_state=SEED)
pca.fit(X[sample_idx])
evr = float(pca.explained_variance_ratio_.sum())
print(f"PCA {X.shape[1]} -> {PCA_DIMS} dims, explained variance {evr:.1%}")

Xr = np.vstack([pca.transform(X[i : i + 20_000]) for i in range(0, len(X), 20_000)])
print(f"reduced: {Xr.shape}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Cluster
# MAGIC
# MAGIC `min_cluster_size = 25`: a defect mode with fewer complaints than that across the whole
# MAGIC 24-month corpus cannot produce a month with >= `MIN_COUNT = 5` *and* a 12-month
# MAGIC baseline, so smaller clusters could never fire in the detector regardless.

# COMMAND ----------

clusterer = HDBSCAN(
    min_cluster_size=MIN_CLUSTER_SIZE,
    min_samples=MIN_SAMPLES,
    metric="euclidean",
    cluster_selection_method="eom",
)
labels = clusterer.fit_predict(Xr)

n_clusters = int((np.unique(labels) >= 0).sum())
n_noise = int((labels == -1).sum())
print(f"clusters      : {n_clusters:,}")
print(f"noise (-1)    : {n_noise:,}  ({100.0 * n_noise / len(labels):.1f}%)")
print(f"clustered     : {len(labels) - n_noise:,}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Persist assignments
# MAGIC
# MAGIC Noise (`-1`) is kept rather than dropped. Dropping it would shrink the two arms by
# MAGIC different amounts and quietly change the population the backtest runs on — the v3
# MAGIC detector simply never groups on it.

# COMMAND ----------

pdf_out = pdf[["complaint_id"]].copy()
pdf_out["cluster_label"] = labels.astype(int)

(
    spark.createDataFrame(pdf_out)
    .write.mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable("gold_backtest_cluster")
)
spark.sql("""
ALTER TABLE gold_backtest_cluster SET TBLPROPERTIES (
  'comment' = 'HDBSCAN semantic cluster per complaint. cluster_label = -1 is HDBSCAN noise, retained deliberately so the backtest population is unchanged. One global model over PCA-reduced gte-large-en embeddings, so both backtest arms are treated identically.'
)""")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Diagnostics that decide whether v3 is worth running
# MAGIC
# MAGIC Three ways this could be broken, all invisible in a lead-time number:
# MAGIC
# MAGIC 1. **Noise rate too high** — the semantic arm would detect on a small biased subset.
# MAGIC 2. **Arm asymmetry** — if one arm is clustered far better than the other, any lift is
# MAGIC    an artefact of the clustering, not of the detector.
# MAGIC 3. **Fragmentation** — if clusters are much finer than components, series fall below
# MAGIC    `MIN_COUNT` and detection drops for a mechanical reason, not a semantic one.

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE TEMP VIEW cluster_arm AS
SELECT DISTINCT k.arm, e.complaint_id, c.cluster_label, e.make, e.model, e.comp_top
FROM gold_backtest_embedding e
JOIN gold_backtest_cluster c USING (complaint_id)
JOIN gold_backtest_scope k
  ON k.make = e.make AND k.model = e.model AND k.comp_top = e.comp_top
""")

print("--- noise rate by arm (asymmetry check) ---")
display(
    spark.sql("""
    SELECT arm, COUNT(*) AS complaints,
           SUM(CASE WHEN cluster_label = -1 THEN 1 ELSE 0 END) AS noise,
           ROUND(100.0 * AVG(CASE WHEN cluster_label = -1 THEN 1 ELSE 0 END), 1) AS noise_pct,
           COUNT(DISTINCT cluster_label) AS distinct_clusters
    FROM cluster_arm GROUP BY arm ORDER BY arm
""")
)

# COMMAND ----------

print("--- fragmentation: series per grouping key ---")
display(
    spark.sql("""
    SELECT 'v2  (make, model, comp_top)' AS grouping,
           COUNT(*) AS series,
           ROUND(AVG(n), 1) AS mean_complaints_per_series
    FROM (SELECT make, model, comp_top, COUNT(*) AS n FROM cluster_arm GROUP BY 1,2,3)
    UNION ALL
    SELECT 'v3  (make, model, cluster)', COUNT(*), ROUND(AVG(n), 1)
    FROM (SELECT make, model, cluster_label, COUNT(*) AS n
          FROM cluster_arm WHERE cluster_label <> -1 GROUP BY 1,2,3)
""")
)

# COMMAND ----------

print("--- largest clusters, with an exemplar narrative ---")
display(
    spark.sql("""
    WITH sized AS (
      SELECT cluster_label, COUNT(*) AS n FROM gold_backtest_cluster
      WHERE cluster_label <> -1 GROUP BY cluster_label ORDER BY n DESC LIMIT 12
    )
    SELECT s.cluster_label, s.n AS complaints,
           MIN(c.comp_top) AS example_component,
           SUBSTRING(MIN(c.narrative), 1, 150) AS exemplar
    FROM sized s
    JOIN gold_backtest_cluster g ON g.cluster_label = s.cluster_label
    JOIN gold_backtest_complaint c ON c.complaint_id = g.complaint_id
    GROUP BY s.cluster_label, s.n ORDER BY s.n DESC
""")
)
