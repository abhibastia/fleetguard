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
pip install -r requirements.txt
export FLEETGUARD_AUTH_MODE=static-dev
export FLEETGUARD_DEV_TOKEN="$(databricks auth token --profile abhi | python3 -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')"
uvicorn fleetguard_api.main:app --reload --port 8000
```

`GET /healthz` is unauthenticated. `GET /me` proves the seam resolves an identity.

## Rules

- **No handler reads `request.headers`.** Depend on `CurrentPrincipal`.
- **Auth failure is 401, never a service-principal fallback** — that would run a user's
  request under broader grants than they hold, the exact bypass §5.1 rules out.
- Every query runs with the *user's* token so Unity Catalog evaluates ABAC under their
  identity.
