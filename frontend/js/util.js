/**
 * ANS Prospect Tool — small DOM/router helpers (step 12).
 *
 * Vanilla helpers only. No framework, no build step, no third-party
 * runtime. We deliberately avoid raw HTML interpolation for any value
 * that could come from a backend response — every interpolation uses
 * textContent or createElement so an attacker who somehow injected
 * a string into a briefing field can't pivot to script execution.
 */

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") {
      node.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (v === true) node.setAttribute(k, "");
    else node.setAttribute(k, String(v));
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined || child === false) continue;
    if (typeof child === "string") {
      node.appendChild(document.createTextNode(child));
    } else {
      node.appendChild(child);
    }
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

export function navigate(hash) {
  if (window.location.hash === hash) {
    // Force a re-render even if the hash hasn't changed.
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  } else {
    window.location.hash = hash;
  }
}

export function toast(message, { kind = "info", ms = 4000 } = {}) {
  const node = document.getElementById("app-toast");
  if (!node) return;
  node.textContent = message;
  node.dataset.kind = kind;
  node.classList.add("visible");
  window.clearTimeout(node._timer);
  node._timer = window.setTimeout(() => {
    node.classList.remove("visible");
    node.textContent = "";
  }, ms);
}

/**
 * Parse a hash like "#/jobs/abc/briefing" into { name, params }.
 *
 * Routes (steps 12 + 31 + 34):
 *   #/                      -> { name: "home" }
 *   #/setup                 -> { name: "setup" }
 *   #/login                 -> { name: "login" }
 *   #/jobs/new              -> { name: "new_job" }
 *   #/jobs/<id>             -> { name: "job_status", params: { id } }
 *   #/jobs/<id>/briefing    -> { name: "briefing",   params: { id } }
 *   #/jobs/<id>/brief       -> { name: "brief_viewer", params: { id } }
 *   #/jobs/<id>/manifest    -> { name: "manifest_viewer", params: { id } }
 */
export function parseRoute(hash) {
  const raw = (hash || "").replace(/^#/, "");
  const path = raw === "" || raw === "/" ? "/" : raw;
  const parts = path.split("/").filter(Boolean);

  if (parts.length === 0) return { name: "home", params: {} };
  if (parts.length === 1 && parts[0] === "setup")
    return { name: "setup", params: {} };
  if (parts.length === 1 && parts[0] === "login")
    return { name: "login", params: {} };
  if (parts[0] === "jobs" && parts[1] === "new" && parts.length === 2)
    return { name: "new_job", params: {} };
  if (parts[0] === "jobs" && parts.length === 2)
    return { name: "job_status", params: { id: parts[1] } };
  if (parts[0] === "jobs" && parts[2] === "briefing" && parts.length === 3)
    return { name: "briefing", params: { id: parts[1] } };
  if (parts[0] === "jobs" && parts[2] === "brief" && parts.length === 3)
    return { name: "brief_viewer", params: { id: parts[1] } };
  if (parts[0] === "jobs" && parts[2] === "manifest" && parts.length === 3)
    return { name: "manifest_viewer", params: { id: parts[1] } };
  return { name: "not_found", params: { path } };
}

export function formatDetail(detail) {
  if (detail === null || detail === undefined) return "";
  if (typeof detail === "string") return detail;
  try {
    return JSON.stringify(detail, null, 2);
  } catch {
    return String(detail);
  }
}
