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


function _renderSummary(manifest, lifecycle) {
  // Every value goes through textContent (via the el() helper), so
  // a tampered manifest cannot pivot a string into script execution.
  const manifestSha = manifest.markdown_sha256 || "—";
  const onDiskSha = (lifecycle && lifecycle.manifest
                      && typeof lifecycle.manifest.on_disk_markdown_sha256 === "string")
    ? lifecycle.manifest.on_disk_markdown_sha256
    : null;
  const drift = !!(lifecycle && lifecycle.manifest
                    && lifecycle.manifest.source_markdown_drift);

  // The two SHA rows are visually paired so an operator can compare
  // them by eye. When they diverge we tag both with .sha-mismatch.
  const manifestShaClass = drift ? "manifest-sha sha-mismatch" : "manifest-sha";
  const onDiskShaClass = drift ? "manifest-sha sha-mismatch" : "manifest-sha";

  const rows = [
    el("h3", { text: "Summary" }),
    _row("Schema version", manifest.schema_version),
    _row("Generated at", manifest.generated_at),
    _row("Company", manifest.company_name),
    _row("Company URL", manifest.company_url),
    _row("Markdown filename", manifest.markdown_filename),
    _row("Markdown byte length", manifest.markdown_byte_length),
    _row("Critic verdict", manifest.critic_verdict),
    el("p", {}, [
      el("strong", { text: "Manifest Markdown SHA-256: " }),
      el("code", {
        class: manifestShaClass,
        text: manifestSha,
      }),
    ]),
  ];

  if (onDiskSha !== null) {
    rows.push(el("p", {}, [
      el("strong", { text: "Source Markdown SHA-256: " }),
      el("code", {
        class: onDiskShaClass,
        text: onDiskSha,
      }),
    ]));
  }

  return el("section", { class: "manifest-summary" }, rows);
}


