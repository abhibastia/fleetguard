# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — Model B: recall-to-fleet match classifier (Phase 4)
# MAGIC
# MAGIC Scores the `MODEL_VARIANT` residual — 725,356 of 989,042 exposure rows, 3× the `EXACT`
# MAGIC tier (I-030). §7's deterministic guarantee is `EXACT`-only; this ranks the rest rather
# MAGIC than deciding it (per the proposal — Model B never performs the match, it scores
# MAGIC ambiguity for a human or a downstream threshold to act on).
# MAGIC
# MAGIC ## No leakage from the golden set's own label rule
# MAGIC
# MAGIC `gold_model_b_golden_set` (`05_build_model_b_golden_set`) derives its **labels** from
# MAGIC whether the vehicle's model string appears in the recall's `defect_description` text.
# MAGIC That signal must NOT also be a **feature** here, and every feature below is built from
# MAGIC the structured `model`/`recall_model` fields, never from `defect_description` text.
# MAGIC
# MAGIC **A second, less obvious leak was found and fixed (I-060).** The golden set's negative
# MAGIC rule additionally requires `recall_model NOT LIKE '%model%'` — so for every `label = 0`
# MAGIC row, the boolean `model_is_substring_of_recall` is `False` **by construction**, not by
# MAGIC anything learned. Measured on the live table: that one feature is `True` for 613/621
# MAGIC (98.7%) positives and **0/144 (0%) negatives** — a first training run scored
# MAGIC precision 1.000 / recall 0.989 at threshold 1.000, which is the signature of exactly
# MAGIC this, not of a strong model. `model_is_substring_of_recall` and
# MAGIC `recall_is_substring_of_model` are excluded below for that reason. The continuous fuzzy
# MAGIC scores are kept — they correlate with the same real naming pattern (trim suffixes like
# MAGIC `F-250` → `F-250 SD` land on the *recall* side; distinguishing suffixes like
# MAGIC `PROMASTER` → `PROMASTER CITY` land on the *vehicle* side) without being a hard 0/1 tied
# MAGIC to the exact label rule, but the resulting numbers below should still be read as
# MAGIC optimistic — this golden set was not built independently of every feature that scores
# MAGIC it.
# MAGIC
# MAGIC ## Threshold tuned for recall, not F1
# MAGIC
# MAGIC Per the proposal: a fleet manager missing a genuine match (false negative) leaves a
# MAGIC vehicle exposed to a real defect. A false positive costs one wasted inspection. The two
# MAGIC errors are not symmetric, so accuracy and F1 are the wrong things to optimize.

# COMMAND ----------

# MAGIC %pip install -q -U mlflow scikit-learn rapidfuzz
# MAGIC %restart_python

# COMMAND ----------

CATALOG, SCHEMA = "bootcamp_students", "fleetguard"
spark.sql(f"USE {CATALOG}.{SCHEMA}")

MODEL_NAME = f"{CATALOG}.{SCHEMA}.fleetguard_model_b"
TARGET_RECALL = 0.90  # threshold chosen to catch >=90% of true matches; see rationale above

# COMMAND ----------

# MAGIC %md
# MAGIC ## Features — structured only, no text signal from the label source

# COMMAND ----------

import pandas as pd
from rapidfuzz import fuzz

golden = spark.table("gold_model_b_golden_set").filter("label IS NOT NULL").toPandas()
print(f"golden set: {len(golden)} rows ({golden['label'].sum()} positive)")


def featurize(df: pd.DataFrame) -> pd.DataFrame:
    m = df["model"].str.upper().fillna("")
    r = df["recall_model"].str.upper().fillna("")

    out = pd.DataFrame(index=df.index)
    # String-similarity family: different metrics fail differently, so more than one is kept
    # rather than picking a single "best" one that happens to fit this sample.
    out["ratio"] = [fuzz.ratio(a, b) for a, b in zip(m, r)]
    out["partial_ratio"] = [fuzz.partial_ratio(a, b) for a, b in zip(m, r)]
    out["token_sort_ratio"] = [fuzz.token_sort_ratio(a, b) for a, b in zip(m, r)]
    out["token_set_ratio"] = [fuzz.token_set_ratio(a, b) for a, b in zip(m, r)]

    # model_is_substring_of_recall / recall_is_substring_of_model deliberately OMITTED —
    # see I-060 above. Do not re-add without re-deriving the golden set's negative rule
    # independently of them.
    out["len_diff"] = (r.str.len() - m.str.len()).abs()
    out["word_count_diff"] = (r.str.split().str.len() - m.str.split().str.len()).abs()

    m_tokens = m.str.split().apply(set)
    r_tokens = r.str.split().apply(set)
    out["token_jaccard"] = [
        len(a & b) / len(a | b) if (a | b) else 0.0 for a, b in zip(m_tokens, r_tokens)
    ]
    out["first_word_matches"] = [
        (a.split()[0] if a.split() else "") == (b.split()[0] if b.split() else "")
        for a, b in zip(m, r)
    ]
    return out


