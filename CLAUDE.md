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
- vPIC decode: `vpic.nhtsa.dot.gov/api` — works as documented, no gotchas found.

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
- Scopes are declared in `app.yaml`. Default/common scopes: `sql`,
  `dashboards.genie`, `files.files`, `iam.access-control:read`,
  `iam.current-user:read`.
- Resource-bound credentials (SQL Warehouse, Model Serving, Lakebase, UC Volume) are
  rotated automatically by the platform — no refresh code needed in the App.

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
    table_names: ["fleetguard.raw.lb_agent_action_history", "..."]
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
- Databricks CLI profiles available: `abhi` (default, not currently valid),
  `free-edition` (valid, used for all work so far), `DEFAULT`. Never auto-select —
  always pass `--profile` explicitly and confirm which one is intended.
- `fleetguard.raw.nhtsa_flat_files` volume already exists in the `free-edition`
  workspace with test files landed (see Phase 1 starting point in `PLAN.md`).

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
