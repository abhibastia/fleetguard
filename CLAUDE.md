# FleetGuard — project notes for Claude

Vehicle defect early warning & recall response platform on Databricks. Full design
in `docs/FleetGuard_Proposal.md`; build sequence in `PLAN.md`.

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
- `DO_NOT_DRIVE` (the Park It flag, field 28) = YES on 211 of 15,211 campaigns (1.39%),
  **zero for 2010–2011** (field added May 2025, backfilled unevenly). Demo from 2015+.
- `COMPDESC` (field 12) is an already-structured component field — do not spend
  `ai_extract` re-deriving it.

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
- "Latest per key" derivation pattern from the history table:
  ```sql
  SELECT * FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY <key> ORDER BY _sort_by DESC) AS rn
    FROM lb_<table>_history
    WHERE _pg_change_type NOT IN ('delete', 'update_preimage')
  ) WHERE rn = 1
  ```

**Databricks Apps OBO (on-behalf-of):**
- User identity arrives via the `x-forwarded-access-token` request header (lowercase
  in docs; HTTP headers are case-insensitive).
- Scopes are declared in `app.yaml`. **Corrected 2026-08-31** — the current scope
  vocabulary is: `ai-gateway`, `apps`, `files`, `genie`, `model-serving`, `postgres`,
  `sql`, `vector-search`, `sql:restricted-query`, plus SDK scopes `catalog.catalogs`,
  `catalog.connections`, `catalog.schemas`, `catalog.tables`, `workspace.workspace`
  (each supporting a `:read` modifier). The previously recorded `dashboards.genie`,
  `files.files`, `iam.access-control:read`, `iam.current-user:read` were **wrong** —
  `genie` and `files` are the real names and the `iam.*` ones don't exist here.
- Resource-bound credentials (SQL Warehouse, Model Serving, Lakebase, UC Volume) are
  rotated automatically by the platform — no refresh code needed in the App.

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
```yaml
trigger:
  table_update:
    condition: ALL_UPDATED
    table_names: ["bootcamp_students.fleetguard.lb_agent_action_history", "..."]
    min_time_between_triggers_seconds: 15
    wait_after_last_change_seconds: 5
```
`wait_after_last_change_seconds` batches rapid writes into one run;
`min_time_between_triggers_seconds` caps run frequency. Both are tight in this
project by design — a longer settle window would push worst-case latency past the
sub-minute velocity claim.

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
- **The project's schema is `bootcamp_students.fleetguard`** — created 2026-08-31, owned by
  `abhisek.bastia17@gmail.com`. Catalog creation is not available here, so all objects live
  in this one schema and medallion layers are **table-name prefixes** (`bronze_`, `silver_`,
  `gold_`), not sibling schemas. Volume path:
  `/Volumes/bootcamp_students/fleetguard/nhtsa_flat_files/`.
- The old `fleetguard.raw.nhtsa_flat_files` volume and test files exist only in the
  `free-edition` workspace and do **not** carry over. Nothing else is provisioned in `abhi`
  yet.

## Working conventions for this project

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
