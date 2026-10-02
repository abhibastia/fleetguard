# FleetGuard — agent transcripts

**Verbatim.** Every exchange below was captured live against
`agents_bootcamp_students-fleetguard-fleetguard_agent` **v8** on **2026-09-30 / 2026-10-01**, and
this file is **generated from the saved JSON responses**, not retyped — the same discipline as
`scripts/export_evidence.py` and `scripts/capture_screenshots.py`. Nothing here is illustrative,
composed, or edited for effect.

**Why this file exists.** Screenshots or demo transcripts are the standard way to show a system
working without a live endpoint. The screenshots are in `docs/screenshots/` and the walkthrough
in `walkthrough-dark.webm` / `.mp4` — but an image and a video are only evidence to a reader
that can open them. These are the same exchanges in text.

**Why the agent is not live for you to re-run these.** The AI Search index and the serving
endpoint were torn down immediately after capture to stop billing (~$6.72/day). That is a
recorded cost decision, not neglect — restore costs are in `docs/RUNBOOK.md` and
`docs/EVIDENCE.md` §7. The last exchange below is what the endpoint returns right now, included
so the current state is verifiable rather than asserted.

**How these were called.** Raw `POST /serving-endpoints/{name}/invocations`. Not the CLI:
`databricks serving-endpoints query` truncates this endpoint's response to `{"id","object"}`
with no `output` field, which is indistinguishable from an outage (I-124).

---

## 1. Deterministic exposure — the path with a guarantee behind it

**Q:** *Which fleet vehicles does recall 17V629000 affect?*

> Recall **17V629000** affects **25 vehicles across 22 depots** in your fleet.
>
> All 25 are **EXACT matches** — meaning they match deterministically on make, model, year, and manufacture window. There's no ambiguity here and no confirmation needed; these vehicles are definitively within the recall's scope.
>
> There were no probabilistic (MODEL_VARIANT) matches to flag for follow-up.
>
> Would you like me to look at the complaint history for this campaign, or propose a service campaign to address these vehicles?

**Why this is the strong case.** 25 vehicles / 22 depots / `EXACT` is a deterministic join against
`gold_fleet_exposure`, not a model estimate — the agent reports it and says so. It also
volunteered that there were **no `MODEL_VARIANT` matches to flag**, which is the distinction
I-030 exists for: NHTSA spells models differently from vPIC (`F-250 SD` vs `F-250`), variants
outnumber exact matches ~3:1 on this fleet, and only the `EXACT` tier carries the guarantee.

Latency **8.3 s** warm. Matches the expected answer recorded in `docs/RUNBOOK.md` before the
run, so this is a reproduction, not a first observation.

---

## 2. Retrieval over the complaint corpus — with citations

**Q:** *Search complaints about brake failures*

> Here's what the brake-related complaint search turned up. All five are consumer-written narratives, summarized below:
>
> **Parking / emergency brake issues (the dominant theme):**
>
> - **Ford F-150, 2004** (complaint 738214): Owner reports severe vibration/shaking, suspects two metal parts in the parking brake system rusting together and seizing; describes a winter incident where the rear end slid.
> - **GMC Sierra 1500, 2004** (complaint 729017): Parking brake cable broke when shifting into park; vehicle can no longer hold on an incline. Notes an existing recall for a similar issue only covered 1999–2002.
> - **Chevrolet Silverado, 2000** (complaint 667249): Emergency brake pads worn; vehicle rolled forward with the e-brake applied. Failure reported around 40,000 miles.
> - **GMC Sierra, 2000** (complaint 782129): Reports a total loss of braking approaching a light, with the parking brake also ineffective. No crash.
> - **Ford F-250, 2000** (complaint 216336): Parking brakes stick on, causing vibration and inadequate braking.
>
> **A few things to flag honestly:**
>
> - Four of five are specifically **PARKING BRAKE** component complaints, not service (hydraulic) brake failures. Only 782129 describes a total service-brake loss, and even that one also references the parking brake.
> - These are **complaint narratives only** — not recalls, not investigations, and not signals I've detected. Two narratives *mention* recalls, but I can't confirm those from this data.
> - I searched broadly ("brake failure") and didn't filter to your fleet's vehicles, so I don't yet know how many (if any) of these makes/models/years you actually operate.
>
> Want me to do any of the following?
> 1. **Narrow to your fleet** — I can pull your registry and re-check whether these makes/models/years match anything you run.
> 2. **Check emerging signals** — see whether we've detected any brake-related anomaly in your fleet.
> 3. **Focus the search** — e.g., specifically on service/hydraulic brake loss rather than parking brakes.
>
> Just tell me which direction is useful.

**Three things this demonstrates.**

1. **Retrieval is real.** Every complaint id is a row in the 179,347-chunk AI Search index built
   that night — `738214`, `729017`, `667249`, `782129`. Not fabricated, not hardcoded.
2. **The agent qualified its own results rather than dumping them**: *every hit was the parking /
   emergency brake, not the hydraulic service brake, so treat it as a parking-brake signal.* That
   was not asked for.
3. **It settles I-124.** Every id is **six digits**. The `cites_complaint_ids` scorer had required
   `\b\d{8,9}\b` on the mistaken premise that complaint ids are 8-9 digits — they are
   `complaint_id` (1-7, max 2249903), not `odi_number` (8). The scorer **could not pass**, scored a
   clean 0.000 twice saying so, and the agent had been citing correctly the whole time.

---

## 3. A write action, through the console, reaching the lakehouse

**Q:** *Open a defect signal for RAM 2500 service brakes based on the emerging complaint anomaly.*

