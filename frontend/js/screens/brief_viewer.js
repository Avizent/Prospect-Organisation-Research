/**
 * Brief viewer screen (step 31) — read-only Markdown rollup.
 *
 * Hash route:  ``#/jobs/<id>/brief``
 *
 * Fetches ``GET /api/jobs/{id}/brief/markdown`` and renders the body
 * with the safe Markdown helper. The screen is read-only:
 *
 *   - no edit controls,
 *   - no document-generation buttons,
 *   - no Stage 2 run trigger, no external API wiring, no email or
 *     delivery integration.
 *
 * Error states:
 *
 *   - 401 → navigate to ``#/login`` (auth gate elsewhere will pick up).
 *   - 404 → render a "not yet assembled" empty state with a link back
 *           to the job status screen. The Step 29/30 work-up means the
 *           file is only present after assembly has run; this screen
 *           must surface that cleanly rather than show a stack trace.
 *   - other → form-banner with the HTTP status and detail.
 */

import { api, ApiError } from "../api.js";
import { clear, el, formatDetail, navigate } from "../util.js";
import { renderMarkdown } from "../markdown.js";


function _renderNotYetAssembled(container, jobId) {
  clear(container);
  container.appendChild(el("section", { class: "brief-empty" }, [
    el("h2", { text: "Brief not yet assembled" }),
    el("p", {
      class: "muted",
      text: (
        "There is no prospect_brief.md on disk for this job. "
        + "The Markdown brief is produced by the local assembler "
        + "after Stage 2 completes."
      ),
    }),
    el("p", {}, [
      el("a", {
        class: "btn btn-secondary",
        href: `#/jobs/${encodeURIComponent(jobId)}`,
        text: "Back to job status",
      }),
    ]),
  ]));
}


function _renderError(container, jobId, err) {
  clear(container);
  const message = err instanceof ApiError
    ? `Could not load brief (HTTP ${err.status}).`
    : `Could not load brief: ${err && err.message ? err.message : String(err)}`;
  container.appendChild(el("div", { class: "form-banner", role: "alert" }, [
    el("p", { text: message }),
    el("pre", {
      class: "detail-pre",
      text: err instanceof ApiError ? formatDetail(err.detail) : "",
    }),
    el("p", {}, [
      el("a", {
        href: `#/jobs/${encodeURIComponent(jobId)}`,
        text: "Back to job status",
      }),
    ]),
  ]));
}


export async function render(container, params) {
  const id = params.id;
  clear(container);
  container.appendChild(el("p", { text: "Loading brief…" }));

  let payload;
  try {
    payload = await api.getProspectBriefMarkdown(id);
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      navigate("#/login");
      return;
    }
    if (err instanceof ApiError && err.status === 404) {
      _renderNotYetAssembled(container, id);
      return;
    }
    _renderError(container, id, err);
    return;
  }

  clear(container);

  // Header strip — keep the operator anchored to the job they are
  // viewing the brief for. The page heading is the job id rather than
  // the company name because this screen does not re-fetch state.json.
  container.appendChild(el("section", { class: "brief-header" }, [
    el("h2", { text: "Prospect brief" }),
    el("p", { class: "muted", text: `Job ID: ${id}` }),
    el("p", {}, [
      el("a", {
        href: `#/jobs/${encodeURIComponent(id)}`,
        text: "Back to job status",
      }),
      // Step 34: surface the manifest viewer as a sibling navigation
      // link. The link is unconditional — if the manifest is missing
      // the viewer renders its own empty state rather than letting the
      // operator hit a dead link from the brief view.
      el("span", { text: " · " }),
      el("a", {
        href: `#/jobs/${encodeURIComponent(id)}/manifest`,
        text: "View Manifest",
      }),
    ]),
  ]));

  const markdown = (payload && typeof payload.markdown === "string")
    ? payload.markdown
    : "";
  container.appendChild(renderMarkdown(markdown));
}
