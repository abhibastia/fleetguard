# FleetGuard — project notes for Claude

Vehicle defect early warning & recall response platform on Databricks.

**Starting a new session? Read `docs/STATUS.md` first.** It is the single "where are we"
page: phase-by-phase state, what is running unattended, what is costing money, and a
"Picking this up tomorrow" section with the next steps in priority order. Keep it current
at the end of a working session — it is what a cold session resumes from.

**`docs/ARCHITECTURE.md` is the living spec — what the system actually is.** Keep it true;
update it in the same commit as the code that changes it.

`docs/FleetGuard_Proposal.md` is **FROZEN** at 2026-08-31 — the design as proposed, never
updated. Do not "fix" it to match later findings: the gap between proposal and architecture
is the record of what was learned, and its header tabulates the known contradictions.

Build sequence in `PLAN.md`; **every problem hit during development is logged in
`docs/ISSUES.md` — add to it whenever something breaks or turns out to be wrong, especially
anything that failed silently.**

## Verified facts — do not re-derive, do not "correct" without re-checking live

These were each wrong at least once during proposal drafting and cost real time to
fix. Treat any claim that contradicts these as suspect until re-verified against a
live system or current docs — not against training data or a cached skill reference.

**NHTSA data sources (confirmed live 2026-08-29):**
- Complaints: `https://static.nhtsa.gov/odi/ffdd/cmpl/FLAT_CMPL.zip`
- Recalls: `https://static.nhtsa.gov/odi/ffdd/rcl/FLAT_RCL_POST_2010.zip`
  (**not** `FLAT_RCL.zip` — that's a dead/deleted S3 key, 404)
- Investigations: `https://static.nhtsa.gov/odi/ffdd/inv/FLAT_INV.zip`
- TSBs / manufacturer communications: `https://static.nhtsa.gov/odi/ffdd/tsbs/TSBS_RECEIVED_<range>.zip`
  (5-year chunks, e.g. `TSBS_RECEIVED_2025-2026.zip` is current). **Not**
  `FLAT_TSBS.zip` (dead) and **not** `MFR_COMMS_RECEIVED_*.zip` (that's a
  slimmer CSV sibling dataset, not the tab-delimited flat file this project needs).
- Recalls API: `api.nhtsa.gov/recalls/recallsByVehicle` — does **not** support
  conditional requests (no `ETag`/`Last-Modified`, ignores `If-Modified-Since` and
  `If-None-Match`). Every poll returns the full body. Don't design a polling
  strategy around cheap `304` responses on this endpoint.
- Recalls API correct path is `api.nhtsa.gov/recalls/recallsByVehicle`. **`api.nhtsa.gov/recallsByVehicle` (no `/recalls` segment) returns 403** — that wrong form was in the proposal until 2026-08-31. There is **no VIN→recall lookup**: `recallsByVin` 403s.
- vPIC decode: `vpic.nhtsa.dot.gov/api` — works as documented. Heavy-truck (Class 8) decode is **strong**, not weak: Freightliner/Peterbilt/Kenworth/Mack/Volvo/International all return make, model, year, `Truck-Tractor`, `Class 8: 33,001 lb and above`. vPIC returns full attributes even when the check digit fails (`ErrorCode 1`) — do not gate decode success on `ErrorCode == 0`.
- **Flat-file conditional requests (measured 2026-08-31):** `static.nhtsa.gov` returns both
  `Last-Modified` and `ETag` on `HEAD`. `If-Modified-Since` **works** (`304`, 0 bytes).
  `If-None-Match` with the exact advertised ETag **does not** — returns `200` and the full
  370 MB body. Use `If-Modified-Since` only; the ETag is published and ignored.

**NHTSA measured row counts (2026-08-31 — use these, don't re-estimate):**
- `FLAT_CMPL`: **2,240,289** rows, 51 fields, `LDATE` 1995-01-01 → 2026-08-27. 370 MB zipped.
- `FLAT_RCL_POST_2010`: **244,925** rows, 29 fields, **15,211 distinct campaigns**.
- `FLAT_INV`: 154,367 rows, 11 fields, but only **5,344 distinct investigations**
  (rows are make/model/year granular). **777 opened 2010+**; 352 of those have a `CAMPNO`.
- TSBs: **5,801,279** rows across all seven chunks. The oft-quoted "2.4M" is only the
  `2020-2024` chunk (2,406,749) — not the corpus.
- Backtest: **497** post-2010 investigations have ≥30 complaints in the prior year (the
  working set). **Keep three intervals distinct — do not conflate them:**
  (1) *complaints → investigation opens* = FleetGuard's actual claim, **not yet measured**;
  signal exists (674/777 investigations have prior complaints, median **341 in the prior
  365 days**). (2) *investigation → recall issued* = regulatory latency, median **118 days**
  (886 campaigns joined on `CAMPNO`, 97.2% investigation-first) — context only, **never
  report this as a system result**. (3) *recall → operator response* = seconds by design.
  The 118-day figure was briefly written into §3 as if it were the lead time; it is not.
- `PROD_TYPE`: V 96.78% / T 1.85% / C 0.68% / E 0.68%. Tire-only and restraint-only columns
  are perfectly scoped (zero population outside their type). V+T keeps 98.63%.
- Harm fields are **never null** (always `Y`/`N`): CRASH 6.25% Y, FIRE 2.53% Y,
  POLICE_RPT_YN 4.12% Y, MEDICAL_ATTN 1.19% Y. INJURED/DEATHS always populated;
  96.0% zero injuries, 99.8% zero deaths. Among harm-alleging complaints, police 39.0% /
  medical 12.2%; among injury-or-death complaints, 51.2% / 28.2%. Corroboration is real,
  not decorative.
- **Complaint `VIN` is `CHAR(11)`** — a partial. 85.1% populated, but it identifies no
  individual vehicle. `FLAT_RCL` has **no VIN-range columns at all**; campaigns scope by
  make/model/year + `BGMAN`/`ENDMAN`. Never design a "VIN-range match" on this data.
- `DO_NOT_DRIVE` (the Park It flag, field 28) is stored as title-case **`Yes`/`No`**, not
  `YES`/`NO` — always compare with `UPPER(...)` or a case-sensitive predicate silently
  returns zero rows. `Yes` on 2,128 rows / **211 of 15,211 campaigns** (1.39%),
  **zero for 2010–2011** (field added May 2025, backfilled unevenly). Demo from 2015+.
- `COMPDESC` (field 12) is an already-structured component field — do not spend
  `ai_extract` re-deriving it.

**Bronze pipeline (`fleetguard-bronze`, id `937b9ce4-4fbe-4493-96ad-b76317bf58db`) — built and passing 2026-08-31.**
Volume layout is **per-source subdirectories** (`cmpl/`, `rcl/`, `inv/`, `tsbs/`) because
Auto Loader monitors directories, not files. Every `read_files` call needs
`sep => '\t'`, `header => false`, `quote => '\0'`, `encoding => 'ISO-8859-1'`,
`partitionColumns => ''`. Cluster key must be in the **first 32 columns** (Delta stats
window), so metadata columns come first in the SELECT. Loaded row counts:
`bronze_complaints` 2,240,289 · `bronze_recalls` 244,925 · `bronze_investigations` 154,367 ·
`bronze_tsbs` 5,801,279 (all seven chunks, 7 distinct `_source_file`). All with 0 rescued
rows. See `docs/ISSUES.md` I-019…I-022.

Adding files to a monitored directory is **incremental** — verified: loading the six
historical TSB chunks processed only those files and left the other three bronze tables
untouched. A *full refresh*, by contrast, reprocesses everything, which is the real reason
to load a complete corpus early rather than late.

**Silver (built 2026-08-31).** Every layer reconciles exactly — `bronze = silver + quarantine`,
no silent drops:

| grain | bronze | silver | quarantine |
|---|---|---|---|
| complaints (V+T scope) | 2,209,695 | 2,209,123 | 572 |
| recalls | 244,925 | 244,701 | 224 |
| investigations | 154,367 | 154,191 | 176 |

Quarantine reasons: complaints 569 `incident_after_received` + 3 `missing_make`; recalls 224
`inverted_manufacture_window`; investigations 157 `unparseable_odate` + 19 `closed_before_opened`.

Entity-grain tables (never quote the row count when you mean entities):
`silver_investigation_case` **5,233** distinct investigations, of which **777 opened 2010+**
(the backtest population, preserved exactly); `silver_tsb_bulletin` **258,438** bulletins.
111 investigations are absent from the case table because *all* their rows lacked a parseable
`ODATE` — they could not participate in a lead-time backtest regardless.

Failure reasons are computed **once** in a staging temp view and drive both the silver and
quarantine predicates, so the two cannot drift apart. Constraints on the silver tables are
post-routing invariants — if one fires, the split logic is broken, not the source data.

**`read_files` MUST disable quote handling on these files (measured in-workspace 2026-08-31):**
- The ODI flat files are tab-delimited with **no quoting convention**, but narratives are
  free text containing `"`. Spark's CSV reader treats `"` as a quote char by default, which
  swallows tabs and shifts fields.
- Required option: `quote => '\0'` (any char that cannot occur). Also
  `sep => '\t'`, `header => false`, `encoding => 'ISO-8859-1'`.
- Measured on `FLAT_CMPL.txt` (2,240,289 rows): **with** default quoting →
  `PROD_TYPE='V'` = 2,168,077 and 162 NULLs. **With** `quote => '\0'` →
  2,168,220 and 19 NULLs, matching the ground-truth local parse exactly. 143 rows
  silently corrupted by the default.
- **`_rescued_data` was 0 in BOTH cases.** Zero rescued rows does *not* prove a clean
  parse here — it is necessary, not sufficient. Validate against known column
  cardinalities (e.g. `PROD_TYPE` counts, distinct `NHTSA ACTION NUMBER` = 5,344)
  rather than trusting the rescue column alone.

**Lakebase Change Data Feed (the Postgres→UC mechanism):**
- Official product name is **"Lakebase Change Data Feed"** (Lakebase CDF), Public
  Preview. It is *not* called "Lakehouse Sync" — that name doesn't match the current
  UI or docs, despite appearing in some cached skill reference material.
- This is a *different* feature from native **Delta Change Data Feed** (the one
  enabled on the bronze Delta table via `delta.enableChangeDataFeed`). Both exist in
  this project; keep the names distinct in docs and code comments.
- Destination tables: `lb_<table_name>_history`, columns `_pg_change_type`,
  `_pg_lsn`, `_pg_xid`, `_timestamp`, `_sort_by`.
- Prerequisites: Postgres 16/17/18, `REPLICA IDENTITY FULL` on every source table,
  `CAN MANAGE` on the Lakebase project, `USE CATALOG`/`USE SCHEMA`/`CREATE TABLE` on
  the destination.
- Configurable via UI **or** the Postgres REST API / Databricks SDKs. Asset Bundle
  support is **unconfirmed** — check before assuming `bundle deploy` covers it.
- "Latest per key" derivation pattern from the history table. **Corrected 2026-09-08 (I-080)
  — the earlier version recorded here was wrong and silently resurrected deleted rows:**
  ```sql
  SELECT * FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY <key> ORDER BY _sort_by DESC) AS rn
    FROM lb_<table>_history
    WHERE _pg_change_type <> 'update_preimage'   -- the BEFORE state, never current
  ) WHERE rn = 1 AND _pg_change_type <> 'delete' -- drop keys whose LATEST event is a delete
  ```
  **Filter deletes AFTER ranking, never before.** Excluding `'delete'` in the inner `WHERE`
  (the previous form) removes the tombstone from the window, so the last *surviving* event for
  a deleted key is its own `insert`, and the key comes back as current. Measured on
  `lb_fleetguard_agent_action_history`: 4 inserts, 2 deletes, 2 rows actually live in
  Postgres. The old pattern returned **4**, the corrected one returns **2**.
  `_sort_by` is `BIGINT`, so `ORDER BY _sort_by DESC` is safe — no lexicographic hazard.

**Databricks Apps OBO (on-behalf-of):**
- User identity arrives via the `x-forwarded-access-token` request header (lowercase
  in docs; HTTP headers are case-insensitive).
- Scope **names**: `ai-gateway`, `apps`, `files`, `genie`, `model-serving`, `postgres`,
  `sql`, `vector-search`, `sql:restricted-query`, plus SDK scopes `catalog.catalogs`,
  `catalog.connections`, `catalog.schemas`, `catalog.tables`, `workspace.workspace`
  (each supporting a `:read` modifier). `dashboards.genie` and `files.files` are **wrong** —
  `genie` and `files` are the real names.
- **Scopes are NOT declared in `app.yaml` — that was wrong (I-083, deployed 2026-09-08).**
  A `user_authorization: scopes:` block in `app.yaml` is silently ignored; the app keeps
  default scopes only. They live on the **app resource**:
  `databricks apps update <name> --json '{"name":"<name>","user_api_scopes":["postgres","sql","model-serving"]}'`
  (or the UI). Verify with `apps get` → `user_api_scopes` / `effective_user_api_scopes`.
- **`iam.access-control:read` and `iam.current-user:read` exist but are NOT assignable.**
  They appear in `effective_user_api_scopes` as platform defaults, and the API **rejects**
  them on write: *"The specified scope iam.access-control:read is not a valid scope."* So the
  earlier note that they "don't exist here" was half wrong and the skill reference listing
  them as selectable was also half wrong. List only assignable scopes; the defaults arrive
  on their own.
- **After changing scopes you MUST restart the app.** A granted-but-not-restarted app returns
  the **identical** `403 Invalid scope, required scopes: postgres` as an ungranted one —
  there is no signal distinguishing "never granted" from "granted, not yet live". Restart,
  then re-test, before concluding a scope does not work.
- **The SDK refuses to run inside an App if you also pass a token** (I-082). Apps injects
  `DATABRICKS_CLIENT_ID`/`DATABRICKS_CLIENT_SECRET` for the app's own service principal, so
  `WorkspaceClient(host=..., token=<user token>)` raises
  *"validate: more than one authorization method configured: oauth and pat"*. Pass
  **`auth_type="pat"`** to pin the caller's token. This fails **only** inside Apps — locally
  and on Render those env vars are absent — so it cannot be caught before deploying.
- Resource-bound credentials (SQL Warehouse, Model Serving, Lakebase, UC Volume) are
  rotated automatically by the platform — no refresh code needed in the App.
- An app created with `--no-compute` reports `Error: failed to reach ACTIVE, got STOPPED`.
  That is the **success** path for a deliberately-stopped create; read `apps get`, not the
  exit code.

**Databricks product-name drift (checked 2026-08-31):**
- **Declarative Automation Bundles** (DABs) is current; "Databricks Asset Bundles" is the
  old name. CLI command is still `databricks bundle ...`.
- **Lakebase bundle support is Beta** and covers `postgres_projects`, `postgres_branches`,
  `postgres_endpoints`, `postgres_roles`, `postgres_databases`, `postgres_synced_tables`,
  `postgres_catalogs`, `apps`. **Lakebase CDF is NOT a bundle resource** — enabling it is a
  manual step. (This resolves the previously-open question.)
- **"AI Search"** and **"Unity AI Gateway"** are both current, correct names — do not
  "fix" them to Mosaic AI Vector Search / Mosaic AI Gateway.
- **Agent Bricks ≠ Mosaic AI Agent Framework.** This project uses the Agent Framework.
  Agent Bricks is the Knowledge Assistant / Supervisor Agent product. The diagrams
  currently say "Agent Bricks" and are wrong.
- AI Gateway PII guardrail: `None` / `Block` / `Mask`, set via
  `PUT /api/2.0/serving-endpoints/{name}/ai-gateway`. **Output guardrails do not apply to
  streaming responses** — a streaming agent UI silently bypasses the PII output check.
- Lakebase CDF adds `_sort_by` alongside `_pg_change_type`/`_pg_lsn`/`_pg_xid`/`_timestamp`;
  `_pg_change_type` values are `insert`/`delete`/`update_preimage`/`update_postimage`;
  flush is ~15 s; destination tables auto-suffix on collision (`lb_users_history_1`).
  Databricks publishes **no** latency SLA for this path — don't attribute one.

**Databricks U2M OAuth (user login, for the Render-phase console — confirmed 2026-08-31):**
- This is a *different* flow from Apps OBO above and from the M2M `client_credentials`
  path (§8.2/§5.2 of the proposal). Needed only because the Render-hosted console
  (§8.7) sits outside the Databricks Apps ingress and has to obtain a real user token
  itself.
- Requires registering a **custom OAuth app integration** first — done in the
  **account console** ("App connections" → Add connection) or via
  `databricks account custom-app-integration create`. This is account-level, not
  workspace-admin-console. Register as a **confidential client** for a server-side
  backend (gets a client secret, shown once).
- Authorize: `https://<workspace>/oidc/v1/authorize` — `client_id`, `redirect_uri`
  (must exactly match registration), `response_type=code`, `scope`,
  `code_challenge`/`code_challenge_method=S256`, `state`. **PKCE is mandatory**, not
  optional, per Databricks' own documented example.
- Token exchange: `https://<workspace>/oidc/v1/token` — `client_id`,
  `grant_type=authorization_code`, `code`, `code_verifier`, `redirect_uri`.
- Documented supported `scopes` values for a custom app integration: `all-apis`,
  `sql`, `offline_access`, `openid`, `profile`, `email`. The Databricks Apps `app.yaml`
  scope vocabulary (`dashboards.genie`, `files.files`, `iam.access-control:read`,
  `iam.current-user:read` above) is a **different, more fine-grained set that is NOT
  confirmed to work on this endpoint** — don't assume it carries over. Use
  `all-apis offline_access` as the working default for the Render phase.
- Access token lifetime: **1 hour**. `offline_access` in scope returns a
  `refresh_token`. The exact refresh-grant request shape is standard OAuth2 but was
  **not found explicitly documented** for this endpoint — treat as needing empirical
  confirmation, not as verified.
- **Unconfirmed, don't assert either way:** whether HTTPS is enforced on
  non-localhost `redirect_uri`s (localhost plain-HTTP is shown as valid in
  Databricks' own example); whether narrower scopes than `all-apis` are accepted.

**Lakeflow Jobs `table_update` trigger** (used for the CDF→fact-table pipeline,
§8.3 of the proposal):
**CORRECTED 2026-09-08 (I-081) — the config previously recorded here could not be created.**
Both intervals were below the platform floor and both table paths were wrong. As built and
accepted (job `fleetguard-cdf-to-gold`, `851598550157757`):
```yaml
trigger:
  pause_status: PAUSED          # see STATUS — unpausing is a deliberate cost decision
  table_update:
    condition: ANY_UPDATED
    table_names:
      - "bootcamp_students.bootcamp_cdc.lb_fleetguard_agent_action_history"
      - "bootcamp_students.bootcamp_cdc.lb_fleetguard_defect_signal_history"
    min_time_between_triggers_seconds: 60   # 15 rejected: "must be greater than 60 seconds"
    wait_after_last_change_seconds: 61      # 5 rejected, same message
```
Three corrections, each verified by the API rejecting the old value:
- **Both intervals have a >60 s floor.** `15` and `5` are not merely tight, they are
  **impossible** — `jobs create` refuses them.
- **The old rationale was therefore false.** It claimed both were "tight by design" because a
  longer settle window "would push worst-case latency past the sub-minute velocity claim".
  The platform *forces* a settle window over a minute, so a `table_update` trigger cannot
  deliver a sub-minute Postgres→gold-fact path at all. **§8.3's sub-minute claim survives only
  for CDF replication itself** (measured 7.1–15.6 s, I-046) — Postgres→`bootcamp_cdc`.
  **MEASURED end to end 2026-09-08, two live cycles (n=2): Postgres commit → gold fact
  available = 155 s and 269 s.** Trigger detection is the variable part (102 s / 213 s to run
  start); the job itself is steady at 54–56 s. State it as a **range of roughly 2.5–4.5
  minutes**, never as one averaged number, and never quote the CDF figure for the whole chain.
- **`ALL_UPDATED` is wrong for this table pair.** It fires only once *every* named table has
  changed, and `fleetguard_defect_signal` is also written by the batch signals loader with no
  matching `fleetguard_agent_action` write — so a batch-only change would wait forever.
  `ANY_UPDATED` is correct here.
- The old table paths (`bootcamp_students.fleetguard.lb_agent_action_history`) had both the
  wrong schema and the wrong table name; CDF writes `lb_fleetguard_*_history` into
  `bootcamp_cdc`.

**Phase 2 fleet registry — DONE 2026-08-31.** `gold_fleet_vehicle` (20,000),
`gold_fleet_depot` (60), `gold_fleet_exposure` (~989k rows). VIN prefixes come from real
complaint VINs with the check digit recomputed; **make/model/year always come from vPIC**,
never from the complaint record (complaint VINs are dirty — `!FTEW1EG2GK`, `11C6-RR6FG2`,
GMC WMIs labelled RAM). 400 generated VINs verified independently: 400/400 exact.
- Segment is assigned from vPIC `BodyClass`/`GVWR`, not the make string — `VOLVO` covers
  both Class 8 tractors and passenger cars.
- **Exact recall matching is insufficient:** only 91/163 fleet combos match exactly;
  `MODEL_VARIANT` rows (725,356) outnumber `EXACT` (263,686) ~3:1. All 2,116 F-250s match
  only as variants (`F-250 SD` is NHTSA's dominant spelling). `gold_fleet_exposure.match_basis`
  carries the tier; §7's deterministic guarantee applies to `EXACT` only.
- Delta **cluster keys cannot be BOOLEAN** (`DELTA_CLUSTERING_COLUMNS_DATATYPE_NOT_SUPPORTED`).

**Lakebase / Phase 5 — UNPARKED 2026-08-31, both decisions made:**
- Project `projects/summer-bootcamp-2026-v2`, branch `production`, endpoint `primary`,
  host `ep-patient-sun-d1ycq936.database.us-west-2.cloud.databricks.com`, db `databricks_postgres`.
- **This project is owned by `zach@zachwilson.tech`, not by this project's team** (confirmed
  2026-09-08 via `databricks postgres list-projects --profile abhi`, which returns every
  Lakebase project visible on the shared account, not just ours — same flat-namespace
  pattern as `jobs list`'s 296 jobs). This is *why* I-084's "no `CREATEROLE` on this account"
  is true here: it is an ownership gap on someone else's project, not a platform-wide
  Lakebase limitation. Do not conflate the two when reasoning about what's fixable from here.
- **`enable_pg_native_login: true` is already set on `summer-bootcamp-2026-v2`** (confirmed
  same session) — corrects the older claim elsewhere that "Lakebase roles are all
  `LAKEBASE_OAUTH_V1`, no password auth": that described the roles someone happened to
  inspect, not a project- or platform-level restriction. Native Postgres password roles
  (`CREATE ROLE ... WITH LOGIN PASSWORD`) are a real, switched-on capability on this project.
  **Still unverified:** whether this account's identity can actually exercise it (create a
  new role) on a project it doesn't own — that's a distinct, untested question from the flag
  being on. Found by checking a bootcamp peer's own project (`zdsteele-capstone`, owned by
  `zacharysteele8@gmail.com`) at the account-metadata level only — see I-085.
- **CDF destination AUTHORISED by the user:** `databricks_postgres.bootcamp_students` →
  `bootcamp_students.bootcamp_cdc`. That destination is owned by `zach@zachwilson.tech`
  and holds 354 cohort tables. This is an **explicitly authorised exception** to the
  "own schemas only" rule — do not treat it as licence to use other shared schemas.
- **NAMING DECIDED: `fleetguard_<entity>`** → CDF output `lb_fleetguard_<entity>_history`.
  Chosen over `fg_<entity>` because a 2-letter prefix is independently guessable in a
  schema shared by ~296 students, whereas no one else is building FleetGuard. Also makes
  `SHOW TABLES LIKE 'lb_fleetguard_%'` return exactly our tables out of 354+.
  The 11 tables map 1:1 to proposal §4.4:
  `fleetguard_vehicle`, `_depot`, `_defect_signal`, `_recall_campaign`, `_vehicle_exposure`,
  `_service_campaign`, `_work_order`, `_agent_action`, `_approval`, `_audit_log`,
  `_public_summary`.
- **GET THE NAME RIGHT BEFORE THE FIRST `CREATE`.** CDF auto-suffixes on collision
  (`lb_x_history_1`) *silently*, and renaming a Postgres table orphans its history table.
  105 of the 256 existing `lb_*` tables in that schema are exactly such orphans.
- CDF is **schema-level**: every table created in that Postgres schema replicates, so all
  11 land in `bootcamp_cdc`, not just the two §8.3's trigger reads.
- §8.3's trigger path is therefore
  `bootcamp_students.bootcamp_cdc.lb_fleetguard_agent_action_history`.
- Lakebase CDF config itself is **UI-only** — no CLI or API.

**Lakebase credential API (external/service-principal path only — the App doesn't
need this, platform handles it):**
- `POST /api/2.0/postgres/credentials` — current, correct.
- `POST /api/2.0/database/credentials` — legacy/retired Provisioned-tier path, do
  not use.

## Environment quirks

- **Free-edition workspace catalog creation via CLI is blocked** for managed/default
  storage: `databricks catalogs create` fails with "Please use the UI to create a
  catalog with Default Storage." Workaround used successfully: create via SQL
  instead — `databricks experimental aitools tools query "CREATE CATALOG ..."`.
  Schema/volume creation via SQL works the same way.
- Databricks CLI profiles available: `abhi` (paid workspace, re-authenticated and
  valid as of 2026-08-31 — host `dbc-7b106152-caf3.cloud.databricks.com`, user
  `abhisek.bastia17@gmail.com`; this is the intended workspace for the project going
  forward), `free-edition` (valid, used for all work up to and including the
  Phase 1 starting point below), `DEFAULT`. Never auto-select — always pass
  `--profile` explicitly and confirm which one is intended, even though `abhi` is
  now the default in `.databrickscfg`.
- **`abhi` is a SHARED bootcamp metastore, not a private workspace.** Catalogs `main`
  (owner `zach@zachwilson.tech`) and `tabular` (owner `gudetayared@gmail.com`) belong to
  other people; `bootcamp_students` (owner `eumardassis@gmail.com`) holds ~296 schemas
  belonging to other students. **Never write outside the project's own schema.**
- **The JOBS namespace is shared and flat — there is no per-user scoping.** Measured
  2026-09-08: `databricks jobs list` returns **296 jobs, of which only 24 are FleetGuard's.**
  The rest belong to other students, and several have names that read like ours —
  `Lakebase-CDF` (sangwanrahul@icloud.com), `AgentTraceOps_Pipeline` (drajesh@hotmail.com),
  `AgentTraceOps Pipeline` (bhargavilakshmi201@gmail.com) are **not ours**. Before deleting or
  editing any job, check `creator_user_name` from `jobs get`. **Scope every destructive job
  operation to the `fleetguard-` name prefix**; never act on a `jobs list` result set
  unfiltered. The same applies to the ~250 `lb_*` tables in `bootcamp_students.bootcamp_cdc`,
  which is a cohort-shared CDF destination, not ours.
- **The project's schema is `bootcamp_students.fleetguard`** — created 2026-08-31, owned by
  `abhisek.bastia17@gmail.com`. Catalog creation is not available here, so all objects live
  in this one schema and medallion layers are **table-name prefixes** (`bronze_`, `silver_`,
  `gold_`), not sibling schemas. Volume path:
  `/Volumes/bootcamp_students/fleetguard/nhtsa_flat_files/`.
- The old `fleetguard.raw.nhtsa_flat_files` volume and test files exist only in the
  `free-edition` workspace and do **not** carry over. Nothing else is provisioned in `abhi`
  yet.

## Working conventions for this project

- **Record every Databricks platform change as a runbook — both the CLI/code path AND the
  UI path.** This project is a learning exercise as much as a build. Whenever something is
  created, configured, or changed in the workspace (a job, an endpoint, a serving
  deployment, a CDF config, a permission), capture: the exact command run, the equivalent
  click-path in the UI, what the output looked like, and anything that surprised you.
  File it in the Obsidian vault via the **`obsidian-notetaker`** agent. Some things are
  UI-only (Lakebase CDF config), some are CLI-only in practice — say which, because that
  asymmetry is itself worth knowing.


- **Never write a specific URL, API endpoint, or product/feature name into
  `docs/FleetGuard_Proposal.md` without verifying it against a live system or
  current docs first.** This project has already shipped two rounds of confidently
  wrong technical claims (a dead recall-data URL, a fabricated NHTSA conditional-
  request mechanism, a feature-name flip-flop) that only surfaced because someone
  checked. Assume the same risk applies to any new claim.
- Prefer stating a number as "estimated, to be measured" over asserting it, unless
  it's been checked against a live system this session. The proposal's own backtest
  and latency sections follow this discipline — new additions should too.
- `PLAN.md` is the source of truth for build sequencing and phase definitions of
  done. Keep it in sync with the proposal if either changes.
