# Databricks notebook source
# MAGIC %md
# MAGIC # FleetGuard — Phase 2: synthetic fleet registry
# MAGIC
# MAGIC 20,000 vehicles across 60 depots. The roster is the **only** synthetic asset in the
# MAGIC project (§3 provenance table), so it has to survive the obvious challenge: *"are
# MAGIC these real vehicles or did you make up plausible-looking strings?"*
# MAGIC
# MAGIC **Method.** VIN prefixes (positions 1–11: WMI + VDS + check digit + model year +
# MAGIC plant) are sampled from *real* complaint VINs in `silver_complaint`, then a 6-digit
# MAGIC serial is appended and the check digit at position 9 is **recomputed** so the full
# MAGIC 17-character VIN is structurally valid.
# MAGIC
# MAGIC **vPIC is the source of truth for make/model/year — not the complaint record.**
# MAGIC Complaint VINs are owner-entered and dirty: observed values include `!FTEW1EG2GK`
# MAGIC (invalid character), `11C6-RR6FG2` (malformed), and `1GDJG31V361` labelled RAM 2500
# MAGIC when the WMI is GMC. Every prefix is therefore validated against live vPIC and the
# MAGIC returned attributes are what get stored. Nothing about make, model or year is
# MAGIC asserted by this notebook.
# MAGIC
# MAGIC **On API volume.** §3 commits to not using vPIC for bulk lookups. Decoding 20,000
# MAGIC VINs individually would violate that. Because positions 12–17 are a serial that does
# MAGIC not affect decoding, only the ~400 distinct *prefixes* need validation (8 batch
# MAGIC requests), plus a verification sample of the generated 17-character VINs (10 more).

# COMMAND ----------

import json
import random
import urllib.parse
import urllib.request

CATALOG, SCHEMA = "bootcamp_students", "fleetguard"
spark.sql(f"USE {CATALOG}.{SCHEMA}")

N_VEHICLES = 20_000
N_DEPOTS = 60
SEED = 20260831
random.seed(SEED)

VPIC_BATCH = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVINValuesBatch/"

# COMMAND ----------

# MAGIC %md
# MAGIC ## VIN check digit
# MAGIC
# MAGIC Position 9 is a checksum over all 17 characters (weighted sum mod 11). Appending a
# MAGIC serial to a real prefix invalidates the original check digit, so it is recomputed.

# COMMAND ----------

_TRANS = {
    **{str(d): d for d in range(10)},
    "A": 1,
    "B": 2,
    "C": 3,
    "D": 4,
    "E": 5,
    "F": 6,
    "G": 7,
    "H": 8,
    "J": 1,
    "K": 2,
    "L": 3,
    "M": 4,
    "N": 5,
    "P": 7,
    "R": 9,
    "S": 2,
    "T": 3,
    "U": 4,
    "V": 5,
    "W": 6,
    "X": 7,
    "Y": 8,
    "Z": 9,
}
_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]


def with_check_digit(vin17: str) -> str:
    """Return vin17 with position 9 replaced by the correct check digit."""
    total = sum(_TRANS[c] * w for c, w in zip(vin17, _WEIGHTS, strict=True))
    rem = total % 11
    return vin17[:8] + ("X" if rem == 10 else str(rem)) + vin17[9:]


assert with_check_digit("1FTEW1C4" + "0" + "KF000001")[8] in "0123456789X"

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Candidate prefixes from real complaint VINs
# MAGIC
# MAGIC Filtered to the valid VIN alphabet (no I, O, Q) — which alone removes the malformed
# MAGIC entries above — and to commercial-fleet-appropriate vehicles.

# COMMAND ----------

