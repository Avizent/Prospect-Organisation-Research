/**
 * Job status screen — GET /api/jobs/{id}.
 *
 * Shows the current job state, the on-disk transition history, the
 * recorded last_error (if any), which artefacts are present on disk,
 * and a link to the briefing inspector when one is available.
 *
 * Artefacts rendered (one row per key in `available_artefacts`):
 *   Stage 1 — research_dossier, contacts, needs_assessment, briefing
 *   Stage 2 — product_mapping, benefits, faq, objections, critic_report
 *
 * The renderer iterates `available_artefacts` generically; new keys
 * added by future steps appear automatically as long as the backend
 * exposes a matching `GET /api/jobs/{id}/artefacts/<kebab-case>` route
 * (`briefing` is the documented exception — it lives at
 * `/api/jobs/{id}/briefing`, not under `/artefacts/`).
 *
 * Artefact links open JSON in a new tab via the backend route; the
 * inspector does not pretty-render any artefact JSON itself — pretty
 * rendering is deferred to a later step.
 */

import { api, ApiError } from "../api.js";
import { clear, el, formatDetail, navigate, toast } from "../util.js";


// ---------------------------------------------------------------------------
// Stage 2 run control
// ---------------------------------------------------------------------------
//
// The button is rendered only when ``snapshot.current_state === "approved"``.
// It POSTs a fake-safe knowledge_bundle to the existing route. The route
// itself returns 503 unless the opt-in fake runtime is enabled
// (ANS_ENABLE_FAKE_STAGE2_RUNTIME=1) — the handler treats that as a
// documented runtime-disabled state, not a crash.

const _STAGE2_KNOWLEDGE_BUNDLE =
  "## products/emulators.md\n\n" +
  "Netropy 100G — fake runtime smoke bundle from frontend.";

const _STAGE2_USER_CONTEXT =
  "Frontend-triggered Stage 2 fake runtime run.";

const _STAGE2_DISABLED_MESSAGE =
  "Stage 2 runtime is disabled. To enable the fake runtime for " +
  "testing, restart the server with " +
  "ANS_ENABLE_FAKE_STAGE2_RUNTIME=1.";

function _renderStage2Status(node, kind, message) {
  clear(node);
  node.dataset.kind = kind;
  node.appendChild(el("p", { text: message }));
}

async function _onRunStage2(container, params, button, statusNode) {
  button.disabled = true;
  const originalLabel = button.textContent;
  button.textContent = "Running Stage 2…";
  _renderStage2Status(statusNode, "info", "Running Stage 2…");
  try {
    const result = await api.runStage2(params.id, {
      knowledge_bundle: _STAGE2_KNOWLEDGE_BUNDLE,
      user_context: _STAGE2_USER_CONTEXT,
    });
    const stages = (result && result.stages_completed) || [];
    toast(
      `Stage 2 complete — stages: ${stages.join(", ")}`,
      { kind: "success" },
    );
    await render(container, params);
    return;
  } catch (err) {
    button.disabled = false;
    button.textContent = originalLabel;
    if (err instanceof ApiError && err.status === 503) {
      _renderStage2Status(statusNode, "warn", _STAGE2_DISABLED_MESSAGE);
      return;
    }
    if (err instanceof ApiError) {
      _renderStage2Status(
        statusNode,
        "error",
        `Stage 2 run failed (HTTP ${err.status}): ${formatDetail(err.detail)}`,
      );
      return;
    }
    _renderStage2Status(
      statusNode,
      "error",
      `Stage 2 run failed: ${err && err.message ? err.message : String(err)}`,
    );
  }
}

