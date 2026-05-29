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

// ---------------------------------------------------------------------------
// Assemble Brief control (Step 33)
// ---------------------------------------------------------------------------
//
// The button is rendered only when:
//   - ``snapshot.current_state === "approved"`` (state precondition), AND
//   - ``snapshot.available_artefacts.critic_report === true`` (inputs ready).
//
// The "Stage 2 artefacts present" and "assembled Markdown present" flags
// are deliberately treated as orthogonal — first-time assembly flips
// ``prospect_brief`` from false to true while leaving ``critic_report``
// unchanged. Re-assembly leaves both true. The button's label reflects
// which case we're in:
//   - "Assemble Brief"    when ``prospect_brief`` is absent
//   - "Re-assemble Brief" when ``prospect_brief`` is present
//
// On success the handler does NOT auto-navigate to the brief viewer —
// the operator stays on the inspector page. The "View Brief" link
// appears (or refreshes) via the next ``render()``'s generic
// available_artefacts loop. Step 32's response distinguishes 201 (file
// created) from 200 (file overwritten); the toast copy mirrors that
// distinction via ``api.assembleBrief``'s ``{ status, body }`` shape.

async function _onAssembleBrief(container, params, button, statusNode) {
  button.disabled = true;
  const originalLabel = button.textContent;
  button.textContent = "Assembling…";
  _renderAssemblyStatus(statusNode, "info", "Assembling Markdown brief…");
  try {
    const result = await api.assembleBrief(params.id);
    const verb =
      result.status === 201 ? "Brief assembled" : "Brief re-assembled";
    const sha = (result.body && result.body.markdown_sha256) || "";
    const message = sha
      ? `${verb} (${sha.slice(0, 12)})`
      : verb;
    toast(message, { kind: "success" });
    // Re-render the inspector so the ``prospect_brief`` artefact row
    // and its "View Brief" link appear via the generic
    // available_artefacts loop. We deliberately do NOT navigate to
    // the brief viewer — the operator stays on the inspector page.
    await render(container, params);
    return;
  } catch (err) {
    button.disabled = false;
    button.textContent = originalLabel;
    if (err instanceof ApiError && err.status === 401) {
      navigate("#/login");
      return;
    }
    if (err instanceof ApiError && err.status === 404) {
      _renderAssemblyStatus(
        statusNode, "error", "Job no longer exists.",
      );
      toast("Assembly failed: job no longer exists.", { kind: "error" });
      return;
    }
    if (err instanceof ApiError && err.status === 409) {
      const reason =
        err.detail && typeof err.detail === "object"
          ? err.detail.reason
          : null;
      if (reason === "state_not_approved") {
        const currentState =
          (err.detail && err.detail.current_state) || "unknown";
        const msg =
          "Cannot assemble: job is in state " + currentState +
          ", must be approved.";
        _renderAssemblyStatus(statusNode, "error", msg);
        toast(msg, { kind: "error" });
        return;
      }
      if (reason === "missing_critic_report") {
        const msg =
          "Cannot assemble: critic report missing. Re-run Stage 2.";
        _renderAssemblyStatus(statusNode, "error", msg);
        toast(msg, { kind: "error" });
        return;
      }
      const msg = "Assembly failed (HTTP 409): " + formatDetail(err.detail);
      _renderAssemblyStatus(statusNode, "error", msg);
      toast(msg, { kind: "error" });
      return;
    }
    if (err instanceof ApiError) {
      const msg =
        "Assembly failed (HTTP " + err.status + "): " +
        formatDetail(err.detail);
      _renderAssemblyStatus(statusNode, "error", msg);
      toast(msg, { kind: "error" });
      return;
    }
    const msg =
      "Assembly failed: " + (err && err.message ? err.message : String(err));
    _renderAssemblyStatus(statusNode, "error", msg);
    toast(msg, { kind: "error" });
  }
}