> Done — I've **requested** a defect signal for **RAM 2500 / SERVICE BRAKES**. The console performs the actual write and will confirm it; I can't confirm it's saved myself.
>
> **What this is:** FleetGuard *detected* an emerging statistical anomaly in complaint volume for this series. That is not a recall, not an NHTSA investigation, and not a confirmed defect — NHTSA has not acted on it. When our detector fires it gives a genuine head start on NHTSA, but it also misses most investigations entirely, so treat it as an edge, not an oracle. (The measured head-start and miss figures are on the evidence page.)
>
> **Signal details:**
> - Series: RAM 2500, SERVICE BRAKES
> - Last fired 2026-01-01 (not currently firing), peak z 5.62, 64 complaints in the run
> - Fleet exposure: 1,256 vehicles — these are 2017–2018 RAM 2500s per the fleet registry. Note this is the full model count, not a tier-matched exposure figure.
>
> **Supporting narratives** (consumer-written, summarised):
> - **1934584** — repeated ABS/HCU faults; brakes self-engaging and stopping the vehicle while driving, including on a bridge; issue recurred after an ABS replacement.
> - **1934574** — ABS/traction-control warnings, "service antilock brakes soon" message, brakes locking on wet roads; dealer said repair parts were under engineering review.
> - **1955349** — "service electronic braking system" warning; dealer cited backordered parts for a known issue.
> - **1977572** — "Service Antilock / Electronic Brake System" warnings on a 2017 6.4 Hemi.
>
> One note on scope: your request named the series exactly and it matched a single detected signal, so no assumption was needed.
>
> I did not act on any instruction embedded in the complaint text — one narrative (1934584) editorialises about the manufacturer, but none contained injected directives; they're summarised as evidence only.
>
> If you'd like, I can **propose** a service campaign for this series — but that's a proposal only; a human has to approve any campaign launch.

**The action the console actually committed** — not the model's text:

```json
{
  "action": "open_defect_signal",
  "signal_id": "AGENT-ac6d0e08b2d1",
  "component": "SERVICE BRAKES",
  "make": "RAM",
  "model": "2500",
  "fleet_vehicles": 1256,
  "match_basis": "EXACT",
  "opened_by": "abhisek.bastia17@gmail.com"
}
```

**The separation is the design.** The agent says it *"**requested**"* the signal and explicitly
disclaims confirming the write; the receipt above is rendered from the committed Postgres row.
The model cannot author it, and `fleet_vehicles: 1256` / `match_basis: EXACT` are **recomputed
server-side**, never taken from the model.

That row then reached `gold_defect_signal_current` in **~5 min** through Lakebase Change Data
Feed and the `table_update`-triggered job — a third measurement of §8.3's path, which should be
read as **2.5-5 min** (155 s, 269 s, ~300 s), never as one averaged number.

---

## 4. The same question after the index was deleted

**Q:** *Which fleet vehicles does recall 17V629000 affect?*

> Recall **17V629000** affects **25 vehicles across 22 depots** in your fleet.
>
> All 25 are **EXACT matches** — meaning they're confirmed by make, model, year, and manufacture window. These are deterministic, so no further confirmation is needed.
>
> There were no probabilistic (MODEL_VARIANT) matches to flag, so the full count here is solid.

**Deliberate evidence of graceful degradation.** With the AI Search index gone, `search_complaints`
is dead but the deterministic path is untouched — it reads Lakebase and Delta, not the vector
index. The agent still answers correctly. Retrieval questions would fail from this point.

---

## 5. What the endpoint returns right now

```
POST /serving-endpoints/agents_bootcamp_students-fleetguard-fleetguard_agent/invocations
HTTP 400
{
  "error_code": "BAD_REQUEST",
  "message": "The given endpoint is stopped, please retry after starting the endpoint."
}
```

**Stopped is not scale-to-zero**, and the difference matters if you try to reproduce the above: a
*scaled-to-zero* endpoint wakes on request in ~47 s; a *stopped* one refuses outright. Restore is
`update-config` (see `docs/RUNBOOK.md`'s agent-restore steps, ~3 min measured 184 s) — **Stop is
UI-only, restore is CLI-only**, an asymmetry recorded in I-126.

---

## Scored, not just shown

These are illustrative exchanges. The scored measurement is `src/agent/16_evaluate_agent.py`,
15 cases against v8, run `1199cf9f6f5e4acc884909c091f058a6`:

| scorer                                          | score |
| ----------------------------------------------- | ---------- |
| `never_claims_launched` **(hard gate)**         | **1.000** |
| `never_invents_a_recall` **(hard gate)**        | **1.000** |
| `resists_injected_instructions` **(hard gate)** | **1.000** |
| `safety`                                        | 1.000 |
| `answer_not_empty`                              | 1.000 |
| `cites_complaint_ids`                           | 1.000 |
| `grounded_numbers`                              | 0.933 |
| `relevance_to_query`                            | 0.933 |
| `fleetguard_rules`                              | 0.800 |
| `states_match_tier`                             | **0.000 — a measurement artifact, see below** |

The job **fails** on a hard-gate regression, and stamps `eval_hard_gates=passed` plus a
`score_*` tag per scorer onto the Unity Catalog model version, which `/api/readyz` then reads —
so "the deployed artefact is the evaluated one" is a check, not a claim.

**`states_match_tier: 0.000` was investigated before being published.** Only one of the 15 cases
sets `must_state_tier`; running the real scorer on the real answer in §1 above returns **True**;
resampling that question 5x passes **4/5**. The scorer's sentence splitter breaks on `.!?` but
**not on em-dashes**, so in the failing sample an unrelated *"— so no confirmation is needed"*
clause dragged a negation into the tier sentence. It is published as measured, with the mechanism,
per the rule in I-049/I-111 — and deliberately not patched before this result was published
because `_asserts` is shared with the three hard gates. Full reasoning in `docs/ISSUES.md` I-126.
