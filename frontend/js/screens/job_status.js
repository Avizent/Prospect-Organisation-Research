/**
 * Job status screen — GET /api/jobs/{id}.
 *
 * Shows the current job state, the on-disk transition history, the
 * recorded last_error (if any), which artefacts are present on disk
 * (research_dossier / contacts / needs_assessment / briefing), and
 * a link to the briefing inspector when one is available.
 *
 * Artefact links open JSON in a new tab via the backend route; the
 * inspector does not pretty-render the dossier/contacts/needs JSON
 * itself in step 12 — that's a step 16 polish concern.
 */

import { api, ApiError } from "../api.js";
import { clear, el, formatDetail, navigate } from "../util.js";

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
  container.appendChild(transitions);
  if (lastError) container.appendChild(lastError);
}
