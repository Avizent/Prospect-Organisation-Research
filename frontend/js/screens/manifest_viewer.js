/**
 * Manifest viewer screen (step 34) — read-only provenance panel.
 *
 * Hash route:  ``#/jobs/<id>/manifest``
 *
 * Fetches ``GET /api/jobs/{id}/manifest`` and renders the Step 29
 * document_manifest.json as a structured panel. The screen is strictly
 * read-only:
 *
 *   - no edit controls,
 *   - no document-generation buttons,
 *   - no Stage 2 run trigger,
 *   - no auto-regeneration on missing manifest (a 404 just renders an
 *     empty state with a link back to the job status screen).
 *
 * The screen MUST never POST to ``/brief/assemble`` or any other route —
 * see ``tests/frontend/test_manifest_viewer_screen.py``'s
 * ``test_manifest_viewer_never_calls_assemble_route``. Assembly is an
 * explicit operator action triggered from the inspector; the manifest
 * viewer only displays what the assembler has already written.
 *
 * UX
 * --
 *   Header strip — title + Job ID + back-link + "View Brief" sibling.
 *   Summary block — generated_at, schema_version, company name/URL,
 *                   markdown filename, byte length, critic verdict.
 *   markdown_sha256 — rendered FULL (no truncation) in a <code> block
 *                     so operators can compare it by eye.
 *   Outputs — one line per ``{key, filename}`` pair.
 *   Artefacts — one line per ``{key: filename}`` pair (input files).
 *   Sections — one line per section the assembler emitted.
 *   Warnings — list, or "(no warnings)" when empty.
 *   "Open raw JSON" link — secondary link to the JSON route for the
 *                          rare case an operator wants the unformatted
 *                          file.
 *
 * Error states:
 *
 *   - 401 → navigate to ``#/login``.
 *   - 404 → "manifest not yet produced" empty state.
 *   - 500 with ``detail.reason === "manifest_corrupt"`` → dedicated
 *           banner explaining the file exists but is unreadable.
 *   - other → form-banner with the HTTP status and detail.
 */

import { api, ApiError } from "../api.js";
import { clear, el, formatDetail, navigate } from "../util.js";


function _backLink(jobId) {
  return el("a", {
    href: `#/jobs/${encodeURIComponent(jobId)}`,
    text: "Back to job status",
  });
}


function _renderNotYetProduced(container, jobId) {
  clear(container);
  container.appendChild(el("section", { class: "manifest-empty" }, [
    el("h2", { text: "Manifest not yet produced" }),
    el("p", {
      class: "muted",
      text: (
        "There is no document_manifest.json on disk for this job. "
        + "The manifest is written by the local assembler alongside "
        + "prospect_brief.md after Stage 2 completes."
      ),
    }),
    el("p", {}, [_backLink(jobId)]),
  ]));
}


function _renderCorrupt(container, jobId) {
  clear(container);
  container.appendChild(el("section", { class: "manifest-empty" }, [
    el("h2", { text: "Manifest unreadable" }),
    el("p", {
      class: "muted",
      text: (
        "document_manifest.json exists on disk but could not be parsed "
        + "as JSON. The file is corrupt — re-run the assembler from the "
        + "job inspector to rebuild it."
      ),
    }),
    el("p", {}, [_backLink(jobId)]),
  ]));
}


function _renderError(container, jobId, err) {
  clear(container);
  const message = err instanceof ApiError
    ? `Could not load manifest (HTTP ${err.status}).`
    : `Could not load manifest: ${err && err.message ? err.message : String(err)}`;
  container.appendChild(el("div", { class: "form-banner", role: "alert" }, [
    el("p", { text: message }),
    el("pre", {
      class: "detail-pre",
      text: err instanceof ApiError ? formatDetail(err.detail) : "",
    }),
    el("p", {}, [_backLink(jobId)]),
  ]));
}


function _row(label, value) {
  return el("p", {}, [
    el("strong", { text: `${label}: ` }),
    el("span", { text: value === null || value === undefined ? "—" : String(value) }),
  ]);
}


function _renderSummary(manifest) {
  // Every value goes through textContent (via the el() helper), so
  // a tampered manifest cannot pivot a string into script execution.
  return el("section", { class: "manifest-summary" }, [
    el("h3", { text: "Summary" }),
    _row("Schema version", manifest.schema_version),
    _row("Generated at", manifest.generated_at),
    _row("Company", manifest.company_name),
    _row("Company URL", manifest.company_url),
    _row("Markdown filename", manifest.markdown_filename),
    _row("Markdown byte length", manifest.markdown_byte_length),
    _row("Critic verdict", manifest.critic_verdict),
    el("p", {}, [
      el("strong", { text: "Markdown SHA-256: " }),
      // Step 34 plan decision: render the FULL hash, no truncation, so
      // operators can verify integrity by eye. Wrapped in <code> for
      // monospace presentation.
      el("code", {
        class: "manifest-sha",
        text: manifest.markdown_sha256 || "—",
      }),
    ]),
  ]);
}


