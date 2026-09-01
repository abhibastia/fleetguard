# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — Phase 9: tune HDBSCAN on a sample before paying 85 minutes again
# MAGIC
# MAGIC The first full clustering run took **85 minutes** and returned **85.4% noise** — only
# MAGIC 29,909 of 205,219 complaints clustered, into 286 clusters. The semantic arm would have
# MAGIC been detecting on a 15% subset, so the diagnostics did their job and stopped the
# MAGIC lead-time number from ever being computed on it.
# MAGIC
# MAGIC Tuning at 85 minutes per attempt is not viable. This sweeps parameters on a **sample**,
# MAGIC where a configuration costs seconds, then the full run happens once with the winner.
# MAGIC
# MAGIC ## The prime suspect: unnormalised vectors under Euclidean distance
# MAGIC
# MAGIC The first run clustered **raw** `gte-large-en` vectors with `metric="euclidean"`. For
# MAGIC text embeddings that is wrong: similarity is conventionally **cosine**, and Euclidean
# MAGIC distance on unnormalised vectors conflates *magnitude* with *direction*. Embedding
# MAGIC magnitude tracks things like narrative length and verbosity, so two complaints
# MAGIC describing the same defect at different lengths land far apart, and density-based
# MAGIC clustering sees no density.
# MAGIC
# MAGIC L2-normalising first makes Euclidean distance a monotonic function of cosine distance
# MAGIC (`||a-b||² = 2 - 2·cos(a,b)` for unit vectors), which is the standard way to get cosine
# MAGIC behaviour from a Euclidean implementation.
# MAGIC
# MAGIC Also swept: **PCA dimensionality** (density estimation degrades as dimensions rise) and
# MAGIC `min_samples` (how conservative the noise labelling is).

# COMMAND ----------

import time

import numpy as np
from pyspark.sql import functions as F
from sklearn.cluster import HDBSCAN
from sklearn.decomposition import PCA
from sklearn.preprocessing import normalize

CATALOG, SCHEMA = "bootcamp_students", "fleetguard"
spark.sql(f"USE {CATALOG}.{SCHEMA}")

SAMPLE_N = 30_000
SEED = 42

# COMMAND ----------

pdf = (
    spark.table("gold_backtest_embedding")
    .filter(F.col("embedding").isNotNull())
    .select("complaint_id", "embedding")
    .toPandas()
)
X_all = np.vstack(pdf["embedding"].to_numpy()).astype(np.float32)

rng = np.random.default_rng(SEED)
idx = rng.choice(len(X_all), size=min(SAMPLE_N, len(X_all)), replace=False)
X = X_all[idx]
print(f"sample: {X.shape}")

# Magnitude spread is the thing that breaks Euclidean clustering — quantify it rather than
# asserting it.
norms = np.linalg.norm(X, axis=1)
print(
    f"vector norms: min {norms.min():.2f}  median {np.median(norms):.2f}  "
    f"max {norms.max():.2f}  ratio {norms.max() / norms.min():.2f}x"
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Sweep
# MAGIC
# MAGIC Each row is one configuration. The target is a **low noise fraction with a sensible
# MAGIC cluster count** — thousands of tiny clusters would fragment the monthly series below
# MAGIC `MIN_COUNT = 5` and defeat the detector just as surely as 85% noise does.

# COMMAND ----------

CONFIGS = [
    # (normalise, pca_dims, min_cluster_size, min_samples)
    (False, 50, 25, 5),  # the failing baseline, for comparison
    (True, 50, 25, 5),  # normalisation alone
    (True, 30, 25, 5),
    (True, 20, 25, 5),
    (True, 20, 25, 3),
    (True, 20, 50, 5),
    (True, 10, 25, 5),
    (True, 10, 50, 10),
]

rows = []
for norm, dims, mcs, ms in CONFIGS:
    t0 = time.time()
    Xw = normalize(X) if norm else X
    pca = PCA(n_components=dims, random_state=SEED)
    Xr = pca.fit_transform(Xw)
    evr = float(pca.explained_variance_ratio_.sum())

    labels = HDBSCAN(
        min_cluster_size=mcs, min_samples=ms, metric="euclidean", cluster_selection_method="eom"
    ).fit_predict(Xr)

    n_noise = int((labels == -1).sum())
    n_clusters = int((np.unique(labels) >= 0).sum())
    sizes = np.bincount(labels[labels >= 0]) if n_clusters else np.array([0])

    rows.append(
        {
            "normalised": bool(norm),
            "pca_dims": int(dims),
            "min_cluster_size": int(mcs),
            "min_samples": int(ms),
            "explained_var": round(evr, 3),
            "clusters": n_clusters,
            "noise_pct": round(100.0 * n_noise / len(labels), 1),
            "median_cluster_size": int(np.median(sizes)),
            "max_cluster_size": int(sizes.max()),
            "seconds": round(time.time() - t0, 1),
        }
    )
    r = rows[-1]
    print(
        f"  norm={norm!s:<5} dims={dims:<3} mcs={mcs:<3} ms={ms:<2} | "
        f"evr={evr:.2f} clusters={n_clusters:<5} noise={r['noise_pct']:>5.1f}% "
        f"med_size={r['median_cluster_size']:<5} ({r['seconds']}s)"
    )

# COMMAND ----------

sweep = spark.createDataFrame(rows)
(sweep.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("ops_hdbscan_sweep"))
display(sweep.orderBy("noise_pct"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Read this before picking a winner
# MAGIC
# MAGIC Lowest noise is **not** automatically best. A configuration that clusters everything
# MAGIC into a handful of enormous blobs has low noise and no discriminating power — the
# MAGIC monthly series would be as coarse as the component codes v2 already uses, and v3 would
# MAGIC measure nothing new. The useful region is low noise **and** cluster sizes in the tens
# MAGIC to low hundreds.
# MAGIC
# MAGIC These numbers come from a 30k sample; HDBSCAN's density estimates shift with sample
# MAGIC size, so the full 205,219-point run will not reproduce them exactly. Treat the sweep as
# MAGIC choosing a *region*, not as a prediction.

# COMMAND ----------

print("sweep complete — pick from ops_hdbscan_sweep, then run 06 with those parameters")
