/**
 * ANS Prospect Tool — SPA bootstrap and hash router.
 *
 * Step 12: initial scaffold.
 * Step 46: replaced renderNav with renderShell(currentUser, route) which
 *          renders the four-step workflow stepper, the Active Job chip, the
 *          Operational Mode chip, and the operator identity panel.
 *
 * Routes:
 *   #/                      -> redirect to #/jobs/new (or #/setup/#/login)
 *   #/setup                 -> first-run wizard (no auth required)
 *   #/login                 -> sign-in form (no auth required)
 *   #/jobs/new              -> create a new job
 *   #/jobs/<id>             -> job status
 *   #/jobs/<id>/briefing    -> briefing inspector / approval gate
 *   #/jobs/<id>/brief       -> read-only Markdown brief viewer (step 31)
 *   #/jobs/<id>/manifest    -> read-only document manifest / provenance
 *                              viewer (step 34)
 *
 * The bootstrap sequence on every navigation:
 *   1. If the app hasn't been set up, force the user to #/setup.
 *   2. Otherwise, if not signed in, force #/login.
 *   3. Otherwise, render the requested screen.
 *
 * No production model client or vendor SDK touches this layer —
 * the inspector talks only to the Step 11 backend over fetch.
 */

import { api, ApiError } from "./api.js";
import { clear, el, navigate, parseRoute } from "./util.js";
import { render as renderLogin } from "./screens/login.js";
import { render as renderSetup } from "./screens/setup.js";
import { render as renderNewJob } from "./screens/new_job.js";
import { render as renderJobStatus } from "./screens/job_status.js";
import { render as renderBriefing } from "./screens/briefing.js";
import { render as renderBriefViewer } from "./screens/brief_viewer.js";
import { render as renderManifestViewer } from "./screens/manifest_viewer.js";

const PUBLIC_ROUTES = new Set(["setup", "login"]);

// ---------------------------------------------------------------------------
// Workflow stepper — Step 46
// ---------------------------------------------------------------------------

// Ordered workflow steps shown in the header stepper. Each step lists the
// route names that count as "this step is active". A step is "complete" when
// a later step is active.
const _STEPS = [
  { n: 1, label: "New Job",         routes: new Set(["new_job"]) },
  { n: 2, label: "Researching",     routes: new Set(["job_status"]) },
  { n: 3, label: "Review Brief",    routes: new Set(["briefing", "brief_viewer"]) },
  { n: 4, label: "Delivery Ready",  routes: new Set(["manifest_viewer"]) },
];

// Returns "active", "complete", or "" for a given step and current route.
function _stepState(step, route) {
  if (!route) return "";
  if (step.routes.has(route.name)) return "active";
  const activeIdx = _STEPS.findIndex(s => s.routes.has(route.name));
  const stepIdx   = _STEPS.indexOf(step);
  if (activeIdx > -1 && stepIdx < activeIdx) return "complete";
  return "";
}

// Render the app shell: workflow stepper + operator identity panel.
// Called on every dispatch so the header always reflects the current route.
// Uses el() throughout — all DOM construction via the el() helper.
function renderShell(currentUser, route) {
  // --- Stepper ---
  const stepper = document.getElementById("app-stepper");
  if (stepper) {
    clear(stepper);

    // Active job chip: shown when the current route carries a job id.
    const jobId = route && route.params && route.params.id;
    if (currentUser && jobId) {
      const shortId = jobId.length > 12
        ? jobId.slice(0, 8) + "\u2026"
        : jobId;
      stepper.appendChild(el("span", { class: "app-active-job" }, [
        el("span", { class: "app-active-job-label", text: "Active Job:" }),
        el("span", { class: "app-active-job-name",  text: shortId }),
      ]));
      stepper.appendChild(
        el("span", { class: "app-stepper-sep", text: " / " }),
      );
    }

    _STEPS.forEach((step, idx) => {
      if (idx > 0) {
        stepper.appendChild(
          el("span", { class: "app-stepper-sep", text: " / " }),
        );
      }
      const state = _stepState(step, route);
      const cls = state === "active"   ? "app-step app-step--active"
                : state === "complete" ? "app-step app-step--complete"
                : "app-step";
      stepper.appendChild(el("span", { class: cls }, [
        el("span", { class: "app-step-num",   text: String(step.n) }),
        el("span", { class: "app-step-label", text: step.label }),
      ]));
    });
  }

  // --- Operator panel ---
  const operatorDiv = document.getElementById("app-operator");
  if (operatorDiv) {
    clear(operatorDiv);
    if (!currentUser) return;
    operatorDiv.appendChild(el("div", { class: "app-operator-info" }, [
      el("span", { class: "app-operator-name",  text: "Internal Operator" }),
      el("span", { class: "app-operator-email", text: currentUser }),
    ]));
    operatorDiv.appendChild(el("a", {
      href: "#",
      class: "app-operator-signout btn btn-ghost",
      text: "Sign out",
      onclick: async (event) => {
        event.preventDefault();
        try { await api.logout(); } catch { /* idempotent */ }
        navigate("#/login");
      },
    }));
  }
}

async function checkSetup() {
  try {
    const status = await api.getSetupStatus();
    return Boolean(status && status.setup_completed);
  } catch {
    // If /auth/setup/status itself fails we err on the side of
    // pushing the user toward setup so they aren't trapped at a
    // blank screen. The setup endpoint will 404 if setup is in
    // fact already complete, which the setup screen handles.
    return false;
  }
}

async function whoAmI() {
  try {
    const me = await api.getMe();
    return me && me.username ? me.username : null;
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return null;
    return null;
  }
}

async function dispatch() {
  const main = document.getElementById("app-main");
  if (!main) return;
  const route = parseRoute(window.location.hash);

  // Public routes render immediately — no auth check.
  if (PUBLIC_ROUTES.has(route.name)) {
    renderShell(null, route);
    if (route.name === "setup") {
      const isSetUp = await checkSetup();
      if (isSetUp) { navigate("#/login"); return; }
      renderSetup(main);
    } else {
      renderLogin(main);
    }
    return;
  }

  const isSetUp = await checkSetup();
  if (!isSetUp) { navigate("#/setup"); return; }

  const username = await whoAmI();
  if (!username) { navigate("#/login"); return; }

  renderShell(username, route);

  switch (route.name) {
    case "home":
      navigate("#/jobs/new");
      return;
    case "new_job":
      renderNewJob(main);
      return;
    case "job_status":
      await renderJobStatus(main, route.params);
      return;
    case "briefing":
      await renderBriefing(main, route.params);
      return;
    case "brief_viewer":
      await renderBriefViewer(main, route.params);
      return;
    case "manifest_viewer":
      await renderManifestViewer(main, route.params);
      return;
    case "not_found":
    default:
      clear(main);
      main.appendChild(el("h2", { text: "Not found" }));
      main.appendChild(el("p", {}, [
        el("span", { text: "No screen for " }),
        el("code", { text: window.location.hash || "(empty)" }),
      ]));
      main.appendChild(el("p", {}, [
        el("a", { href: "#/jobs/new", text: "Go to new job" }),
      ]));
  }
}

window.addEventListener("hashchange", () => { dispatch(); });
window.addEventListener("DOMContentLoaded", () => { dispatch(); });
