// API client for the GenieBot L1 Assistant backend (doc section 7.1).
// Auth: the backend expects a bearer JWT (doc 2.3 "JWT via OpenAM, DSP
// token translation"). This UI does not implement the real OpenAM login
// redirect - that's bank-infra-specific and out of scope for a reference
// implementation - it just holds whatever token the user pastes in
// (see TokenBar.tsx), which works directly against AUTH_BACKEND=mock's
// locally-issued dev tokens (see api/deps.py create_dev_token) and against
// a real OpenAM-issued token once AUTH_BACKEND=openam is configured.

const BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";
const TOKEN_STORAGE_KEY = "geniebot_dev_token";

export function getToken(): string {
  return localStorage.getItem(TOKEN_STORAGE_KEY) || "";
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_STORAGE_KEY, token);
}

// Display-only: reads the "role" claim off the token to decide whether the
// UI shows the Dashboard link (GenieBot L1 Enhancement Plan, "Scope what
// each role can see"). Never used for actual access control - the API
// enforces that independently (api/deps.py's get_current_principal), so an
// unverified/forged claim here can only mis-render a nav link, never grant
// real access to data.
export function decodeTokenRole(token: string): string | null {
  try {
    const payloadSegment = token.split(".")[1];
    if (!payloadSegment) return null;
    const base64 = payloadSegment.replace(/-/g, "+").replace(/_/g, "/");
    const payload = JSON.parse(atob(base64));
    return typeof payload.role === "string" ? payload.role : null;
  } catch {
    return null;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const response = await fetch(`${BASE_URL}${path}`, { ...options, headers });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      // ignore
    }
    throw new Error(`${response.status}: ${detail}`);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export interface IncidentListItem {
  incident_id: string;
  bot_id: string;
  job_run_id: string;
  environment: string;
  status: string;
  error_signature_id: string | null;
  jira_key: string | null;
  ingested_at: string;
  updated_at: string;
}

export interface EvidenceLine {
  line_no: number;
  text: string;
}

export interface ParseOutput {
  exception_type: string;
  exception_message: string;
  failing_module: string;
  root_frame: string;
  cascading_errors: string[];
  evidence_lines: EvidenceLine[];
  parse_status: "parsed" | "unparseable";
}

export interface Citation {
  type: "log" | "kb";
  ref: string;
}

export interface DiagnosisOutput {
  root_cause: string;
  proposed_resolution: string;
  resolution_type: "guidance" | "controlled_rerun" | "escalate";
  rerun_parameters: Record<string, unknown>;
  citations: Citation[];
  confidence: number;
  insufficient_information: boolean;
}

export interface TemplatePayload {
  incident_id: string;
  bot_id: string;
  job_run_id: string;
  environment: string;
  error_category: string;
  summary: string;
  root_cause: string;
  recommended_action: string;
  evidence_refs: { type: string; ref: string }[];
  log_s3_uri?: string | null;
  priority?: string | null;
  unknown_fields: string[];
}

export interface IncidentDetail {
  incident_id: string;
  bot_id: string;
  job_run_id: string;
  user_id: string;
  environment: string;
  log_s3_uri: string;
  status: string;
  error_signature_id: string | null;
  parse_output: ParseOutput | null;
  diagnosis: DiagnosisOutput | null;
  template_payload: TemplatePayload | null;
  reviewer_id: string | null;
  decision: string | null;
  reviewer_comment: string | null;
  jira_key: string | null;
  token_cost_usd: string;
  rerun_count: number;
  active_prompt_versions: Record<string, number> | null;
  active_index_version: string | null;
  ingested_at: string;
  created_at: string;
  updated_at: string;
  generic_l1_checklist: string[] | null;
}

export function listIncidents(filters: {
  status?: string;
  bot_id?: string;
  environment?: string;
}): Promise<IncidentListItem[]> {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.bot_id) params.set("bot_id", filters.bot_id);
  if (filters.environment) params.set("environment", filters.environment);
  const qs = params.toString();
  return request(`/incidents${qs ? `?${qs}` : ""}`);
}

export function getIncident(id: string): Promise<IncidentDetail> {
  return request(`/incidents/${id}`);
}

export interface JiraTicketDetail {
  key: string;
  summary: string;
  description: string;
  status: string;
  labels: string[];
  comments: string[];
  created_at: string;
}

export function getJiraTicket(id: string): Promise<JiraTicketDetail> {
  return request(`/incidents/${id}/jira`);
}

export interface IncidentTimelineEntry {
  status: string;
  at: string;
}

export function getIncidentTimeline(id: string): Promise<IncidentTimelineEntry[]> {
  return request(`/incidents/${id}/timeline`);
}

// Local-only demo affordance - there's no real Jira instance here to send a
// real closure webhook, so this is how you exercise "L2 closes the ticket."
export function simulateJiraClosure(id: string): Promise<{ closed_incident_ids: string[] }> {
  return request(`/incidents/${id}/simulate-jira-closure`, { method: "POST" });
}

export interface PrecedentIncident {
  incident_id: string;
  job_run_id: string;
  at: string;
  reviewer_id: string | null;
  reviewer_comment: string | null;
  resolution_summary: string;
  jira_key: string | null;
  jira_status: string | null;
  within_dedup_window: boolean;
}

export interface IncidentPrecedent {
  resolved_precedent: PrecedentIncident | null;
  open_precedent: PrecedentIncident | null;
}

export function getIncidentPrecedent(id: string): Promise<IncidentPrecedent> {
  return request(`/incidents/${id}/precedent`);
}

export interface IncidentStats {
  total_incidents: number;
  open_count: number;
  status_counts: Record<string, number>;
  decision_counts: Record<string, number>;
  confidence_buckets: Record<string, number>;
  avg_confidence: number | null;
  cost_last_24h_usd: string;
  low_context_escalations_last_24h: number;
  recent: IncidentListItem[];
}

export function getIncidentStats(): Promise<IncidentStats> {
  return request(`/incidents/stats`);
}

export type ReviewDecision = "approve" | "reject" | "approve_rerun" | "resolved" | "escalated";

export function reviewIncident(
  id: string,
  body: { reviewer_id: string; decision: ReviewDecision; comment?: string }
): Promise<{ incident_id: string; status: string }> {
  return request(`/incidents/${id}/review`, { method: "POST", body: JSON.stringify(body) });
}

export function updateTemplate(
  id: string,
  body: { reviewer_id: string; template_payload: TemplatePayload }
): Promise<{ incident_id: string; updated: boolean }> {
  return request(`/incidents/${id}/template`, { method: "PUT", body: JSON.stringify(body) });
}

export interface HealthResponse {
  status: string;
  checks: Record<string, string>;
}

export function getHealth(): Promise<HealthResponse> {
  return request(`/health`);
}