export async function render(container, params) {
  const id = params.id;
  clear(container);
  container.appendChild(el("p", { text: "Loading job…" }));

  let snapshot;
  try {
    snapshot = await api.getJobStatus(id);
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      navigate("#/login");
      return;
    }
    clear(container);
    container.appendChild(el("div", { class: "form-banner", role: "alert" }, [
      el("p", { text: "Could not load job." }),
      el("pre", { class: "detail-pre", text: formatDetail(err.detail) }),
    ]));
    return;
  }

  clear(container);

  const header = el("section", { class: "job-header" }, [
    el("h2", { text: snapshot.company_name }),
    el("p", { class: "muted" }, [
      el("span", { text: "URL: " }),
      el("a", { href: snapshot.company_url, target: "_blank",
                rel: "noopener noreferrer", text: snapshot.company_url }),
    ]),
    el("p", { class: "muted",
              text: `Job ID: ${snapshot.job_id}` }),
    el("p", {}, [
      el("span", { text: "Current state: " }),
      el("strong", { text: snapshot.current_state }),
    ]),
    el("p", { class: "muted", text: `Created at: ${snapshot.created_at}` }),
  ]);

  const artefactList = el("ul", { class: "artefact-list" });
  const artefacts = snapshot.available_artefacts || {};
  for (const [name, present] of Object.entries(artefacts)) {
    const li = el("li", {}, [
      el("span", { text: present ? "✓ " : "· " }),
      el("span", { text: name }),
    ]);
    if (present) {
      const href = name === "briefing"
        ? `/api/jobs/${encodeURIComponent(id)}/briefing`
        : `/api/jobs/${encodeURIComponent(id)}/artefacts/`
            + name.replace(/_/g, "-");
      li.appendChild(el("span", { text: " — " }));
      li.appendChild(el("a", {
        href, target: "_blank", rel: "noopener noreferrer", text: "open JSON",
      }));
    }
    artefactList.appendChild(li);
  }

  const actions = el("div", { class: "row-actions" });
  if (artefacts.briefing) {
    actions.appendChild(el("a", {
      class: "btn btn-secondary",
      href: `#/jobs/${encodeURIComponent(id)}/briefing`,
      text: "Open briefing inspector",
    }));
  }

  // Stage 2 run control — visible only when the job is approved.
  // The button is the inspector's sole entry point to the Stage 2
  // route; everything else on this screen is read-only.
  let stage2Status = null;
  if (snapshot.current_state === "approved") {
    const stage2Button = el("button", {
      class: "btn btn-primary",
      type: "button",
      text: "Run Stage 2",
    });
    stage2Status = el("div", {
      class: "stage2-status", role: "status", "aria-live": "polite",
    });
    stage2Button.addEventListener("click", () => {
      _onRunStage2(container, params, stage2Button, stage2Status);
    });
    actions.appendChild(stage2Button);
  }

  actions.appendChild(el("a", {
    class: "btn btn-plain", href: "#/jobs/new", text: "Create another job",
  }));

  const transitions = el("section", {}, [el("h3", { text: "Transitions" })]);
  if (!snapshot.transitions || snapshot.transitions.length === 0) {
    transitions.appendChild(el("p", { class: "muted",
                                      text: "(no transitions yet)" }));
  } else {
    const table = el("table", { class: "transitions" }, [
      el("thead", {}, el("tr", {}, [
        el("th", { text: "At" }),
        el("th", { text: "From" }),
        el("th", { text: "To" }),
        el("th", { text: "Reason" }),
      ])),
    ]);
    const tbody = el("tbody");
    // state.json writes transition records with keys "from" and
    // "to" (see backend.jobs.storage.append_transition) — NOT
    // "from_state"/"to_state". The HTTP response passes the on-disk
    // shape through unchanged.
    for (const t of snapshot.transitions) {
      tbody.appendChild(el("tr", {}, [
        el("td", { text: t.at || "" }),
        el("td", { text: t.from || "" }),
        el("td", { text: t.to || "" }),
        el("td", { text: t.reason || "" }),
      ]));
    }
    table.appendChild(tbody);
    transitions.appendChild(table);
  }

  const lastError = snapshot.last_error
    ? el("section", { class: "last-error" }, [
        el("h3", { text: "Last error" }),
        el("pre", { class: "detail-pre",
                    text: formatDetail(snapshot.last_error) }),
      ])
    : null;

  container.appendChild(header);
  container.appendChild(el("h3", { text: "Artefacts on disk" }));
  container.appendChild(artefactList);
  container.appendChild(actions);
  if (stage2Status) container.appendChild(stage2Status);
  container.appendChild(transitions);
  if (lastError) container.appendChild(lastError);
}