# Stratified by segment. A single global top-N ranking is wrong here: pickups dominate
# complaint volume and crowded vans and heavy trucks out entirely, producing a "last-mile
# delivery fleet" containing no vans. Each segment gets its own quota of candidates.
SEGMENT_SQL = {
    "VAN": """
        (make = 'FORD'          AND model RLIKE 'TRANSIT') OR
        (make = 'RAM'           AND model RLIKE 'PROMASTER') OR
        (make = 'CHEVROLET'     AND model RLIKE 'EXPRESS') OR
        (make = 'GMC'           AND model RLIKE 'SAVANA') OR
        (make RLIKE 'MERCEDES'  AND model RLIKE 'SPRINTER') OR
        (make = 'NISSAN'        AND model RLIKE '^NV')
    """,
    "PICKUP": """
        (make = 'FORD'      AND model RLIKE '^(F-150|F-250|F-350)') OR
        (make = 'RAM'       AND model RLIKE '^(1500|2500|3500)') OR
        (make = 'CHEVROLET' AND model RLIKE '^(SILVERADO|COLORADO)') OR
        (make = 'GMC'       AND model RLIKE '^(SIERRA|CANYON)') OR
        (make = 'TOYOTA'    AND model RLIKE '^(TACOMA|TUNDRA)')
    """,
    "HEAVY": """
        make RLIKE 'FREIGHTLINER|PETERBILT|KENWORTH|INTERNATIONAL|MACK|WESTERN STAR|HINO|ISUZU'
        OR (make = 'VOLVO' AND model RLIKE '^V')
    """,
}
SEGMENT_QUOTA = {"VAN": 260, "PICKUP": 220, "HEAVY": 220}

candidates = []
for seg, pred in SEGMENT_SQL.items():
    rows = spark.sql(f"""
        SELECT vin_partial AS prefix, COUNT(*) AS complaint_weight, '{seg}' AS intent
        FROM silver_complaint
        WHERE vin_partial RLIKE '^[A-HJ-NPR-Z0-9]{{11}}$'
          AND model_year BETWEEN 2015 AND 2024
          AND product_type = 'V'
          AND ({pred})
        GROUP BY 1
        ORDER BY complaint_weight DESC
        LIMIT {SEGMENT_QUOTA[seg]}
    """).collect()
    candidates.extend(rows)
    print(f"  {seg:<8} candidate prefixes: {len(rows)}")

print(f"candidate prefixes: {len(candidates):,}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Validate every prefix against live vPIC
# MAGIC
# MAGIC A prefix survives only if vPIC returns a make, a model and a model year. Whatever it
# MAGIC returns is what the roster stores.

# COMMAND ----------


def vpic_batch(vins):
    """Decode up to 50 VINs in one request."""
    payload = urllib.parse.urlencode({"format": "json", "data": ";".join(vins)}).encode()
    req = urllib.request.Request(VPIC_BATCH, data=payload)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)["Results"]


validated = []
probe = {with_check_digit(c["prefix"] + "000001"): c for c in candidates}
probe_vins = list(probe)

for i in range(0, len(probe_vins), 50):
    for res in vpic_batch(probe_vins[i : i + 50]):
        src = probe.get(res.get("VIN", ""))
        if src is None:
            continue
        make, model, year = res.get("Make"), res.get("Model"), res.get("ModelYear")
        if make and model and year and str(year).isdigit():
            body = res.get("BodyClass") or ""
            gvwr = res.get("GVWR") or ""
            # vPIC decides the segment, not the complaint's make string. VOLVO covers both
            # Class 8 tractors and passenger cars, so the source label cannot be trusted.
            if "Class 7" in gvwr or "Class 8" in gvwr or "Truck-Tractor" in body:
                seg = "HEAVY"
            elif "Van" in body:
                seg = "VAN"
            elif "Pickup" in body:
                seg = "PICKUP"
            else:
                seg = "OTHER"
            validated.append(
                {
                    "segment": seg,
                    "prefix": src["prefix"],
                    "weight": int(src["complaint_weight"]),
                    "make": make.upper(),
                    "model": model.upper(),
                    "model_year": int(year),
                    "body_class": res.get("BodyClass") or None,
                    "gvwr_class": res.get("GVWR") or None,
                    "plant_city": res.get("PlantCity") or None,
                }
            )

