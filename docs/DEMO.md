# FleetGuard — demo runbook

**Demo 25–30 Sept 2026.** Everything here was measured live on **2026-09-09**, not estimated.
Numbers carry their source so nothing gets quoted from memory.

> **The demo's job is to sell rigour, not a big number.** The headline result is a **1.44×**
> lift, which is modest and real. The argument is the *method*: a volume-matched control arm, a
> falsified hypothesis published as a negative, and a system that reports its own uncertainty.
> An unfalsifiable 10× would be a weaker claim, not a stronger one. Say that out loud — it is
> the whole case.

---

## 1. Pre-flight — start 15 minutes early

Three things are asleep and **two of them do not wake on their own.**

| # | Action | Time | Notes |
|---|---|---|---|
| 1 | `databricks serving-endpoints get agents_bootcamp_students-fleetguard-fleetguard_agent --profile abhi` | — | Check `state.ready`. If `NOT_READY` / `DEPLOYMENT_STOPPED`, do step 2 |
| 2 | **Restore the agent endpoint** — see below | **~3 min** (measured 184 s) | **A stopped endpoint does NOT wake on request.** It returns `400 The given endpoint is stopped` and the Assistant panel shows an error |
| 3 | `databricks apps start fleetguard-console --profile abhi` | **~2 min** (measured 117 s to `RUNNING`) | `apps start` returns after ~105 s but `app_status` is still `UNAVAILABLE`; poll until `RUNNING` |
| 4 | Ask the assistant one throwaway question | ~20 s | First answer after a restore is slower (measured 20 s vs 13 s warm) |
| 5 | `curl .../api/signals` → expect **200** | ~1 s | This route has broken before (I-091) and it is the proactive half of the story |

**Restoring the agent endpoint.** There is **no `start` or `resume` subcommand** — Model Serving
offers only scale-to-zero or delete. Re-apply the config, building the payload *from the live
entity* so `environment_vars` survive (dropping `MLFLOW_EXPERIMENT_ID` silently misfiles
tracing), and re-assert `scale_to_zero_enabled=True`, which `agents.deploy()` resets every time:

```python
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.serving import ServedEntityInput
EP = "agents_bootcamp_students-fleetguard-fleetguard_agent"
w = WorkspaceClient(profile="abhi")
e = w.serving_endpoints.get(EP).config.served_entities[0]
w.serving_endpoints.update_config(name=EP, served_entities=[ServedEntityInput(
    name=e.name, entity_name=e.entity_name, entity_version=str(e.entity_version),
    workload_size=e.workload_size, workload_type=e.workload_type,
    scale_to_zero_enabled=True, environment_vars=dict(e.environment_vars))])
```

**Confirm what is still unapproved.** The demo's live approval moment needs an unlaunched Park It
recall. Three campaigns are already launched (`17V629000`, `21V037000`, `17V621000`) — do **not**
use those. Check the queue for a `park_it` campaign with no service campaign against it.

**Console URL:** `https://fleetguard-console-1352785079224954.aws.databricksapps.com`

---

## 2. The narrative

Nine beats. The spine is **detect → scope → decide → dispatch → prove**, and every beat should
answer "why would a fleet operator care".

### Beat 1 · Evidence — open here, not at the end

Counter-intuitive, but it front-loads the honesty. **16.0% vs 11.1%, 1.44×, p ≈ 0.009.**

> "We detect roughly 1 in 6 defect ramps before NHTSA opens an investigation, at a median 197
> days of lead. The control arm catches 11.1%. That gap is small and it is real — z 2.62.
> I also tried the obvious improvement, semantic clustering, and it made detection *worse*. That
> negative is published on this page too."

The point: everything that follows sits on a measured, falsifiable claim with a control arm.

### Beat 2 · Emerging signals — the proactive half

"**2 affecting your fleet**" of 50. RAM 2500 service brakes: **1,256 fleet vehicles**, peak
z 5.62, 64 complaints.

Say the three-state distinction explicitly, because it is the honest core: **a signal is not an
investigation, and an investigation is not a recall.** Nothing here has been acted on by NHTSA.

Point out the **AGENT** badge on the two agent-opened rows — a human opened those through the
assistant, and the audit log names them.

### Beat 3 · Recall queue — consequence before volume

50 campaigns, ordered Park It first, then by vehicles exposed. Not sorted by count: a 25-vehicle
Park It outranks a 2,000-vehicle labelling recall, which is the operator's actual priority.

### Beat 4 · Campaign detail — the deterministic guarantee

Open `17V629000`. **25 vehicles across 22 depots, EXACT.**

> "EXACT means make, model, year and manufacture window all match deterministically. The system
> also reports MODEL_VARIANT matches separately and never blends them — a variant match on RAM
> PROMASTER pulls in 315 PROMASTER CITY vans, which is a different vehicle. §7's guarantee covers
> EXACT only, and the tier travels with every number."

### Beat 5 · The human gate — the strongest moment

Approve an unlaunched Park It campaign. One transaction: service campaign + one work order per
exposed vehicle + audit row. **205 work orders in 3 s**, measured.

> "The agent never launches. It proposes. This button is the only thing that dispatches work, and
> it is gated on an allowlist that workspace admin does not bypass."

### Beat 6 · Work orders — closing the loop

