// API client for the GenieBot L1 Assistant backend (doc section 7.1).
// Auth: the backend expects a bearer JWT (doc 2.3 "JWT via OpenAM, DSP
// token translation"). This UI does not implement the real OpenAM login
// redirect - that's bank-infra-specific and out of scope for a reference
// implementation - it just holds whatever token the user pastes in
// (see TokenBar.jsx), which works directly against AUTH_BACKEND=mock's
// locally-issued dev tokens (see api/deps.py create_dev_token) and against
// a real OpenAM-issued token once AUTH_BACKEND=openam is configured.

const BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";
const TOKEN_STORAGE_KEY = "geniebot_dev_token";

export function getToken() {
  return localStorage.getItem(TOKEN_STORAGE_KEY) || "";
}

export function setToken(token) {
  localStorage.setItem(TOKEN_STORAGE_KEY, token);
}

// Display-only: reads the "role" claim off the token to decide whether the
// UI shows the Dashboard link (GenieBot L1 Enhancement Plan, "Scope what
// each role can see"). Never used for actual access control - the API
// enforces that independently (api/deps.py's get_current_principal), so an
// unverified/forged claim here can only mis-render a nav link, never grant
// real access to data.
export function decodeTokenRole(token) {
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

// POST /auth/dev-token (AUTH_BACKEND=mock only) - validates the requested
// role against the backend's placeholder user directory rather than just
// trusting whatever's picked here (api/routers/auth.py). Throws (via
// request()'s error handling) with the backend's own message on a
// role/username mismatch - surface that directly, don't reword it.
export function generateDevToken(username, role) {
  return request(`/auth/dev-token`, { method: "POST", body: JSON.stringify({ username, role }) });
}

async function request(path, options = {}) {
  const token = getToken();
  const headers = {
    "Content-Type": "application/json",
    ...options.headers,
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
  if (response.status === 204) return undefined;
  return await response.json();
}

export function listIncidents(filters) {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.bot_id) params.set("bot_id", filters.bot_id);
  if (filters.environment) params.set("environment", filters.environment);
  const qs = params.toString();
  return request(`/incidents${qs ? `?${qs}` : ""}`);
}

export function getIncident(id) {
  return request(`/incidents/${id}`);
}

export function getJiraTicket(id) {
  return request(`/incidents/${id}/jira`);
}

export function getIncidentTimeline(id) {
  return request(`/incidents/${id}/timeline`);
}

// Local-only demo affordance - there's no real Jira instance here to send a
// real closure webhook, so this is how you exercise "L2 closes the ticket."
export function simulateJiraClosure(id) {
  return request(`/incidents/${id}/simulate-jira-closure`, { method: "POST" });
}

export function getIncidentPrecedent(id) {
  return request(`/incidents/${id}/precedent`);
}

export function getIncidentStats() {
  return request(`/incidents/stats`);
}

export function reviewIncident(id, body) {
  return request(`/incidents/${id}/review`, { method: "POST", body: JSON.stringify(body) });
}

export function updateTemplate(id, body) {
  return request(`/incidents/${id}/template`, { method: "PUT", body: JSON.stringify(body) });
}

export function getHealth() {
  return request(`/health`);
}