print(
    f"prefixes decoding cleanly: {len(validated):,} of {len(candidates):,} "
    f"({len(validated) / max(len(candidates), 1):.1%})"
)
print("distinct make/model combos:", len({(v["make"], v["model"]) for v in validated}))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Depots

# COMMAND ----------

REGIONS = {
    "NORTHEAST": [
        ("Boston", "MA"),
        ("Newark", "NJ"),
        ("Philadelphia", "PA"),
        ("Hartford", "CT"),
        ("Buffalo", "NY"),
        ("Pittsburgh", "PA"),
    ],
    "SOUTHEAST": [
        ("Atlanta", "GA"),
        ("Charlotte", "NC"),
        ("Orlando", "FL"),
        ("Nashville", "TN"),
        ("Birmingham", "AL"),
        ("Richmond", "VA"),
    ],
    "MIDWEST": [
        ("Chicago", "IL"),
        ("Detroit", "MI"),
        ("Columbus", "OH"),
        ("Indianapolis", "IN"),
        ("Milwaukee", "WI"),
        ("Kansas City", "MO"),
    ],
    "SOUTHWEST": [
        ("Dallas", "TX"),
        ("Houston", "TX"),
        ("Phoenix", "AZ"),
        ("Albuquerque", "NM"),
        ("Oklahoma City", "OK"),
        ("San Antonio", "TX"),
    ],
    "WEST": [
        ("Los Angeles", "CA"),
        ("Oakland", "CA"),
        ("Seattle", "WA"),
        ("Portland", "OR"),
        ("Denver", "CO"),
        ("Las Vegas", "NV"),
    ],
}

depots, n = [], 0
while len(depots) < N_DEPOTS:
    for region, cities in REGIONS.items():
        if len(depots) >= N_DEPOTS:
            break
        city, state = cities[n % len(cities)]
        idx = len(depots) + 1
        depots.append(
            {
                "depot_id": f"DEP-{idx:03d}",
                "depot_name": f"{city} {'North' if n % 3 == 0 else 'South' if n % 3 == 1 else 'Central'} Depot",
                "region": region,
                "city": city,
                "state": state,
                "manager_principal": f"depot{idx:03d}@fleetguard.example",
            }
        )
    n += 1