331 work orders: 144 completed, 84 in progress, 94 open, 9 cancelled. All assigned to a
technician **at the right depot** — the server rejects a cross-depot assignment, it is not UI
filtering. **44 are overdue across 28 depots.**

### Beat 7 · Cost and audit — why a fleet buys this

**$84,409.68** logged across 3 components. Deliberately **not** one blended $/vehicle figure: a
steering-rack inspection and a brake master-cylinder job are different money, and `costed_count`
vs `total_work_orders` is shown so partial coverage is visible rather than averaged away.

**723 audit rows**, filterable and CSV-exportable — every launch, status change, assignment and
cost entry, with the human who did it.

### Beat 8 · The assistant — agentic, and constrained

Ask: *"What emerging defect signals affect our fleet right now?"*

The agent volunteers the caveats unprompted — that these are not recalls, that the detector is
"a real edge, but not an oracle", that harm-share is descriptive only. That is the demo, not a
disclaimer to skip past.

Then: *"Open a defect signal for the RAM 2500 brake issue."* The agent returns an **action
envelope**; the *app* executes the write under **your** OBO token. The model has no database
path. `opened_by` is a real human.

### Beat 9 · The trip through the platform

The write just made lands in Postgres → Lakebase CDF → Unity Catalog. **CDF capture 7.1–15.6 s**;
**Postgres commit → gold fact 2.5–4.5 minutes** (measured 155 s and 269 s, n=2 — quote the range,
never one averaged number).

---

## 3. Numbers, with sources

| Claim | Value | Source |
|---|---|---|
| Detection, real arm | 124/777 = **16.0%**, median **197 d** | `gold_lead_time_summary` |
| Detection, placebo | 67/606 = **11.1%**, median 343 d | `gold_lead_time_summary` |
| Lift / significance | **1.44×**, z **2.62**, p **0.009** | recomputed from arm counts |
| Complaints corpus | **2,240,289** | `bronze_complaints` |
| Recall campaigns | 244,925 rows / **15,211** campaigns | `bronze_recalls` |
| Investigations | 154,367 rows / **5,344** distinct | `bronze_investigations` |
| Indexed chunks | **1,746,601** | `complaint_chunk_idx` |
| Fleet | **20,000** vehicles · **60** depots · 47 models | `gold_fleet_vehicle` |
| Exposure (EXACT) | **118,323** distinct (vin, campaign) | `fleetguard_vehicle_exposure` |
| `17V629000` | **25** vehicles / **22** depots, EXACT | verified 2026-09-09 |
| Ford F-250 in fleet | **2,116** | verified 2026-09-09 |
| Signals | **48** detected / 9 live / **2** fleet-relevant | `gold_emerging_signal` |
| Signals in console | **50** / 9 live / **4** fleet-relevant | Lakebase = 48 detector + 2 agent |
| RAM 2500 signal | **1,256** vehicles, z 5.62, 64 complaints | verified 2026-09-09 |
| Model B | precision **83.7%**, recall **96.3%**, AUC 0.925 | 765-pair golden set |
| Work orders / audit / cost | **331** / **723** / **$84,409.68** | verified 2026-09-09 |

**Why the console says 4 and the warehouse says 2.** `gold_emerging_signal` holds 48 detector
rows, 2 of them fleet-relevant. Lakebase adds the 2 agent-opened signals, giving 50 and 4. Both
are right; they count different things. This becomes 6 only after B3 rebuilds the gold table with
B1's tiered match.

---

## 4. Fragile moments

- **The agent endpoint stops and does not wake.** Highest-risk item; see pre-flight. Found dead
  during this dry run, having been believed merely scaled-to-zero.
- **Judges can approve.** All three are in `FLEETGUARD_APPROVERS` and hold `CAN_USE`. An approval
  writes up to ~200 work orders and replicates to **append-only** CDF that cannot be scrubbed.
  Fine if intended — budget the cleanup.
- **`CAN_USE` cannot start stopped compute**, so nobody can look at the app unless you start it.
- **Cold starts:** app ~2 min, agent restore ~3 min, first answer ~20 s.
- **If a route misbehaves,** check it directly — `/api/signals` returning 500 while twelve other
  routes return 200 is easy to miss from the UI alone (I-091).

---

## 5. What NOT to claim

- **Not** that a judge's sign-in is proven. Every verification ran under the owner's identity, and
  under a **programmatic token** rather than a browser for the App routes. All three judges hold
  Lakebase roles, so the known failure mode cannot hit them — but it is untested, not proven.
- **Not** that `match_basis` is populated. It is NULL for every row until B3 rebuilds; the console
  correctly shows no tier badge rather than a wrong one.
- **Not** the 118-day investigation→recall figure as a system result. That is regulatory latency,
  context only.
- **Not** that the semantic/embedding arm helps. It was built, measured, and **falsified** — it
  lowered detection to 11.2% and added 0.0 days. Present it as a published negative; it is one of
  the strongest things here.
- **Not** that determinism covers every match. **EXACT only.**
- **Not** that the audit trail spans months. It is genuine — real writes by a real identity —
  but seeded on 2026-09-09, so the timestamps cluster.

---

## 6. Shutdown

```bash
databricks apps stop fleetguard-console --profile abhi
```

The agent endpoint is on scale-to-zero and idles down by itself. **AI Search (`fleetguard-vs`,
~$6.72/day) is the only continuously-billing resource** — leave it, deleting the index costs most
of a working day to rebuild.