// Amendment 1: ``source_markdown_drift`` is an error-severity
// condition. We surface it as a prominent manifest-level banner ABOVE
// every other panel so an operator sees it immediately on screen
// load. ``source_entry_drift`` is intentionally NOT surfaced here —
// it lives on the per-export stale badge inside the Exports section.
function _renderSourceDriftBanner(lifecycle) {
  if (!(lifecycle && lifecycle.manifest
        && lifecycle.manifest.source_markdown_drift)) {
    return null;
  }
  return el("div", {
    class: "manifest-banner manifest-banner-error source-markdown-drift",
    role: "alert",
  }, [
    el("p", {}, [
      el("strong", { text: "Source Markdown drift — " }),
      el("span", {
        text: (
          "the manifest's recorded markdown_sha256 no longer matches "
          + "prospect_brief.md on disk. Every downstream artefact, "
          + "including the current PDF export, may be stale. "
          + "Re-assemble the brief from the job inspector to refresh "
          + "the manifest before regenerating exports."
        ),
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


// ---------------------------------------------------------------------------
// Step 36 + 38: exports section
// ---------------------------------------------------------------------------
//
// Each export format is rendered as its own panel with two visually-
// distinct controls that never merge into a single "click here"
// affordance:
//
//   - "Generate <FORMAT>" button — POSTs ``/api/jobs/{id}/export/<format>``.
//     On a 201/200 we re-render the screen so the new ``exports[]`` entry
//     shows up alongside the download link.
//   - "Download <FORMAT>" anchor — opens
//     ``/api/jobs/{id}/exports/<format>`` directly so the browser handles
//     the stream. Only appears once an export entry exists in the
//     manifest.
//
// The two MUST be separate elements (see
// ``tests/frontend/test_manifest_viewer_exports.py``): a user clicking
// "Generate" must not silently trigger a download, and vice-versa. Per-
// format generate handlers are written inline (rather than parametrised)
// so the static fence in
// ``tests/frontend/test_manifest_viewer_lifecycle.py`` can count the
// number of per-format export-trigger references directly.

function _findExportEntry(manifest, format) {
  const exports = Array.isArray(manifest.exports) ? manifest.exports : [];
  for (const entry of exports) {
    if (entry && entry.format === format) return entry;
  }
  return null;
}


function _findEntryLifecycle(lifecycle, format) {
  if (!lifecycle || !Array.isArray(lifecycle.entries)) return null;
  for (const record of lifecycle.entries) {
    if (record && record.format === format) {
      return record.lifecycle || null;
    }
  }
  return null;
}


// Convenience wrappers — preserved as named functions so a content scan
// for ``_findPdfExportEntry`` / ``_findPdfEntryLifecycle`` still finds
// the symbol it expects.
function _findPdfExportEntry(manifest) {
  return _findExportEntry(manifest, "pdf");
}


function _findPdfEntryLifecycle(lifecycle) {
  return _findEntryLifecycle(lifecycle, "pdf");
}


function _findDocxExportEntry(manifest) {
  return _findExportEntry(manifest, "docx");
}


function _findDocxEntryLifecycle(lifecycle) {
  return _findEntryLifecycle(lifecycle, "docx");
}


function _appendEntryMeta(wrap, entry) {
  // Lightweight provenance projection so operators can verify the
  // ``regeneration_count`` and the renderer versions at a glance.
  const meta = el("ul", { class: "manifest-exports-meta" });
  meta.appendChild(el("li", {}, [
    el("strong", { text: "Filename: " }),
    el("span", { text: String(entry.filename || "—") }),
  ]));
  meta.appendChild(el("li", {}, [
    el("strong", { text: "Byte length: " }),
    el("span", { text: String(entry.byte_length ?? "—") }),
  ]));
  meta.appendChild(el("li", {}, [
    el("strong", { text: "SHA-256: " }),
    el("code", {
      class: "manifest-sha",
      text: String(entry.sha256 || "—"),
    }),
  ]));
  meta.appendChild(el("li", {}, [
    el("strong", { text: "Exporter version: " }),
    el("span", { text: String(entry.exporter_version || "—") }),
  ]));
  meta.appendChild(el("li", {}, [
    el("strong", { text: "Template version: " }),
    el("span", { text: String(entry.template_version || "—") }),
  ]));
  // Step 38 amendment 2: DOCX entries carry a ``template_sha256``
  // provenance field. We render it conditionally — PDF entries do not
  // have it, and the row stays out of the way when absent.
  if (typeof entry.template_sha256 === "string" && entry.template_sha256) {
    meta.appendChild(el("li", {}, [
      el("strong", { text: "Template SHA-256: " }),
      el("code", {
        class: "manifest-sha",
        text: String(entry.template_sha256),
      }),
    ]));
  }
  const renderer = entry.renderer && typeof entry.renderer === "object"
    ? entry.renderer : {};
  meta.appendChild(el("li", {}, [
    el("strong", { text: "Renderer: " }),
    el("span", {
      text: `${renderer.name || "—"} ${renderer.version || ""}`.trim(),
    }),
  ]));
  const md = entry.markdown_renderer && typeof entry.markdown_renderer === "object"
    ? entry.markdown_renderer : {};
  meta.appendChild(el("li", {}, [
    el("strong", { text: "Markdown renderer: " }),
    el("span", {
      text: `${md.name || "—"} ${md.version || ""}`.trim(),
    }),
  ]));
  meta.appendChild(el("li", {}, [
    el("strong", { text: "Regeneration count: " }),
    el("span", { text: String(entry.regeneration_count ?? 0) }),
  ]));
  wrap.appendChild(meta);
}


function _renderPdfPanel(container, jobId, manifest, lifecycle) {
  const wrap = el("section", { class: "manifest-export-panel manifest-export-pdf" }, [
    el("h4", { text: "PDF" }),
  ]);

  // Per-entry lifecycle lookup. Step 37 amendment 1: when the entry is
  // stale we flip the "Generate PDF" label to "Regenerate PDF" — same
  // button, same class, same handler; only the label changes.
  const entryLifecycle = _findPdfEntryLifecycle(lifecycle);
  const entryIsStale = !!(entryLifecycle && entryLifecycle.stale);
  const generateLabel = entryIsStale ? "Regenerate PDF" : "Generate PDF";

  // 1. Generate button — always present (regeneration is allowed and bumps
  //    ``regeneration_count`` on the manifest entry).
  const generateBtn = el("button", {
    type: "button",
    class: "btn-generate-pdf",
    text: generateLabel,
  });
  generateBtn.addEventListener("click", async () => {
    generateBtn.disabled = true;
    generateBtn.textContent = "Generating…";
    try {
      await api.runPdfExport(jobId);
      await render(container, { id: jobId });
    } catch (err) {
      generateBtn.disabled = false;
      generateBtn.textContent = generateLabel;
      if (err instanceof ApiError && err.status === 401) {
        navigate("#/login");
        return;
      }
      const banner = el("div", { class: "form-banner", role: "alert" }, [
        el("p", {
          text: err instanceof ApiError
            ? `PDF generation failed (HTTP ${err.status}).`
            : `PDF generation failed: ${err && err.message ? err.message : String(err)}`,
        }),
        el("pre", {
          class: "detail-pre",
          text: err instanceof ApiError ? formatDetail(err.detail) : "",
        }),
      ]);
      wrap.appendChild(banner);
    }
  });
  wrap.appendChild(el("p", {}, [generateBtn]));

  // 2. Download link — only if an entry exists.
  const entry = _findPdfExportEntry(manifest);
  if (entry) {
    if (entryIsStale) {
      const reasons = Array.isArray(entryLifecycle.reasons)
        ? entryLifecycle.reasons : [];
      wrap.appendChild(el("p", {
        class: "export-stale-badge",
        title: `Stale: ${reasons.join(", ")}`,
      }, [
        el("strong", { text: "Stale: " }),
        el("span", {
          text: (
            "this PDF was rendered from an older Markdown version. "
            + "Regenerate before sharing externally. "
            + `Reasons: ${reasons.join(", ")}.`
          ),
        }),
      ]));
    }
    const downloadLink = el("a", {
      class: "btn-download-pdf",
      href: `/api/jobs/${encodeURIComponent(jobId)}/exports/pdf`,
      target: "_blank",
      rel: "noopener noreferrer",
      text: "Download PDF",
    });
    wrap.appendChild(el("p", {}, [downloadLink]));
    _appendEntryMeta(wrap, entry);
  } else {
    wrap.appendChild(el("p", {
      class: "muted",
      text: "(no PDF generated yet)",
    }));
  }

  return wrap;
}


function _renderDocxPanel(container, jobId, manifest, lifecycle) {
  const wrap = el("section", { class: "manifest-export-panel manifest-export-docx" }, [
    el("h4", { text: "DOCX" }),
  ]);

  const entryLifecycle = _findDocxEntryLifecycle(lifecycle);
  const entryIsStale = !!(entryLifecycle && entryLifecycle.stale);
  const generateLabel = entryIsStale ? "Regenerate DOCX" : "Generate DOCX";

  const generateBtn = el("button", {
    type: "button",
    class: "btn-generate-docx",
    text: generateLabel,
  });
  generateBtn.addEventListener("click", async () => {
    generateBtn.disabled = true;
    generateBtn.textContent = "Generating…";
    try {
      await api.runDocxExport(jobId);
      await render(container, { id: jobId });
    } catch (err) {
      generateBtn.disabled = false;
      generateBtn.textContent = generateLabel;
      if (err instanceof ApiError && err.status === 401) {
        navigate("#/login");
        return;
      }
      const banner = el("div", { class: "form-banner", role: "alert" }, [
        el("p", {
          text: err instanceof ApiError
            ? `DOCX generation failed (HTTP ${err.status}).`
            : `DOCX generation failed: ${err && err.message ? err.message : String(err)}`,
        }),
        el("pre", {
          class: "detail-pre",
          text: err instanceof ApiError ? formatDetail(err.detail) : "",
        }),
      ]);
      wrap.appendChild(banner);
    }
  });
  wrap.appendChild(el("p", {}, [generateBtn]));

  const entry = _findDocxExportEntry(manifest);
  if (entry) {
    if (entryIsStale) {
      const reasons = Array.isArray(entryLifecycle.reasons)
        ? entryLifecycle.reasons : [];
      wrap.appendChild(el("p", {
        class: "export-stale-badge",
        title: `Stale: ${reasons.join(", ")}`,
      }, [
        el("strong", { text: "Stale: " }),
        el("span", {
          text: (
            "this DOCX was rendered from an older Markdown version. "
            + "Regenerate before sharing externally. "
            + `Reasons: ${reasons.join(", ")}.`
          ),
        }),
      ]));
    }
    const downloadLink = el("a", {
      class: "btn-download-docx",
      href: `/api/jobs/${encodeURIComponent(jobId)}/exports/docx`,
      target: "_blank",
      rel: "noopener noreferrer",
      text: "Download DOCX",
    });
    wrap.appendChild(el("p", {}, [downloadLink]));
    _appendEntryMeta(wrap, entry);
  } else {
    wrap.appendChild(el("p", {
      class: "muted",
      text: "(no DOCX generated yet)",
    }));
  }

  return wrap;
}


// ---------------------------------------------------------------------------
// Step 41: export history panel
// ---------------------------------------------------------------------------
//
// A read-only table of every historical export entry for each format,
// newest first. Visible only when at least one export entry exists.
// Download links point at the Step 41 historical-retrieval endpoint
// ``GET /api/jobs/{id}/exports/{format}/{export_id}`` using the
// ``download`` attribute so the browser streams the file to disk
// immediately, independent of the current "latest" alias.
//
// No state mutation. No generate/regenerate controls. No polling.
// All values go through textContent (via el()) — direct DOM assignment
// only via the textContent property, never via unsafe property assignment.

// How many hex chars to show in the abbreviated ID column.
const _EXPORT_ID_SHORT_LEN = 12;

function _shortId(exportId) {
  // Display only the first _EXPORT_ID_SHORT_LEN hex chars + "…" so the
  // table stays readable; the full 64-char hash is available in the title
  // tooltip.
  if (typeof exportId !== "string" || exportId.length < _EXPORT_ID_SHORT_LEN) {
    return exportId || "—";
  }
  return exportId.slice(0, _EXPORT_ID_SHORT_LEN) + "…";
}


function _renderExportHistoryForFormat(jobId, manifest, fmt) {
  const exports = Array.isArray(manifest.exports) ? manifest.exports : [];
  const latest_map = manifest.latest_export_id
    && typeof manifest.latest_export_id === "object"
    ? manifest.latest_export_id : {};

  // Collect entries for this format, sort newest first (version DESC).
  const entries = exports
    .filter(e => e && e.format === fmt)
    .sort((a, b) => (b.version || 0) - (a.version || 0));

  if (entries.length === 0) return null;

  const section = el("section", {
    class: `manifest-export-history manifest-export-history-${fmt}`,
  });

  const caption = fmt.toUpperCase();
  const table = el("table", { class: "export-history-table" });
  const captionEl = document.createElement("caption");
  captionEl.textContent = `Historical exports for ${caption}`;
  table.appendChild(captionEl);

  // Header row.
  const thead = el("thead", {}, [
    el("tr", {}, [
      el("th", { text: "Version" }),
      el("th", { text: "Created" }),
      el("th", { text: "File Format" }),
      el("th", { text: "Export ID" }),
      el("th", { text: "Status" }),
      el("th", { text: "Download" }),
    ]),
  ]);
  table.appendChild(thead);

  const tbody = el("tbody");
  for (const entry of entries) {
    const version = entry.version != null ? String(entry.version) : "—";
    const createdAt = typeof entry.generated_at === "string"
      ? entry.generated_at : "—";
    const exportId = typeof entry.export_id === "string"
      ? entry.export_id : (typeof entry.sha256 === "string" ? entry.sha256 : "");
    const isLatest = exportId && latest_map[fmt] === exportId;

    // Status chip.
    const chipClass = isLatest
      ? "export-history-chip export-history-chip-latest"
      : "export-history-chip export-history-chip-superseded";
    const chipText = isLatest ? "Latest" : "Superseded";
    const chip = el("span", { class: chipClass, text: chipText });

    // Version cell — bold if latest.
    const versionCell = el("td", {});
    const versionEl = isLatest
      ? el("strong", { text: version })
      : el("span", { text: version });
    versionCell.appendChild(versionEl);

    // Export ID cell with tooltip for full hash.
    const idCell = el("td", {});
    const codeEl = el("code", {
      class: "export-history-id",
      text: _shortId(exportId),
    });
    if (exportId) codeEl.title = exportId;
    idCell.appendChild(codeEl);

    // Download anchor — only if we have a valid export_id.
    const downloadCell = el("td", {});
    if (exportId) {
      const href = `/api/jobs/${encodeURIComponent(jobId)}/exports/${encodeURIComponent(fmt)}/${encodeURIComponent(exportId)}`;
      const dlLink = el("a", {
        class: `btn-download-${fmt}-historical`,
        href,
        text: `Download v${version}`,
      });
      dlLink.setAttribute("download", `prospect_brief.v${version}.${fmt}`);
      dlLink.setAttribute("aria-label", `Download ${fmt.toUpperCase()} version ${version} from ${createdAt}`);
      downloadCell.appendChild(dlLink);
    }

    const formatBadgeEl = el("span", {
      class: `export-format-badge export-format-badge--${fmt}`,
      text: fmt.toUpperCase(),
    });
    tbody.appendChild(el("tr", {}, [
      versionCell,
      el("td", { text: createdAt }),
      el("td", {}, [formatBadgeEl]),
      idCell,
      el("td", {}, [chip]),
      downloadCell,
    ]));
  }
  table.appendChild(tbody);
  section.appendChild(table);
  return section;
}


function _renderExportHistory(jobId, manifest) {
  const exports = Array.isArray(manifest.exports) ? manifest.exports : [];
  if (exports.length === 0) return null;

  const wrap = el("section", { class: "manifest-export-history-panel" }, [
    el("h3", { text: "Export history" }),
    el("p", {
      class: "muted",
      text: "Older versions remain downloadable for audit.",
    }),
  ]);

  let anyRendered = false;
  // Render in canonical format order so the panel layout is stable.
  for (const fmt of ["pdf", "docx"]) {
    const section = _renderExportHistoryForFormat(jobId, manifest, fmt);
    if (section) {
      wrap.appendChild(section);
      anyRendered = true;
    }
  }

  return anyRendered ? wrap : null;
}


function _renderExports(container, jobId, manifest, lifecycle) {
  const wrap = el("section", { class: "manifest-exports" }, [
    el("h3", { text: "Exports" }),
  ]);
  wrap.appendChild(_renderPdfPanel(container, jobId, manifest, lifecycle));
  wrap.appendChild(_renderDocxPanel(container, jobId, manifest, lifecycle));

  // Step 41: export history panel appended below the per-format
  // generate/download controls. Null when no entries exist.
  const history = _renderExportHistory(jobId, manifest);
  if (history) wrap.appendChild(history);

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


// ---------------------------------------------------------------------------
// Step 43: delivery-ready files panel
// ---------------------------------------------------------------------------
//
// A read-only panel placed near the top of the manifest viewer so the
// operator can immediately download the latest PDF and DOCX outputs
// after a one-click Generate Documents run.
//
// Design rules:
//   - Download links only (anchor elements, never POST calls).
//   - Missing-file notices with a "go generate" hint when exports absent.
//   - Plain-language instruction for manual attachment to email or CRM.
//   - No external integrations of any kind — delivery is the
//     operator's own action, not the application's.

function _renderDeliveryReadyPanel(jobId, manifest) {
  const pdfEntry = _findPdfExportEntry(manifest);
  const docxEntry = _findDocxExportEntry(manifest);

  // Hero download cards — styled primary/secondary anchors.
  const pdfCard = pdfEntry
    ? el("a", {
        class: "dr-download-card dr-download-card--primary btn-download-pdf-delivery",
        href: `/api/jobs/${encodeURIComponent(jobId)}/exports/pdf`,
        "aria-label": "Download latest PDF",
      }, [
        el("span", { class: "dr-download-icon", text: "↓" }),
        el("div", { class: "dr-download-info" }, [
          el("span", { class: "dr-download-title", text: "Download PDF Document" }),
          el("span", { class: "dr-download-sub", text: "Formatted report with styles" }),
        ]),
      ])
    : el("div", { class: "dr-download-card dr-download-card--missing" }, [
        el("span", { class: "delivery-file-missing", text: "Missing PDF" }),
      ]);

  const docxCard = docxEntry
    ? el("a", {
        class: "dr-download-card dr-download-card--secondary btn-download-docx-delivery",
        href: `/api/jobs/${encodeURIComponent(jobId)}/exports/docx`,
        "aria-label": "Download latest DOCX",
      }, [
        el("span", { class: "dr-download-icon", text: "↓" }),
        el("div", { class: "dr-download-info" }, [
          el("span", { class: "dr-download-title", text: "Download DOCX Document" }),
          el("span", { class: "dr-download-sub", text: "Microsoft Word compatible" }),
        ]),
      ])
    : el("div", { class: "dr-download-card dr-download-card--missing" }, [
        el("span", { class: "delivery-file-missing", text: "Missing DOCX" }),
      ]);

  const hero = el("div", { class: "dr-hero" }, [
    el("div", { class: "dr-check-circle", text: "\u2713" }),
    el("p", { class: "dr-hero-eyebrow", text: "INTELLIGENCE COMPLETE" }),
    el("h2", { class: "dr-hero-title", text: "Briefing Ready for Hand-off" }),
    el("p", {
      class: "dr-hero-subtitle",
      text: manifest && manifest.company_name
        ? `The sales briefing for ${manifest.company_name} is formatted, verified, and packaged.`
        : "The sales briefing documents are formatted, verified, and packaged.",
    }),
    el("div", { class: "dr-downloads" }, [pdfCard, docxCard]),
  ]);

  const children = [
    hero,
    el("h3", { text: "Delivery-ready files" }),
    el("p", {
      class: "delivery-instruction",
      text: (
        "Download these files and attach them manually "
        + "to your email or CRM."
      ),
    }),
  ];

  // Accessible fallback list — screen reader supplement to the hero cards.
  const fileList = el("ul", { class: "delivery-file-list" });
  if (pdfEntry) {
    fileList.appendChild(el("li", { class: "delivery-file-item" }, [
      el("a", {
        href: `/api/jobs/${encodeURIComponent(jobId)}/exports/pdf`,
        text: "Download latest PDF",
      }),
    ]));
  } else {
    fileList.appendChild(el("li", {
      class: "delivery-file-item delivery-file-missing",
      text: "Missing PDF",
    }));
  }
  if (docxEntry) {
    fileList.appendChild(el("li", { class: "delivery-file-item" }, [
      el("a", {
        href: `/api/jobs/${encodeURIComponent(jobId)}/exports/docx`,
        text: "Download latest DOCX",
      }),
    ]));
  } else {
    fileList.appendChild(el("li", {
      class: "delivery-file-item delivery-file-missing",
      text: "Missing DOCX",
    }));
  }
  children.push(fileList);

  // When either file is missing, direct the operator back to generate them.
  if (!pdfEntry || !docxEntry) {
    children.push(el("p", {
      class: "delivery-missing-hint",
      text: (
        "One or more files are missing. Return to Job Status "
        + "and click Generate Documents to produce them."
      ),
    }));
    children.push(el("p", {}, [
      el("a", {
        href: `#/jobs/${encodeURIComponent(jobId)}`,
        text: "Go to Job Status",
      }),
    ]));
  }

  return el("section", {
    class: "delivery-ready-panel",
    "aria-label": "Delivery-ready files",
  }, children);
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
  // Step 37: the envelope carries a sibling ``lifecycle`` projection.
  // It is read-only and computed server-side from the manifest plus
  // the on-disk Markdown; we treat absence defensively so an older
  // backend (with no lifecycle field) keeps rendering correctly.
  const lifecycle = (payload && typeof payload.lifecycle === "object"
                      && payload.lifecycle !== null)
    ? payload.lifecycle
    : { manifest: {}, entries: [] };

  clear(container);

  const pageChildren = [];

  // Header strip — keep the operator anchored to the job they are
  // viewing the manifest for, with a sibling link to the brief viewer.
  pageChildren.push(el("section", { class: "manifest-header" }, [
    el("h2", { text: "Document manifest" }),
    el("p", { class: "muted", text: `Job ID: ${id}` }),
    el("p", {}, [
      _backLink(id),
      el("span", { text: " \u00b7 " }),
      el("a", {
        href: `#/jobs/${encodeURIComponent(id)}/brief`,
        text: "View Brief",
      }),
      el("span", { text: " \u00b7 " }),
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

  // Amendment 1: prominent error banner above every panel when
  // ``source_markdown_drift`` is present.
  const driftBanner = _renderSourceDriftBanner(lifecycle);
  if (driftBanner) pageChildren.push(driftBanner);

  // Step 43: delivery-ready panel — placed near the top so the operator
  // sees the download links immediately after a Generate Documents run.
  pageChildren.push(_renderDeliveryReadyPanel(id, manifest));

  pageChildren.push(_renderSummary(manifest, lifecycle));
  pageChildren.push(_renderOutputs(manifest));
  pageChildren.push(_renderArtefacts(manifest));
  pageChildren.push(_renderSections(manifest));
  pageChildren.push(_renderExports(container, id, manifest, lifecycle));
  pageChildren.push(_renderWarnings(manifest));

  // Step 48: footer navigation actions.
  pageChildren.push(el("div", { class: "dr-footer-actions" }, [
    el("a", {
      class: "btn btn-secondary",
      href: `#/jobs/${encodeURIComponent(id)}/brief`,
      text: "Back to Briefing Review",
    }),
    el("a", {
      class: "btn btn-ghost",
      href: "#/jobs/new",
      text: "Prepare Another Job",
    }),
  ]));

  container.appendChild(el("div", { class: "page" }, pageChildren));
}