depot_df = spark.createDataFrame(depots)
depot_df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("gold_fleet_depot")
spark.sql("""
COMMENT ON TABLE gold_fleet_depot IS
'Synthetic depot network, 60 sites across 5 US regions. The fleet roster is the only synthetic asset in the project.'
""")
print(f"depots: {depot_df.count()}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Vehicles
# MAGIC
# MAGIC Prefixes are sampled proportional to their real complaint frequency, so the fleet's
# MAGIC composition mirrors what actually exists on US roads rather than being uniform.
# MAGIC Serial numbers are unique per VIN.

# COMMAND ----------

weights = [v["weight"] for v in validated]
statuses = ["ACTIVE"] * 92 + ["IN_SERVICE"] * 5 + ["OUT_OF_SERVICE"] * 3

# Target mix for a mixed commercial fleet: last-mile delivery vans, utility/service
# pickups, and a Class 7/8 tractor tail. Within each segment, prefixes are still drawn
# proportional to real complaint frequency.
TARGET_MIX = {"VAN": 0.40, "PICKUP": 0.45, "HEAVY": 0.15}

by_segment = {}
for v in validated:
    by_segment.setdefault(v["segment"], []).append(v)
for seg, items in sorted(by_segment.items()):
    print(f"  validated {seg:<8}: {len(items)} prefixes")

seen, vehicles = set(), []
serial = 100_000
for seg, share in TARGET_MIX.items():
    pool = by_segment.get(seg) or []
    if not pool:
        print(f"  WARNING: no validated prefixes for segment {seg}; skipping")
        continue
    weights = [p["weight"] for p in pool]
    target = int(N_VEHICLES * share)
    made = 0
    while made < target:
        src = random.choices(pool, weights=weights, k=1)[0]
        serial += random.randint(1, 9)
        vin = with_check_digit(f"{src['prefix']}{serial:06d}")
        if vin in seen:
            continue
        seen.add(vin)
        depot = depots[random.randrange(N_DEPOTS)]
        age_years = max(0, 2026 - src["model_year"])
        annual = 26_000 if seg == "HEAVY" else 19_000 if seg == "VAN" else 15_000
        vehicles.append(
            {
                "vin": vin,
                "depot_id": depot["depot_id"],
                "segment": seg,
                "make": src["make"],
                "model": src["model"],
                "model_year": src["model_year"],
                "body_class": src["body_class"],
                "gvwr_class": src["gvwr_class"],
                "plant_city": src["plant_city"],
                "mileage": max(500, int(random.gauss(age_years * annual + 12_000, 9_000))),
                "status": random.choice(statuses),
                "vin_prefix": src["prefix"],
            }
        )
        made += 1

# top up any shortfall from the largest available pool so the roster hits exactly N
while len(vehicles) < N_VEHICLES:
    pool = max(by_segment.values(), key=len)
    src = random.choice(pool)
    serial += random.randint(1, 9)
    vin = with_check_digit(f"{src['prefix']}{serial:06d}")
    if vin in seen:
        continue
    seen.add(vin)
    depot = depots[random.randrange(N_DEPOTS)]
    vehicles.append(
        {
            "vin": vin,
            "depot_id": depot["depot_id"],
            "segment": src["segment"],
            "make": src["make"],
            "model": src["model"],
            "model_year": src["model_year"],
            "body_class": src["body_class"],
            "gvwr_class": src["gvwr_class"],
            "plant_city": src["plant_city"],
            "mileage": max(500, int(random.gauss(60_000, 20_000))),
            "status": random.choice(statuses),
            "vin_prefix": src["prefix"],
        }
    )

print(f"generated {len(vehicles):,} vehicles")

vehicle_df = spark.createDataFrame(vehicles)
(
    vehicle_df.write.mode("overwrite")
    .option("overwriteSchema", "true")
    .clusterBy("make", "model", "model_year")
    .saveAsTable("gold_fleet_vehicle")
)
spark.sql("""
COMMENT ON TABLE gold_fleet_vehicle IS
'Synthetic 20,000-vehicle roster. VIN prefixes sampled from real complaint VINs, check digits recomputed, make/model/year sourced from live vPIC — never asserted locally.'
""")
print(f"vehicles: {vehicle_df.count():,}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Verification — decode a random sample of the *generated* VINs
# MAGIC
# MAGIC The prefixes were validated; this confirms the full 17-character VINs still decode
# MAGIC to the same make, model and year after serial + check-digit construction.

# COMMAND ----------

sample = random.sample(vehicles, 500)
by_vin = {v["vin"]: v for v in sample}
ok = mismatch = undecoded = 0
bad_examples = []

vins = list(by_vin)
for i in range(0, len(vins), 50):
    for res in vpic_batch(vins[i : i + 50]):
        exp = by_vin.get(res.get("VIN", ""))
        if exp is None:
            continue
        got_make, got_model, got_year = res.get("Make"), res.get("Model"), res.get("ModelYear")
        if not (got_make and got_model and got_year):
            undecoded += 1
            bad_examples.append((res.get("VIN"), "no decode"))
        elif (
            got_make.upper() == exp["make"]
            and got_model.upper() == exp["model"]
            and int(got_year) == exp["model_year"]
        ):
            ok += 1
        else:
            mismatch += 1
            bad_examples.append((res.get("VIN"), f"{got_make}/{got_model}/{got_year}"))

print(f"sample verified : {len(sample)}")
print(f"  exact match   : {ok} ({ok / len(sample):.1%})")
print(f"  mismatch      : {mismatch}")
print(f"  failed decode : {undecoded}")
for v, why in bad_examples[:10]:
    print("   ", v, "->", why)

# COMMAND ----------

display(
    spark.sql("""
SELECT make, model, model_year, COUNT(*) AS vehicles
FROM gold_fleet_vehicle GROUP BY 1,2,3 ORDER BY vehicles DESC LIMIT 20
""")
)
