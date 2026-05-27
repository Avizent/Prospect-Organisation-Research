/**
 * ANS Prospect Tool — API client (step 12).
 *
 * Thin fetch wrappers for the Step 11 backend surface:
 *   /auth/setup/status, /auth/setup, /auth/login, /auth/logout, /auth/me
 *   /api/jobs (POST + GET + artefact GETs + briefing GET/PATCH + approval edges)
 *
 * Design rules:
 *   - All requests are same-origin and rely on the HttpOnly session cookie.
 *   - JavaScript never reads or writes the session cookie and never
 *     touches any browser storage API. The browser attaches the
 *     cookie automatically because we set `credentials: 'same-origin'`.
 *   - Every path is relative; no third-party hosts are reachable
 *     from this module.
 *   - Errors normalise to a single `ApiError` with `status` and
 *     `detail` so callers can branch on HTTP status without sniffing
 *     response bodies themselves.
 */

class ApiError extends Error {
  constructor(message, { status, detail }) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function request(method, path, { json, form } = {}) {
  const init = {
    method,
    credentials: "same-origin",
    headers: {},
  };
  if (json !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(json);
  } else if (form !== undefined) {
    const body = new URLSearchParams();
    for (const [k, v] of Object.entries(form)) {
      body.append(k, v);
    }
    init.body = body;
    init.headers["Content-Type"] = "application/x-www-form-urlencoded";
  }

  const res = await fetch(path, init);
  let body = null;
  let detail = null;

  if (res.status !== 204) {
    const text = await res.text();
    if (text) {
      try {
        body = JSON.parse(text);
        detail = body && typeof body === "object" ? body.detail ?? null : null;
      } catch {
        body = text;
        detail = text;
      }
    }
  }

  if (!res.ok) {
    throw new ApiError(`HTTP ${res.status}`, { status: res.status, detail });
  }
  return body;
}

const api = {
  // Auth
  getSetupStatus: () => request("GET", "/auth/setup/status"),
  setup: (body) => request("POST", "/auth/setup", { json: body }),
  login: (username, password) =>
    request("POST", "/auth/login", { form: { username, password } }),
  logout: () => request("POST", "/auth/logout"),
  getMe: () => request("GET", "/auth/me"),

  // Jobs
  createJob: (body) => request("POST", "/api/jobs", { json: body }),
  getJobStatus: (id) =>
    request("GET", `/api/jobs/${encodeURIComponent(id)}`),

  // Artefact reads
  getResearchDossier: (id) =>
    request("GET",
      `/api/jobs/${encodeURIComponent(id)}/artefacts/research-dossier`),
  getContacts: (id) =>
    request("GET",
      `/api/jobs/${encodeURIComponent(id)}/artefacts/contacts`),
  getNeedsAssessment: (id) =>
    request("GET",
      `/api/jobs/${encodeURIComponent(id)}/artefacts/needs-assessment`),
  getBriefing: (id) =>
    request("GET", `/api/jobs/${encodeURIComponent(id)}/briefing`),

  // Approval gate
  openForEditing: (id) =>
    request("POST", `/api/jobs/${encodeURIComponent(id)}/approval/open`),
  patchBriefing: (id, patch) =>
    request("PATCH", `/api/jobs/${encodeURIComponent(id)}/briefing`,
      { json: patch }),
  approveJob: (id) =>
    request("POST", `/api/jobs/${encodeURIComponent(id)}/approval/approve`),
  requestRegeneration: (id, section, instructions) =>
    request("POST",
      `/api/jobs/${encodeURIComponent(id)}/approval/regenerate/request`,
      { json: { section, instructions } }),
  completeRegeneration: (id, section, briefing) =>
    request("POST",
      `/api/jobs/${encodeURIComponent(id)}/approval/regenerate/complete`,
      { json: { section, briefing } }),
  failRegeneration: (id, category, details) =>
    request("POST",
      `/api/jobs/${encodeURIComponent(id)}/approval/regenerate/fail`,
      { json: { category, details } }),
};

export { api, ApiError };
