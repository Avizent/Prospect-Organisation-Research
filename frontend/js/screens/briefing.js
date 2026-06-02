/**
 * Briefing inspector — GET /api/jobs/{id}/briefing plus the approval
 * gate (open-for-editing, save section, approve, request/complete/
 * fail regeneration).
 *
 * Step 50: polished three-column layout — sidebar navigation, central
 * editor cards, right-side contextual panels. All API calls, state
 * machine behaviour, validation, and error handling are unchanged.
 *
 * The five editable sections (snapshot, business_context, it_landscape,
 * key_people, opportunity) are presented as JSON textareas. The
 * operator edits the JSON, hits "Save Draft Changes", and the inspector
 * POSTs a BriefingPatch with just that section. Validation errors
 * from the backend (422 with the pydantic error list) are rendered
 * inline so a broken source_indices reference points at the right
 * field.
 *
 * Sources are read-only here — editing the source register in step 12
 * would orphan source_indices in other sections.
 */

import { api, ApiError } from "../api.js";
import { clear, el, formatDetail, navigate, toast } from "../util.js";

const EDITABLE_SECTIONS = [
  "snapshot",
  "business_context",
  "it_landscape",
  "key_people",
  "opportunity",
];

// Operator-friendly display labels for each section key.
const _SECTION_LABELS = {
  snapshot:         "Executive Summary",
  business_context: "Market & Competitive Position",
  it_landscape:     "Technology Landscape",
  key_people:       "Key Decision Makers",
  opportunity:      "Value Alignment & Pitch Generator",
};

function _displayLabel(key) {
  if (_SECTION_LABELS[key]) return _SECTION_LABELS[key];
  // Fallback: title-case the raw key.
  return key.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
}

function _sectionEditor(id, sectionName, value, getState) {
  const label = _displayLabel(sectionName);
  const banner = el("div", { class: "form-banner hidden", role: "alert" });
  const textarea = el("textarea", {
    class: "br-editor-textarea section-json",
    rows: 16,
    spellcheck: "false",
  });
  textarea.value = JSON.stringify(value ?? null, null, 2);

  const regenInput = el("textarea", {
    class: "regen-instructions",
    rows: 3,
    placeholder: "Rewrite instructions for the regeneration agent\u2026",
  });

  const save = el("button", {
    type: "button",
    class: "btn btn-secondary",
    text: "Save Draft Changes",
    onclick: async () => {
      banner.classList.add("hidden");
      let parsed;
      try {
        parsed = JSON.parse(textarea.value);
      } catch (err) {
        banner.textContent = `Invalid JSON: ${err.message}`;
        banner.classList.remove("hidden");
        return;
      }
      const patch = { [sectionName]: parsed };
      try {
        await api.patchBriefing(id, patch);
        toast(`Saved ${sectionName}.`);
        render(document.getElementById("app-main"), { id });
      } catch (err) {
        if (err instanceof ApiError) {
          if (err.status === 401) { navigate("#/login"); return; }
          banner.textContent = formatDetail(err.detail
            ?? `Save failed (HTTP ${err.status}).`);
        } else {
          banner.textContent = "Save failed \u2014 unexpected error.";
        }
        banner.classList.remove("hidden");
      }
    },
  });

  const requestRegen = el("button", {
    type: "button",
    class: "btn btn-plain",
    text: "Refine Section with AI",
    onclick: async () => {
      banner.classList.add("hidden");
      const instructions = regenInput.value.trim();
      if (!instructions) {
        banner.textContent = "Provide rewrite instructions first.";
        banner.classList.remove("hidden");
        return;
      }
      const state = getState();
      if (state !== "user_editing") {
        banner.textContent =
          `Regeneration only allowed in user_editing (currently ${state}).`;
        banner.classList.remove("hidden");
        return;
      }
      try {
        await api.requestRegeneration(id, sectionName, instructions);
        toast(`Requested regeneration of ${sectionName}.`);
        render(document.getElementById("app-main"), { id });
      } catch (err) {
        if (err instanceof ApiError) {
          if (err.status === 401) { navigate("#/login"); return; }
          banner.textContent = formatDetail(err.detail
            ?? `Request failed (HTTP ${err.status}).`);
        } else {
          banner.textContent = "Request failed \u2014 unexpected error.";
        }
        banner.classList.remove("hidden");
      }
    },
  });

  return el("section", {
    id: `br-section-${sectionName}`,
    class: "br-editor-card briefing-section",
    "data-section": sectionName,
  }, [
    el("div", { class: "br-editor-header" }, [
      el("h3", { class: "br-editor-title", text: label }),
      el("span", { class: "br-editable-badge", text: "EDITABLE MODE" }),
    ]),
    banner,
    textarea,
    el("div", { class: "br-editor-footer row-actions" }, [save, requestRegen]),
    el("details", { class: "regen-details" }, [
      el("summary", { text: "Regeneration instructions" }),
      regenInput,
    ]),
  ]);
}

