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

export interface Me {
  user_name: string | null;
  token_source: string;
}

export const api = {
  me: () => request<Me>("/me"),
  queue: (limit = 50) => request<QueueItem[]>(`/queue?limit=${limit}`),
  campaign: (id: string) => request<CampaignDetail>(`/campaigns/${encodeURIComponent(id)}`),
  approve: (id: string, body: { title: string; rationale: string; due_in_days: number }) =>
    request<ApprovalResult>(`/campaigns/${encodeURIComponent(id)}/service-campaign`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  serviceCampaigns: () => request<Record<string, unknown>[]>("/service-campaigns"),
};
