/**
 * The only place the console talks to the backend.
 *
 * Every call sends credentials, because identity is attached by the surface the console is
 * served from — a platform-injected header on Databricks Apps, a developer-supplied token
 * on the local server. The frontend never handles a token itself, and must never be given
 * one. That is the auth seam (E-13) seen from this side.
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
  // Non-null only when a LAUNCHED service campaign already exists for this recall.
  service_campaign_id: string | null;
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
  service_campaign_id: string | null;
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

/** What the console actually did when the agent requested a write. Distinct from `reply`
 *  on purpose: this is the committed row, whereas `reply` is the model's prose about it. */
export interface AgentActionResult {
  action: string;
  signal_id: string;
  component: string;
  make: string | null;
  model: string | null;
  fleet_vehicles: number;
  /** 'EXACT' | 'MODEL_VARIANT' | 'MAKE_ONLY' | 'NONE' — how fleet_vehicles was matched. */
  match_basis: string;
  opened_by: string;
}

export interface ChatReply {
  reply: string;
  endpoint: string;
  action_result: AgentActionResult | null;
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
  /**
   * 'EXACT' | 'MODEL_VARIANT' | 'NONE' — how `fleet_vehicles` was matched (I-079), or null on
   * agent-opened rows, which compute the tier for their chat reply but do not persist it.
   * Worth surfacing rather than hiding: most non-zero matches are variants, and a variant
   * count is genuinely weaker than an exact one.
   */
  match_basis: string | null;
  is_live: boolean;
  status: string;
  /** 'DETECTOR' (batch z-score run) or 'AGENT' (opened by the assistant). */
  source: string;
  /**
   * The human the write was attributed to. Only AGENT rows have one: the agent cannot reach
   * the database, so the console performs its write under the caller's own token. Null on
   * detector rows, which have no opener.
   */
  opened_by: string | null;
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
  signed_in: boolean;
  user_name: string | null;
  // Drives the Approve button. Both surfaces authenticate outside this app, so there is no
  // login flow to describe here — only who the caller is and whether they may write.
  may_approve: boolean;
}

export interface Me {
  user_name: string | null;
  token_source: string;
}

export interface WorkOrder {
  wo_id: string;
  service_campaign_id: string | null;
  vin: string;
  depot_id: string;
  assigned_to: string | null;
  assigned_to_name: string | null;
  due_date: string | null;
  status: string;
  created_at: string;
  completed_at: string | null;
  actual_cost: number | null;
}

export interface Technician {
  technician_id: string;
  name: string;
  depot_id: string;
  active: boolean;
}

export interface ServiceCampaign {
  service_campaign_id: string;
  campaign_id: string;
  title: string;
  vehicle_count: number;
  status: string;
  approved_by: string | null;
  approved_at: string | null;
  open_count: number;
  in_progress_count: number;
  completed_count: number;
  cancelled_count: number;
  total_actual_cost: number;
  costed_count: number;
}

export interface RecallTrendPoint {
  year: number;
  campaigns: number;
  urgent_campaigns: number;
  vehicles_exposed: number;
}

export interface RecallTrend {
  points: RecallTrendPoint[];
  latest_issued_at: string | null;
}

export interface DepotRisk {
  depot_id: string;
  depot_name: string;
  region: string;
  city: string;
  state: string;
  fleet_size: number;
  urgent_vehicles_exposed: number;
  total_vehicles_exposed: number;
  distinct_campaigns: number;
  outstanding_work_orders: number;
  overdue_work_orders: number;
}

export interface AuditLogEntry {
  audit_id: number;
  entity_type: string;
  entity_id: string;
  action: string;
  actor_principal: string;
  before_state: Record<string, unknown> | null;
  after_state: Record<string, unknown> | null;
  created_at: string;
}

export interface CostBreakdownRow {
  key: string;
  total_actual_cost: number;
  costed_count: number;
  total_work_orders: number;
}

export interface CostBreakdown {
  by_component: CostBreakdownRow[];
  by_depot: CostBreakdownRow[];
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
  serviceCampaigns: (limit = 50) =>
    request<ServiceCampaign[]>(`/service-campaigns?limit=${limit}`),
  workOrders: (params?: {
    serviceCampaignId?: string;
    depotId?: string;
    status?: string;
    limit?: number;
  }) => {
    const q = new URLSearchParams();
    if (params?.serviceCampaignId) q.set("service_campaign_id", params.serviceCampaignId);
    if (params?.depotId) q.set("depot_id", params.depotId);
    if (params?.status) q.set("status", params.status);
    // Backend default is 100 with no indication more exist — the console always asks for
    // its max (500) instead, and the view itself discloses when that cap is actually hit.
    q.set("limit", String(params?.limit ?? 500));
    return request<WorkOrder[]>(`/work-orders?${q.toString()}`);
  },
  // `assigned_to`/`actual_cost` deliberately allow `null` (explicit unassign / explicit clear)
  // distinct from omitting the key entirely (leave unchanged) — JSON.stringify drops
  // `undefined` keys but keeps an explicit `null`, which is exactly the distinction the
  // backend's `model_fields_set` check relies on. Never pass a field as `undefined` meaning
  // "clear it" — that's a no-op, not a clear.
  updateWorkOrder: (
    woId: string,
    body: { status?: string; assigned_to?: string | null; actual_cost?: number | null },
  ) =>
    request<WorkOrder>(`/work-orders/${encodeURIComponent(woId)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  technicians: (depotId?: string) =>
    request<Technician[]>(`/technicians${depotId ? `?depot_id=${encodeURIComponent(depotId)}` : ""}`),
  costBreakdown: () => request<CostBreakdown>("/cost-breakdown"),
  auditLog: (limit = 200) => request<AuditLogEntry[]>(`/audit-log?limit=${limit}`),
  depotRisk: () => request<DepotRisk[]>("/depot-risk"),
  recallTrend: () => request<RecallTrend>("/recall-trend"),
};