export async function render(container, params) {
  const id = params.id;
  clear(container);
  container.appendChild(el("p", { text: "Loading briefing\u2026" }));

  let status;
  let briefing;
  try {
    status = await api.getJobStatus(id);
    briefing = await api.getBriefing(id);
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      navigate("#/login");
      return;
    }
    clear(container);
    container.appendChild(el("div", { class: "form-banner", role: "alert" }, [
      el("p", { text: "Could not load briefing." }),
      el("pre", { class: "detail-pre",
                  text: formatDetail(err.detail ?? err.message) }),
    ]));
    return;
  }

  clear(container);

  const currentState = status.current_state;
  const getState = () => currentState;

  const gateBanner = el("div", { class: "form-banner hidden", role: "alert" });

  const openForEditing = el("button", {
    type: "button",
    class: "btn btn-secondary",
    text: "Open for editing",
    disabled: currentState !== "briefing_ready",
    onclick: async () => {
      gateBanner.classList.add("hidden");
      try {
        await api.openForEditing(id);
        toast("Opened for editing.");
        render(container, { id });
      } catch (err) {
        if (err instanceof ApiError) {
          if (err.status === 401) { navigate("#/login"); return; }
          gateBanner.textContent = formatDetail(err.detail
            ?? `Open failed (HTTP ${err.status}).`);
        } else {
          gateBanner.textContent = "Open failed \u2014 unexpected error.";
        }
        gateBanner.classList.remove("hidden");
      }
    },
  });

  const approve = el("button", {
    type: "button",
    class: "btn btn-primary",
    text: "Approve & Export Package",
    disabled: !(currentState === "briefing_ready"
                || currentState === "user_editing"),
    onclick: async () => {
      gateBanner.classList.add("hidden");
      try {
        await api.approveJob(id);
        toast("Briefing approved.");
        navigate(`#/jobs/${encodeURIComponent(id)}`);
      } catch (err) {
        if (err instanceof ApiError) {
          if (err.status === 401) { navigate("#/login"); return; }
          gateBanner.textContent = formatDetail(err.detail
            ?? `Approve failed (HTTP ${err.status}).`);
        } else {
          gateBanner.textContent = "Approve failed \u2014 unexpected error.";
        }
        gateBanner.classList.remove("hidden");
      }
    },
  });

  const failRegen = el("button", {
    type: "button",
    class: "btn btn-plain",
    text: "Mark regeneration failed",
    disabled: currentState !== "regenerating_section",
    onclick: async () => {
      gateBanner.classList.add("hidden");
      const details = window.prompt(
        "Reason / details for the regeneration failure:");
      if (details === null) return;
      const trimmed = details.trim();
      if (!trimmed) return;
      try {
        await api.failRegeneration(id, "crashed", trimmed);
        toast("Marked regeneration failed.");
        render(container, { id });
      } catch (err) {
        if (err instanceof ApiError) {
          if (err.status === 401) { navigate("#/login"); return; }
          gateBanner.textContent = formatDetail(err.detail
            ?? `Fail call failed (HTTP ${err.status}).`);
        } else {
          gateBanner.textContent = "Fail call failed \u2014 unexpected error.";
        }
        gateBanner.classList.remove("hidden");
      }
    },
  });

  // ---------------------------------------------------------------------------
  // Page header — eyebrow, title, subtitle, CTA strip.
  // ---------------------------------------------------------------------------

  const pageHeader = el("div", { class: "page-header br-page-header" }, [
    el("div", { class: "br-header-left" }, [
      el("p", { class: "br-page-eyebrow", text: "STAGE 3: BRIEFING REVIEW" }),
      el("h2", { class: "page-title",
        text: "Review Corporate Briefing Document",
      }),
      el("p", { class: "page-subtitle",
        text: "Edit generated sections, inspect gaps and sources, then approve the briefing for document generation.",
      }),
      el("p", { class: "br-header-meta" }, [
        el("span", { class: "br-meta-company", text: briefing.company_name }),
        el("span", { class: "br-meta-sep", text: " \u00b7 " }),
        el("span", { class: "muted", text: `Job ${id}` }),
        el("span", { class: "br-meta-sep", text: " \u00b7 " }),
        el("a", {
          href: `#/jobs/${encodeURIComponent(id)}`,
          text: "\u2190 back to job status",
        }),
        el("span", { class: "br-meta-sep", text: " \u00b7 " }),
        el("span", { class: "muted", text: `State: ${currentState}` }),
      ]),
    ]),
    el("div", { class: "br-header-right" }, [
      gateBanner,
      el("div", { class: "row-actions" }, [approve, openForEditing, failRegen]),
      el("p", { class: "form-help br-watermark-note" }, [
        el("strong", { text: "Watermark: " }),
        el("span", { text:
          "Every generated document is marked INTERNAL DRAFT. The "
          + "approval gate here is the only place a briefing changes "
          + "before Stage 2 reads it." }),
      ]),
    ]),
  ]);

  // ---------------------------------------------------------------------------
  // Sidebar — section navigation anchors.
  // ---------------------------------------------------------------------------

  const navItems = [];
  navItems.push(el("a", {
    class: "br-section-nav-item",
    href: "#br-section-user_context",
    text: "Operator Notes",
  }));
  for (let i = 0; i < EDITABLE_SECTIONS.length; i++) {
    const sec = EDITABLE_SECTIONS[i];
    const cls = i === 0
      ? "br-section-nav-item br-section-nav-item--active"
      : "br-section-nav-item";
    navItems.push(el("a", {
      class: cls,
      href: `#br-section-${sec}`,
      text: _displayLabel(sec),
    }));
  }
  navItems.push(el("a", {
    class: "br-section-nav-item",
    href: "#br-section-sources",
    text: "Sources (read-only)",
  }));

  const sidebar = el("div", { class: "br-sidebar" }, [
    el("h3", { class: "br-sidebar-heading", text: "Briefing Sections" }),
    el("p", { class: "br-sidebar-helper", text: "Select a section to review" }),
    el("nav", { class: "br-section-nav", "aria-label": "Briefing sections" },
       navItems),
  ]);

  // ---------------------------------------------------------------------------
  // Centre column — section editors.
  // ---------------------------------------------------------------------------

  const editorChildren = [];

  // user_context — free-text operator notes.
  const ucBanner = el("div", { class: "form-banner hidden", role: "alert" });
  const ucTextarea = el("textarea", {
    class: "br-editor-textarea section-json",
    rows: 4,
    spellcheck: "true",
  });
  ucTextarea.value = briefing.user_context ?? "";
  const ucSave = el("button", {
    type: "button",
    class: "btn btn-secondary",
    text: "Save Draft Changes",
    onclick: async () => {
      ucBanner.classList.add("hidden");
      try {
        await api.patchBriefing(id, { user_context: ucTextarea.value });
        toast("Saved user_context.");
        render(container, { id });
      } catch (err) {
        if (err instanceof ApiError) {
          if (err.status === 401) { navigate("#/login"); return; }
          ucBanner.textContent = formatDetail(err.detail
            ?? `Save failed (HTTP ${err.status}).`);
        } else {
          ucBanner.textContent = "Save failed \u2014 unexpected error.";
        }
        ucBanner.classList.remove("hidden");
      }
    },
  });
  editorChildren.push(el("section", {
    id: "br-section-user_context",
    class: "br-editor-card briefing-section",
    "data-section": "user_context",
  }, [
    el("div", { class: "br-editor-header" }, [
      el("h3", { class: "br-editor-title", text: "Operator Notes" }),
      el("span", { class: "br-editable-badge", text: "EDITABLE MODE" }),
    ]),
    ucBanner,
    ucTextarea,
    el("div", { class: "br-editor-footer row-actions" }, [ucSave]),
  ]));

  // Five structured sections.
  for (const section of EDITABLE_SECTIONS) {
    editorChildren.push(_sectionEditor(id, section, briefing[section], getState));
  }

  // Sources — read-only.
  editorChildren.push(el("section", {
    id: "br-section-sources",
    class: "br-editor-card briefing-section sources",
  }, [
    el("div", { class: "br-editor-header" }, [
      el("h3", { class: "br-editor-title", text: "sources (read-only)" }),
    ]),
    el("p", { class: "form-help", text:
      "Editing the source register here would orphan source_indices "
      + "in other sections. Patch via the API directly if you must." }),
    el("pre", { class: "detail-pre",
                text: JSON.stringify(briefing.sources, null, 2) }),
  ]));

  const editorColumn = el("div", { class: "br-editor-column" }, editorChildren);

  // ---------------------------------------------------------------------------
  // Right panels — contextual intelligence.
  // Honest fallback: no real checklist/gap/citation data exists in the
  // section schema at this step, so "Not available for this section." is
  // shown rather than invented data.
  // ---------------------------------------------------------------------------

  const panels = el("div", { class: "br-panels" }, [
    el("div", { class: "br-panel card" }, [
      el("div", { class: "card-header" }, [
        el("span", { class: "br-panel-heading", text: "Section Checklist" }),
      ]),
      el("div", { class: "card-body" }, [
        el("p", { class: "br-panel-muted",
          text: "Not available for this section.",
        }),
      ]),
    ]),
    el("div", { class: "br-panel card" }, [
      el("div", { class: "card-header" }, [
        el("span", { class: "br-panel-heading br-panel-heading--warning",
          text: "Identified Gaps",
        }),
      ]),
      el("div", { class: "card-body" }, [
        el("p", { class: "br-panel-muted",
          text: "Not available for this section.",
        }),
      ]),
    ]),
    el("div", { class: "br-panel card" }, [
      el("div", { class: "card-header" }, [
        el("span", { class: "br-panel-heading", text: "Verified Citations" }),
      ]),
      el("div", { class: "card-body" }, [
        el("p", { class: "br-panel-muted",
          text: "Not available for this section.",
        }),
      ]),
    ]),
  ]);

  // ---------------------------------------------------------------------------
  // Assemble final layout.
  // ---------------------------------------------------------------------------

  const layout = el("div", { class: "br-layout" }, [
    sidebar,
    editorColumn,
    panels,
  ]);

  container.appendChild(el("div", { class: "page br-page" }, [
    pageHeader,
    layout,
  ]));
}
