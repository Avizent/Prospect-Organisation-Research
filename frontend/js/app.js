/**
 * ANS Prospect Tool — SPA bootstrap and hash router (step 12).
 *
 * Routes:
 *   #/                      -> redirect to #/jobs/new (or #/setup/#/login)
 *   #/setup                 -> first-run wizard (no auth required)
 *   #/login                 -> sign-in form (no auth required)
 *   #/jobs/new              -> create a new job
 *   #/jobs/<id>             -> job status
 *   #/jobs/<id>/briefing    -> briefing inspector / approval gate
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

const PUBLIC_ROUTES = new Set(["setup", "login"]);

function renderNav(currentUser) {
  const nav = document.getElementById("app-nav");
  if (!nav) return;
  clear(nav);
  if (!currentUser) return;
  nav.appendChild(el("a", { href: "#/jobs/new", text: "New job" }));
  nav.appendChild(el("span", { class: "nav-spacer", text: " · " }));
  nav.appendChild(el("span", { class: "nav-user",
                               text: `Signed in as ${currentUser}` }));
  nav.appendChild(el("span", { class: "nav-spacer", text: " · " }));
  nav.appendChild(el("a", {
    href: "#",
    text: "Sign out",
    onclick: async (event) => {
      event.preventDefault();
      try { await api.logout(); } catch { /* idempotent */ }
      navigate("#/login");
    },
  }));
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
    renderNav(null);
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

  renderNav(username);

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