function _renderAssemblyStatus(node, kind, message) {
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


// ---------------------------------------------------------------------------
// One-click document generation workflow (Step 42)
// ---------------------------------------------------------------------------
//
// ``_onGenerateDocuments`` runs the full approved-job pipeline in a
// single click:
//   Stage 2  →  assemble brief  →  PDF export  →  DOCX export
//
// On success the operator is automatically redirected to the manifest
// viewer where the export download links are ready.
//
// On any step failure the chain stops immediately, a human-readable
// error is shown in the ``gen-docs-status`` live region, and the
// button is re-enabled so the operator can retry without reloading.

function _renderGenDocsStatus(node, kind, message) {
  clear(node);
  node.dataset.kind = kind;
  node.appendChild(el("p", { text: message }));
}

async function _onGenerateDocuments(container, params, button, statusNode) {
  const id = params.id;
  button.disabled = true;
  const originalLabel = button.textContent;

  try {
    _renderGenDocsStatus(statusNode, "info", "Running Stage 2…");
    button.textContent = "Running Stage 2…";
    await api.runStage2(id, {
      knowledge_bundle: _STAGE2_KNOWLEDGE_BUNDLE,
      user_context: _STAGE2_USER_CONTEXT,
    });

    _renderGenDocsStatus(statusNode, "info", "Assembling brief…");
    button.textContent = "Assembling brief…";
    await api.assembleBrief(id);

    _renderGenDocsStatus(statusNode, "info", "Exporting PDF…");
    button.textContent = "Exporting PDF…";
    await api.runPdfExport(id);

    _renderGenDocsStatus(statusNode, "info", "Exporting DOCX…");
    button.textContent = "Exporting DOCX…";
    await api.runDocxExport(id);

    _renderGenDocsStatus(statusNode, "success", "Complete");
    toast("Documents generated", { kind: "success" });
    navigate(`#/jobs/${encodeURIComponent(id)}/manifest`);
  } catch (err) {
    button.disabled = false;
    button.textContent = originalLabel;
    if (err instanceof ApiError && err.status === 401) {
      navigate("#/login");
      return;
    }
    if (err instanceof ApiError && err.status === 503) {
      _renderGenDocsStatus(statusNode, "warn", _STAGE2_DISABLED_MESSAGE);
      return;
    }
    const msg = err instanceof ApiError
      ? `Document generation failed (HTTP ${err.status}): ${formatDetail(err.detail)}`
      : `Document generation failed: ${err && err.message ? err.message : String(err)}`;
    _renderGenDocsStatus(statusNode, "error", msg);
    toast(msg, { kind: "error" });
  }
}


// ---------------------------------------------------------------------------
// Step 44/45 — job progress dashboard
// ---------------------------------------------------------------------------

// States in which the job is actively running and the dashboard should poll.
// Terminal states (complete, failed) and human-gated states (briefing_ready,
// user_editing, regenerating_section, approved) are excluded — they need no
// automatic refresh.
const _ACTIVE_STATES = new Set([
  "created",
  "researching",
  "generating_documents",
]);

// Polling cadence in milliseconds. setTimeout is used so a slow response
// never queues a second in-flight request.
const _POLL_INTERVAL_MS = 4000;

// Navigation guard — incremented each time render() is called; each polling
// closure captures the value at the time it was created, and drops the tick
// if the value has changed (meaning the user navigated away or re-rendered).
let _timelineGeneration = 0;

// Conservative elapsed-time ranges (minutes) for each artefact key.
// Used only to produce approximate "about N–M min" ETA estimates; never
// shown as exact countdowns or per-stage percentages.
//   briefing duration covers product mapping + critic analysis + assembly.
//   document_manifest duration covers both PDF and DOCX export.
const _STAGE_DURATIONS = {
  research_dossier:   [1, 3],
  contacts:           [1, 2],
  needs_assessment:   [1, 2],
  briefing:           [3, 7],
  prospect_brief:     [1, 2],
  document_manifest:  [2, 4],
};

const _TS_PENDING = "pending";
const _TS_DONE    = "done";
const _TS_ACTIVE  = "active";
const _TS_WAITING = "waiting";
const _TS_FAILED  = "failed";

// Human-gated states — the pipeline is paused waiting for operator action.
// The approval-gate stage shows as _TS_WAITING in these states.
const _WAITING_STATES = new Set([
  "briefing_ready",
  "user_editing",
  "regenerating_section",
  "approved",
]);

// States that have passed the human approval gate.
const _PAST_APPROVAL_STATES = new Set([
  "generating_documents",
  "complete",
]);

// Ordered list of user-facing pipeline stages.
//
// Each stage has:
//   label   — operator-facing label shown in the progress card
//   key     — available_artefacts boolean key (null for virtual stages)
//   virtual — special done-condition token:
//               "submitted"  → always done once the job exists
//               "approval"   → done once the job passes the approval gate
//
// Stages sharing the same artefact key (Exporting PDF / Exporting DOCX /
// Delivery-ready all use document_manifest) will flip simultaneously — this
// is the honest behaviour given the available backend data.
const _TIMELINE_STAGES = [
  { label: "Submitted",            key: null,                virtual: "submitted" },
  { label: "Researching company",  key: "research_dossier",  virtual: null       },
  { label: "Finding contacts",     key: "contacts",          virtual: null       },
  { label: "Assessing needs",      key: "needs_assessment",  virtual: null       },
  { label: "Preparing briefing",   key: "briefing",          virtual: null       },
  { label: "Awaiting approval",    key: null,                virtual: "approval"  },
  { label: "Assembling brief",     key: "prospect_brief",    virtual: null       },
  { label: "Exporting PDF",        key: "document_manifest", virtual: null       },
  { label: "Exporting DOCX",       key: "document_manifest", virtual: null       },
  { label: "Delivery-ready",       key: "document_manifest", virtual: null       },
];

// Derive per-stage completion status from the snapshot's available_artefacts
// map and current_state.
//
// Rules:
//   "submitted" virtual  → always _TS_DONE
//   "approval" virtual   → _TS_DONE if past approval gate; _TS_WAITING if
//                          currently at gate; _TS_PENDING otherwise
//   artefact stage       → _TS_DONE if artefact exists; else the first
//                          incomplete stage in an active job gets _TS_ACTIVE;
//                          failed job gets _TS_FAILED; else _TS_PENDING
function _computeStages(snapshot) {
  const artefacts = snapshot.available_artefacts || {};
  const state = snapshot.current_state;
  const isActive  = _ACTIVE_STATES.has(state);
  const isWaiting = _WAITING_STATES.has(state);
  const isFailed  = state === "failed";
  let activeAssigned = false;

  return _TIMELINE_STAGES.map(stage => {
    // Determine whether this stage is complete.
    let isDone;
    if (stage.virtual === "submitted") {
      isDone = true;
    } else if (stage.virtual === "approval") {
      isDone = _PAST_APPROVAL_STATES.has(state);
    } else {
      isDone = stage.key !== null && !!artefacts[stage.key];
    }

    if (isDone) return { ...stage, status: _TS_DONE };

    // Approval gate: operator action required — waiting when at gate.
    if (stage.virtual === "approval") {
      return { ...stage, status: isWaiting ? _TS_WAITING : _TS_PENDING };
    }

    // Active job: the first incomplete artefact stage gets the active indicator.
    if (!activeAssigned && isActive) {
      activeAssigned = true;
      return { ...stage, status: _TS_ACTIVE };
    }

    if (isFailed) return { ...stage, status: _TS_FAILED };
    return { ...stage, status: _TS_PENDING };
  });
}

// Calculate overall progress as completed stages / total stages, expressed
// as an integer percentage 0–100. This is the only numeric progress figure
// shown — per-stage percentages are not calculated (they would be fake).
function _computeOverallProgress(stages) {
  const total = stages.length;
  if (total === 0) return 0;
  const done = stages.filter(s => s.status === _TS_DONE).length;
  return Math.round((done / total) * 100);
}

// Produce an approximate ETA string for active jobs by summing the
// conservative duration ranges of all incomplete artefact stages.
// Virtual stages and duplicate artefact keys are skipped so the total
// is not inflated. Returns null for non-active states.
function _computeEta(snapshot, stages) {
  if (!_ACTIVE_STATES.has(snapshot.current_state)) return null;
  let minTotal = 0;
  let maxTotal = 0;
  const counted = new Set();
  for (const stage of stages) {
    if (stage.status !== _TS_DONE && stage.key !== null && !stage.virtual) {
      if (!counted.has(stage.key)) {
        counted.add(stage.key);
        const [mn, mx] = _STAGE_DURATIONS[stage.key] || [1, 2];
        minTotal += mn;
        maxTotal += mx;
      }
    }
  }
  if (minTotal === 0 && maxTotal === 0) return null;
  return `about ${minTotal}–${maxTotal} min`;
}

// Badge text for each stage status token.
const _BADGE_LABELS = {
  done:    "Complete",
  active:  "In progress",
  pending: "Pending",
  waiting: "Waiting",
  failed:  "Failed",
};

// Render the progress dashboard section. Returns a <section> element with
// class "job-progress-timeline" containing a "progress-card" div.
//
// Layout:
//   progress-card
//     h3 "Progress"
//     progress-overall
//       overall label "Overall progress: N%"
//       <progress class="progress-overall-bar"> (determinate, 0–100)
//     <ul class="progress-timeline">
//       <li class="progress-stage-row progress-stage-row--{status}">
//         span.progress-stage-label   — stage name
//         span.progress-stage-badge   — Complete / In progress / Pending / …
//         div.progress-stage-bar      — visual bar (CSS handles colours/animation)
//       </li> × N
//     </ul>
//     p.timeline-eta          (active jobs only)
//     p.timeline-caveat
//
// Per-stage bar class: progress-stage-bar progress-stage-bar--{status}
// Overall bar failure class: progress-overall-bar--failed
function _renderProgressTimeline(snapshot) {
  const stages = _computeStages(snapshot);
  const isActive = _ACTIVE_STATES.has(snapshot.current_state);
  const isFailed = snapshot.current_state === "failed";
  const eta      = _computeEta(snapshot, stages);
  const percent  = _computeOverallProgress(stages);

  // Overall progress bar — <progress> is semantic and screen-reader accessible.
  const overallBarClass = isFailed
    ? "progress-overall-bar progress-overall-bar--failed"
    : "progress-overall-bar";
  const overallBar = el("progress", {
    class: overallBarClass,
    value: percent,
    max: 100,
  });

  const stageRows = stages.map(stage => {
    const badgeText = _BADGE_LABELS[stage.status] || stage.status;
    return el("li", {
      class: `progress-stage-row progress-stage-row--${stage.status}`,
    }, [
      el("span", { class: "progress-stage-label", text: stage.label }),
      el("span", {
        class: `progress-stage-badge progress-stage-badge--${stage.status}`,
        text:  badgeText,
      }),
      el("div", {
        class: `progress-stage-bar progress-stage-bar--${stage.status}`,
      }),
    ]);
  });

  const cardChildren = [
    el("h3", { text: "Progress" }),
    el("div", { class: "progress-overall" }, [
      el("span", { class: "timeline-overall-label",
                   text:  `Overall progress: ${percent}%` }),
      overallBar,
    ]),
    el("ul", { class: "progress-timeline" }, stageRows),
  ];

  if (isActive) {
    const etaText = eta !== null ? eta : "calculating…";
    cardChildren.push(el("p", {
      class: "timeline-eta",
      text:  `Estimated time remaining: ${etaText}`,
    }));
  }

  // Always shown — reminds operators not to treat the display as exact.
  cardChildren.push(el("p", {
    class: "timeline-caveat muted",
    text:  "Progress is approximate — inferred from completed outputs, not live agent telemetry.",
  }));

  return el("section", {
    class: "job-progress-timeline",
    "aria-label": "Job progress",
  }, [el("div", { class: "progress-card" }, cardChildren)]);
}


export async function render(container, params) {
  const generation = ++_timelineGeneration;
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
      // ``prospect_brief`` (Step 31) and ``document_manifest`` (Step 34)
      // are *display* artefacts, not JSON ones, so the inspector links
      // into in-app viewer screens rather than opening raw JSON. The
      // other entries (briefing and the eight JSON artefacts) continue
      // to open JSON in a new tab as before.
      if (name === "prospect_brief") {
        li.appendChild(el("span", { text: " — " }));
        li.appendChild(el("a", {
          href: `#/jobs/${encodeURIComponent(id)}/brief`,
          text: "View Brief",
        }));
        // Step 34: when both flags are present, expose the manifest
        // viewer alongside the brief viewer for symmetry with the
        // brief-viewer header strip.
        if (artefacts.document_manifest) {
          li.appendChild(el("span", { text: " · " }));
          li.appendChild(el("a", {
            href: `#/jobs/${encodeURIComponent(id)}/manifest`,
            text: "View Manifest",
          }));
        }
      } else if (name === "document_manifest") {
        li.appendChild(el("span", { text: " — " }));
        li.appendChild(el("a", {
          href: `#/jobs/${encodeURIComponent(id)}/manifest`,
          text: "View Manifest",
        }));
      } else {
        const href = name === "briefing"
          ? `/api/jobs/${encodeURIComponent(id)}/briefing`
          : `/api/jobs/${encodeURIComponent(id)}/artefacts/`
              + name.replace(/_/g, "-");
        li.appendChild(el("span", { text: " — " }));
        li.appendChild(el("a", {
          href, target: "_blank", rel: "noopener noreferrer", text: "open JSON",
        }));
      }
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
  let assemblyStatus = null;
  let generateDocsStatus = null;
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

    // Step 33: Assemble Brief control. Only rendered when the Stage 2
    // inputs are on disk (critic_report) so the backend route has a
    // chance of succeeding. The label flips between first-time and
    // re-assembly based on whether ``prospect_brief`` is already
    // present — the request body and handler logic are identical.
    if (artefacts.critic_report) {
      const alreadyAssembled = !!artefacts.prospect_brief;
      const assembleButton = el("button", {
        class: "btn btn-secondary",
        type: "button",
        text: alreadyAssembled ? "Re-assemble Brief" : "Assemble Brief",
      });
      assemblyStatus = el("div", {
        class: "assembly-status",
        role: "status",
        "aria-live": "polite",
      });
      assembleButton.addEventListener("click", () => {
        _onAssembleBrief(
          container, params, assembleButton, assemblyStatus,
        );
      });
      actions.appendChild(assembleButton);
    }

    // Step 42: one-click document generation pipeline. Runs Stage 2 →
    // assemble → PDF → DOCX and redirects to the manifest viewer on
    // success. The button appears for ALL approved jobs — no additional
    // artefact gate is needed because Stage 2 surfaces any missing-input
    // errors through the normal error path.
    const generateDocsButton = el("button", {
      class: "btn btn-primary",
      type: "button",
      text: "Generate Documents",
    });
    generateDocsStatus = el("div", {
      class: "gen-docs-status",
      role: "status",
      "aria-live": "polite",
    });
    generateDocsButton.addEventListener("click", () => {
      _onGenerateDocuments(
        container, params, generateDocsButton, generateDocsStatus,
      );
    });
    actions.appendChild(generateDocsButton);
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
  container.appendChild(_renderProgressTimeline(snapshot));
  container.appendChild(el("h3", { text: "Artefacts on disk" }));
  container.appendChild(artefactList);
  container.appendChild(actions);
  if (stage2Status) container.appendChild(stage2Status);
  if (assemblyStatus) container.appendChild(assemblyStatus);
  if (generateDocsStatus) container.appendChild(generateDocsStatus);
  container.appendChild(transitions);
  if (lastError) container.appendChild(lastError);

  // Poll while the job is in an active (running) state. setTimeout is used
  // so a slow response never queues a second in-flight request. The
  // generation guard ensures we stop polling if the
  // user navigated away or triggered a fresh render before the tick fires.
  if (_ACTIVE_STATES.has(snapshot.current_state)) {
    setTimeout(async () => {
      if (_timelineGeneration !== generation) return;
      await render(container, params);
    }, _POLL_INTERVAL_MS);
  }
}