function _renderOutputs(manifest) {
  const wrap = el("section", { class: "manifest-outputs" }, [
    el("h3", { text: "Outputs" }),
  ]);
  const outputs = Array.isArray(manifest.outputs) ? manifest.outputs : [];
  if (outputs.length === 0) {
    wrap.appendChild(el("p", { class: "muted", text: "(no outputs)" }));
    return wrap;
  }
  const ul = el("ul", { class: "manifest-outputs-list" });
  for (const output of outputs) {
    const key = output && output.key !== undefined ? String(output.key) : "?";
    const filename = output && output.filename !== undefined
      ? String(output.filename) : "?";
    ul.appendChild(el("li", {}, [
      el("strong", { text: `${key}: ` }),
      el("span", { text: filename }),
    ]));
  }
  wrap.appendChild(ul);
  return wrap;
}


function _renderArtefacts(manifest) {
  const wrap = el("section", { class: "manifest-artefacts" }, [
    el("h3", { text: "Input artefacts" }),
  ]);
  const artefacts = manifest.artefacts && typeof manifest.artefacts === "object"
    ? manifest.artefacts : {};
  const entries = Object.entries(artefacts);
  if (entries.length === 0) {
    wrap.appendChild(el("p", { class: "muted", text: "(no artefacts recorded)" }));
    return wrap;
  }
  const ul = el("ul", { class: "manifest-artefacts-list" });
  for (const [key, filename] of entries) {
    ul.appendChild(el("li", {}, [
      el("strong", { text: `${key}: ` }),
      el("span", { text: String(filename) }),
    ]));
  }
  wrap.appendChild(ul);
  return wrap;
}


function _renderSections(manifest) {
  const wrap = el("section", { class: "manifest-sections" }, [
    el("h3", { text: "Sections" }),
  ]);
  const sections = Array.isArray(manifest.sections) ? manifest.sections : [];
  if (sections.length === 0) {
    wrap.appendChild(el("p", { class: "muted", text: "(no sections recorded)" }));
    return wrap;
  }
  const ul = el("ul", { class: "manifest-sections-list" });
  for (const section of sections) {
    const key = section && section.key !== undefined
      ? String(section.key) : "?";
    const heading = section && section.heading !== undefined
      ? String(section.heading) : "";
    const bytes = section && section.byte_length !== undefined
      ? `(${section.byte_length} bytes)` : "";
    const parts = [el("strong", { text: `${key}` })];
    if (heading) parts.push(el("span", { text: ` — ${heading}` }));
    if (bytes) parts.push(el("span", { class: "muted", text: ` ${bytes}` }));
    ul.appendChild(el("li", {}, parts));
  }
  wrap.appendChild(ul);
  return wrap;
}


function _renderWarnings(manifest) {
  const wrap = el("section", { class: "manifest-warnings" }, [
    el("h3", { text: "Warnings" }),
  ]);
  const warnings = Array.isArray(manifest.warnings) ? manifest.warnings : [];
  if (warnings.length === 0) {
    wrap.appendChild(el("p", { class: "muted", text: "(no warnings)" }));
    return wrap;
  }
  const ul = el("ul", { class: "manifest-warnings-list" });
  for (const warning of warnings) {
    ul.appendChild(el("li", { text: String(warning) }));
  }
  wrap.appendChild(ul);
  return wrap;
}


export async function render(container, params) {
  const id = params.id;
  clear(container);
  container.appendChild(el("p", { text: "Loading manifest…" }));

  let payload;
  try {
    payload = await api.getDocumentManifest(id);
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      navigate("#/login");
      return;
    }
    if (err instanceof ApiError && err.status === 404) {
      _renderNotYetProduced(container, id);
      return;
    }
    if (err instanceof ApiError && err.status === 500) {
      const reason = err.detail && typeof err.detail === "object"
        ? err.detail.reason : null;
      if (reason === "manifest_corrupt") {
        _renderCorrupt(container, id);
        return;
      }
    }
    _renderError(container, id, err);
    return;
  }

  const manifest = (payload && typeof payload.manifest === "object"
                    && payload.manifest !== null)
    ? payload.manifest
    : {};

  clear(container);

  // Header strip — keep the operator anchored to the job they are
  // viewing the manifest for, with a sibling link to the brief viewer.
  container.appendChild(el("section", { class: "manifest-header" }, [
    el("h2", { text: "Document manifest" }),
    el("p", { class: "muted", text: `Job ID: ${id}` }),
    el("p", {}, [
      _backLink(id),
      el("span", { text: " · " }),
      el("a", {
        href: `#/jobs/${encodeURIComponent(id)}/brief`,
        text: "View Brief",
      }),
      el("span", { text: " · " }),
      // Secondary link — operators who want the raw JSON file can grab
      // it without leaving the inspector. Opens in a new tab.
      el("a", {
        href: `/api/jobs/${encodeURIComponent(id)}/manifest`,
        target: "_blank",
        rel: "noopener noreferrer",
        text: "Open raw JSON",
      }),
    ]),
  ]));

  container.appendChild(_renderSummary(manifest));
  container.appendChild(_renderOutputs(manifest));
  container.appendChild(_renderArtefacts(manifest));
  container.appendChild(_renderSections(manifest));
  container.appendChild(_renderWarnings(manifest));
}
