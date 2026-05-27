/**
 * Briefing inspector — GET /api/jobs/{id}/briefing plus the approval
 * gate (open-for-editing, save section, approve, request/complete/
 * fail regeneration).
 *
 * The five editable sections (snapshot, business_context, it_landscape,
 * key_people, opportunity) are presented as JSON textareas. The
 * operator edits the JSON, hits "Save section", and the inspector
 * POSTs a BriefingPatch with just that section. Validation errors
 * from the backend (422 with the pydantic error list) are rendered
 * inline so a broken source_indices reference points at the right
 * field.
 *
 * Sources are read-only here — editing the source register in step 12
 * would orphan source_indices in other sections. That gate lifts in
 * a later step when the source-register editor lands.
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

function _sectionEditor(id, sectionName, value, getState) {
  const banner = el("div", { class: "form-banner hidden", role: "alert" });
  const textarea = el("textarea", {
    class: "section-json",
    rows: 14,
    spellcheck: "false",
  });
  textarea.value = JSON.stringify(value ?? null, null, 2);

  const regenInput = el("textarea", {
    class: "regen-instructions",
    rows: 3,
    placeholder: "Rewrite instructions for the regeneration agent…",
  });

  const save = el("button", {
    type: "button",
    class: "btn btn-secondary",
    text: "Save section",
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
          banner.textContent = "Save failed — unexpected error.";
        }
        banner.classList.remove("hidden");
      }
    },
  });

  const requestRegen = el("button", {
    type: "button",
    class: "btn btn-plain",
    text: "Request regeneration",
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
          banner.textContent = "Request failed — unexpected error.";
        }
        banner.classList.remove("hidden");
      }
    },
  });

  return el("section", { class: "briefing-section",
                         "data-section": sectionName }, [
    el("h3", { text: sectionName }),
    banner,
    textarea,
    el("div", { class: "row-actions" }, [save, requestRegen]),
    el("details", { class: "regen-details" }, [
      el("summary", { text: "Regeneration instructions" }),
      regenInput,
    ]),
  ]);
}

export async function render(container, params) {
  const id = params.id;
  clear(container);
  container.appendChild(el("p", { text: "Loading briefing…" }));

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

  const stateBadge = el("p", {}, [
    el("span", { text: "Current state: " }),
    el("strong", { text: currentState }),
  ]);

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
          gateBanner.textContent = "Open failed — unexpected error.";
        }
        gateBanner.classList.remove("hidden");
      }
    },
  });

  const approve = el("button", {
    type: "button",
    class: "btn btn-primary",
    text: "Approve briefing",
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
          gateBanner.textContent = "Approve failed — unexpected error.";
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
          gateBanner.textContent = "Fail call failed — unexpected error.";
        }
        gateBanner.classList.remove("hidden");
      }
    },
  });

  container.appendChild(el("section", { class: "briefing-header" }, [
    el("h2", { text: `Briefing — ${briefing.company_name}` }),
    el("p", { class: "muted",
              text: `Job ${id} · compiled ${briefing.compiled_at}` }),
    stateBadge,
    el("p", {}, [
      el("a", {
        href: `#/jobs/${encodeURIComponent(id)}`,
        text: "← back to job status",
      }),
    ]),
    gateBanner,
    el("div", { class: "row-actions" }, [openForEditing, approve, failRegen]),
    el("p", { class: "form-help" }, [
      el("strong", { text: "Watermark: " }),
      el("span", { text:
        "Every generated document is marked INTERNAL DRAFT. The "
        + "approval gate here is the only place a briefing changes "
        + "before Stage 2 reads it." }),
    ]),
  ]));

  // User-context (free-text) editor — part of BriefingPatch but not a
  // section enum value. Patch independently.
  const ucBanner = el("div", { class: "form-banner hidden", role: "alert" });
  const ucTextarea = el("textarea", {
    class: "section-json", rows: 4, spellcheck: "true",
  });
  ucTextarea.value = briefing.user_context ?? "";
  const ucSave = el("button", {
    type: "button",
    class: "btn btn-secondary",
    text: "Save user context",
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
          ucBanner.textContent = "Save failed — unexpected error.";
        }
        ucBanner.classList.remove("hidden");
      }
    },
  });
  container.appendChild(el("section", { class: "briefing-section",
                                        "data-section": "user_context" }, [
    el("h3", { text: "user_context (operator notes)" }),
    ucBanner,
    ucTextarea,
    el("div", { class: "row-actions" }, [ucSave]),
  ]));

  for (const section of EDITABLE_SECTIONS) {
    container.appendChild(
      _sectionEditor(id, section, briefing[section], getState));
  }

  // Sources are read-only in step 12.
  container.appendChild(el("section", { class: "briefing-section sources" }, [
    el("h3", { text: "sources (read-only)" }),
    el("p", { class: "form-help", text:
      "Editing the source register here would orphan source_indices "
      + "in other sections. Patch via the API directly if you must." }),
    el("pre", { class: "detail-pre",
                text: JSON.stringify(briefing.sources, null, 2) }),
  ]));
}
