# FleetGuard API

FastAPI backend. Runs unchanged on Render and Databricks Apps — the only difference is how
the caller's Databricks token arrives, and that is confined to `fleetguard_api/auth/tokens.py`
(see `docs/ENHANCEMENTS.md` E-13).

## Auth modes

`FLEETGUARD_AUTH_MODE` is **required**. It is never inferred, because a misconfiguration
would silently select the wrong trust model.

| Mode | Surface | Token source |
|---|---|---|
| `databricks-apps` | Databricks Apps | `X-Forwarded-Access-Token` header, injected by the ingress |
| `render-u2m` | Render | U2M OAuth session (Path D, §8.7) |
| `static-dev` | Local only | `FLEETGUARD_DEV_TOKEN` |

> `databricks-apps` trusts a request header. That is safe **only** behind Databricks Apps
> ingress, where the platform sets it. Selecting it on Render would let any client forge an
> identity.

## Run locally

```bash
scripts/run_local_static_dev.sh          # port 8811 by default, or pass one: ... 8000
```

This is the whole local setup, checked in rather than reconstructed from memory each
session (the previous version of this snippet only set `FLEETGUARD_AUTH_MODE` and
`FLEETGUARD_DEV_TOKEN` — enough for `/healthz` and `/me`, but every Lakebase-backed
endpoint needs `DATABRICKS_HOST`, and every write-path test needs `FLEETGUARD_DEV_USER` +
`FLEETGUARD_APPROVERS` too, or `may_approve` never returns `true`). Read the script if you
need a different profile or user — it's short and everything is inline, no hidden config.

`GET /healthz` is unauthenticated. `GET /me` proves the seam resolves an identity, and now
also reports `may_approve` and `assigned_depot_id` (role-based views, 2026-09-05).

After a frontend change, rebuild the console it serves: `scripts/build_console.sh`.

## Rules

- **No handler reads `request.headers`.** Depend on `CurrentPrincipal`.
- **Auth failure is 401, never a service-principal fallback** — that would run a user's
  request under broader grants than they hold, the exact bypass §5.1 rules out.
- Every query runs with the *user's* token so Unity Catalog evaluates ABAC under their
  identity.