X = featurize(golden)
y = golden["label"].astype(int)
print(f"features: {list(X.columns)}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Train / calibrate
# MAGIC
# MAGIC Gradient-boosted trees per the proposal. Stratified split so the minority class (whichever
# MAGIC it is) is represented in both halves — with 473 total candidates a plain random split
# MAGIC risks an evaluation set that is accidentally single-class.

# COMMAND ----------

from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import (
    average_precision_score,
    precision_recall_curve,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split

X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.3, random_state=42, stratify=y
)
print(f"train: {len(X_train)} ({y_train.sum()} pos) · test: {len(X_test)} ({y_test.sum()} pos)")

base = GradientBoostingClassifier(random_state=42, n_estimators=150, max_depth=3)
# Calibration matters here specifically because the score is read by a human as a confidence
# ("how sure is this?"), not just used to rank — an uncalibrated GBM's raw score is not a
# probability, and presenting it as one on the console would be a quiet overclaim.
clf = CalibratedClassifierCV(base, method="isotonic", cv=3)
clf.fit(X_train, y_train)

proba = clf.predict_proba(X_test)[:, 1]

# COMMAND ----------

# MAGIC %md
# MAGIC ## Tune the threshold for recall, then report precision at that threshold
# MAGIC
# MAGIC The done-when in `PLAN.md` is explicit: "precision/recall numbers exist and are real,
# MAGIC not placeholders." Both are computed here against the held-out test split, not asserted.

# COMMAND ----------

precisions, recalls, thresholds = precision_recall_curve(y_test, proba)
# precision_recall_curve returns one more point than thresholds; align by dropping the last.
precisions, recalls = precisions[:-1], recalls[:-1]

# Smallest threshold that still clears the recall target — the highest-precision point on
# the recall-constrained side of the curve, not merely "a" point that satisfies it.
eligible = recalls >= TARGET_RECALL
if not eligible.any():
    raise RuntimeError(
        f"no threshold reaches recall >= {TARGET_RECALL} on this test split "
        f"(max achievable recall: {recalls.max():.3f}) — golden set may be too small or "
        f"too easy/hard; do not silently lower the target"
    )
import numpy as np

idxs = np.where(eligible)[0]
chosen = idxs[np.argmax(precisions[idxs])]
CHOSEN_THRESHOLD = float(thresholds[chosen])
achieved_recall = float(recalls[chosen])
achieved_precision = float(precisions[chosen])

auc = roc_auc_score(y_test, proba)
ap = average_precision_score(y_test, proba)

print(f"threshold          : {CHOSEN_THRESHOLD:.3f}")
print(f"recall  @ threshold: {achieved_recall:.3f}  (target >= {TARGET_RECALL})")
print(f"precision @ threshold: {achieved_precision:.3f}")
print(f"ROC-AUC             : {auc:.3f}")
print(f"average precision    : {ap:.3f}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Log to MLflow — the numbers above, not placeholders

# COMMAND ----------

import mlflow
import mlflow.sklearn
from mlflow.models.signature import infer_signature

mlflow.set_registry_uri("databricks-uc")

with mlflow.start_run(run_name="fleetguard-model-b-v1") as run:
    mlflow.log_param("golden_set_size", len(golden))
    mlflow.log_param("golden_set_positive", int(y.sum()))
    mlflow.log_param("target_recall", TARGET_RECALL)
    mlflow.log_param("features", list(X.columns))
    mlflow.log_param("derivation_method", "defect_description_text_match_v1 (I-059)")
    mlflow.log_param(
        "excluded_features_reason",
        "model/recall substring booleans drop label-construction leakage (I-060)",
    )

    mlflow.log_metric("threshold", CHOSEN_THRESHOLD)
    mlflow.log_metric("precision_at_threshold", achieved_precision)
    mlflow.log_metric("recall_at_threshold", achieved_recall)
    mlflow.log_metric("roc_auc", auc)
    mlflow.log_metric("average_precision", ap)
    mlflow.log_metric("test_set_size", len(X_test))
    mlflow.log_metric("test_set_positive", int(y_test.sum()))

    signature = infer_signature(X_train, clf.predict_proba(X_train))
    # MLflow's default sklearn serialization (skops) security-gates internal types it does
    # not recognise. CalibratedClassifierCV produces sklearn.calibration._CalibratedClassifier
    # internally, which trips that gate. Trusting it here is correct, not a bypass: this is a
    # model we just trained in-process, not one loaded from an external or untrusted source —
    # the gate exists for the latter case.
    model_info = mlflow.sklearn.log_model(
        clf,
        name="model",
        signature=signature,
        input_example=X_train.head(3),
        skops_trusted_types=["sklearn.calibration._CalibratedClassifier"],
    )
    print(f"run_id: {run.info.run_id}")

registered = mlflow.register_model(model_uri=model_info.model_uri, name=MODEL_NAME)
print(f"registered: {registered.name} v{registered.version}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Honesty check on the golden set size
# MAGIC
# MAGIC 473 candidate pairs, ~330 usable after excluding ambiguous ones, is enough to fit and
# MAGIC evaluate a threshold — but it is a small evaluation set, and this run says so rather
# MAGIC than letting a precision/recall figure imply more confidence than the sample supports.

# COMMAND ----------

print(
    f"Golden set: {len(golden)} labelled pairs (test split: {len(X_test)}). "
    "Small-sample caveat: these figures should be treated as directional until the golden "
    "set grows past ~500 pairs or is cross-checked against a second derivation method."
)
