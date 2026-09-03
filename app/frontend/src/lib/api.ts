/**
 * The only place the console talks to the backend.
 *
 * Every call sends credentials, because identity travels as a session cookie on Render and
 * as a platform-injected header on Databricks Apps — the frontend never handles a token
 * itself, and must never be given one. That is the auth seam (E-13) seen from this side.
 */

const BASE = "/api";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });

  if (res.status === 401) {
    // Fail loudly rather than rendering an empty queue, which would read as "no exposure"
    // — the most dangerous possible mistake in a recall console.
    throw new ApiError(401, "Sign-in required.");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* non-JSON error body; keep the status text */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export interface QueueItem {
  campaign_id: string;
  component: string | null;
  park_it: boolean;
  do_not_drive: boolean;
  vehicles_exposed: number;
  depots_affected: number;
  consequence: string | null;
}

export interface ExposedVehicle {
  vin: string | null;
  depot_id: string;
  make: string;
  model: string;
  model_year: number | null;
}

export interface CampaignDetail {
  campaign_id: string;
  component: string | null;
  park_it: boolean;
  consequence: string | null;
  remedy: string | null;
  vehicles_exposed: number;
  by_depot: Record<string, number>;
  sample_vehicles: ExposedVehicle[];
}

export interface ApprovalResult {
  service_campaign_id: string;
  campaign_id: string;
  work_orders_created: number;
  approved_by: string;
  due_date: string;
}

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

export interface ChatReply {
  reply: string;
  endpoint: string;
}

export interface Signal {
  signal_id: string;
  series_key: string;
  make: string | null;
  model: string | null;
  component: string;
  run_start: string | null;
  run_end: string | null;
  run_len: number | null;
  max_z: number | null;
  complaint_count: number | null;
  harm_share: number | null;
  fleet_vehicles: number;
  is_live: boolean;
  status: string;
}

export interface SignalSummary {
  total: number;
  live: number;
  fleet_relevant: number;
  as_of_month: string | null;
  signals: Signal[];
}

export interface EvidenceArm {
  n: number;
  detected: number;
  rate_pct: number;
  median_lead_days: number;
}

export interface ModelB {
  model_version: number;
  golden_set_size: number;
  golden_set_positive: number;
  threshold: number;
  precision: number;
  recall: number;
  roc_auc: number;
  test_set_size: number;
}

export interface Evidence {
  real: EvidenceArm;
  placebo: EvidenceArm;
  model_b: ModelB;
  lift: number;
  z: number;
  p_value: number;
  source_table: string;
  statement: string;
  generated_at: string;
}

export interface Health {
  status: string;
  auth_mode: string;
  console: boolean;
  data_mode: string;
  snapshot_captured_at: string | null;
}

export interface AuthStatus {
  enabled: boolean;
  signed_in: boolean;
  user_name: string | null;
  may_approve: boolean;
  // Which login flow this deployment runs ("github" | "databricks") and where to send the
  // browser for it. The console renders off these instead of a hardcoded GitHub button, so
  // it's correct under either FLEETGUARD_AUTH_MODE without a frontend redeploy.
  provider: string;
  login_url: string | null;
}

export interface Me {
  user_name: string | null;
  token_source: string;
}

export const api = {
  me: () => request<Me>("/me"),
  authStatus: () => request<AuthStatus>("/auth/status"),
  // /healthz sits outside /api on purpose — it must answer even when the API is unhappy.
  health: () =>
    fetch("/healthz", { credentials: "include" }).then((r) => r.json() as Promise<Health>),
  queue: (limit = 50) => request<QueueItem[]>(`/queue?limit=${limit}`),
  campaign: (id: string) => request<CampaignDetail>(`/campaigns/${encodeURIComponent(id)}`),
  approve: (id: string, body: { title: string; rationale: string; due_in_days: number }) =>
    request<ApprovalResult>(`/campaigns/${encodeURIComponent(id)}/service-campaign`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  chat: (messages: ChatTurn[]) =>
    request<ChatReply>("/chat", { method: "POST", body: JSON.stringify({ messages }) }),
  signals: (fleetOnly = false) => request<SignalSummary>(`/signals?fleet_only=${fleetOnly}`),
  evidence: () => request<Evidence>("/evidence"),
  serviceCampaigns: () => request<Record<string, unknown>[]>("/service-campaigns"),
};
