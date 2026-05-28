/**
 * ANS Prospect Tool — safe Markdown renderer (step 31).
 *
 * Renders a deliberately narrow subset of GitHub-flavoured Markdown
 * that ``backend/assembly/markdown.py`` actually emits:
 *
 *   - ATX headings  ``# … ######``                → <h2>…<h7>
 *   - Paragraphs    (blank-line separated)        → <p>
 *   - Unordered list rows ``- …`` (one level)     → <ul><li>
 *   - Blockquotes   ``> …``                       → <blockquote>
 *   - Code fences   ```` ``` `` … ``` ````          → <pre>
 *   - Pipe tables   ``| a | b |``                 → <table>
 *
 * Inline formatting:
 *   - ``**bold**``                                → <strong>
 *   - autolinks ``<https://example>``             → <a>
 *   - everything else is plain text
 *
 * Security
 * --------
 * We never set raw HTML on any node. Every text fragment reaches the
 * DOM via ``document.createTextNode`` / the ``text:`` channel of
 * ``el()``. Inline formatting is implemented by *splitting* the source
 * string on a regex and creating one DOM node per fragment — there is
 * no parsing step that could be tricked into emitting raw HTML.
 *
 * Autolinks accept only ``http://`` and ``https://`` URLs; anything
 * else is rendered as text. The ``rel="noopener noreferrer"`` +
 * ``target="_blank"`` combo matches the rest of the app.
 *
 * Anything the renderer does not recognise falls through as a plain
 * paragraph — a future change to ``backend/assembly/markdown.py`` that
 * introduces a new construct will appear as readable text rather than
 * an empty section.
 */

import { el } from "./util.js";

// Headings ``# ``…``###### ``.
const _HEADING_RE = /^(#{1,6})\s+(.*)$/;
// Single-level unordered list row.
const _LIST_RE = /^[-*]\s+(.+)$/;
// Blockquote line — optional space after ``>``.
const _BLOCKQUOTE_RE = /^>\s?(.*)$/;
// Code fence open/close — three+ backticks, optional info string.
const _FENCE_RE = /^```/;
// Pipe-table row — must start AND end with a ``|`` after trimming.
const _TABLE_ROW_RE = /^\|.*\|\s*$/;
// Table separator row, e.g. ``| --- | :---: |``.
const _TABLE_SEP_RE = /^\|\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/;

// Inline split: ``**bold**`` or ``<https://...>`` autolink.
const _INLINE_SPLIT_RE =
  /(\*\*[^*]+\*\*|<https?:\/\/[^>\s]+>)/g;
const _BOLD_RE = /^\*\*([^*]+)\*\*$/;
const _AUTOLINK_RE = /^<(https?:\/\/[^>\s]+)>$/;

function _splitTableCells(line) {
  // Strip the leading/trailing pipes and split on remaining pipes.
  // We don't honour escaped pipes (``\|``) at the renderer — the
  // assembler escapes pipes inside cells, so an escaped pipe is rare,
  // and the worst case is one extra column rather than HTML injection.
  let inner = line.trim();
  if (inner.startsWith("|")) inner = inner.slice(1);
  if (inner.endsWith("|")) inner = inner.slice(0, -1);
  return inner.split("|").map((cell) => cell.trim());
}

function _renderInline(target, text) {
  // Walk the inline-split regex and emit one node per fragment.
  // Fragments matching ``**...**`` become <strong>; fragments matching
  // ``<https://...>`` become <a>. Everything else becomes a text node.
  const parts = text.split(_INLINE_SPLIT_RE);
  for (const part of parts) {
    if (part === "") continue;
    const boldMatch = _BOLD_RE.exec(part);
    if (boldMatch) {
      target.appendChild(el("strong", { text: boldMatch[1] }));
      continue;
    }
    const linkMatch = _AUTOLINK_RE.exec(part);
    if (linkMatch) {
      const href = linkMatch[1];
      target.appendChild(el("a", {
        href, target: "_blank", rel: "noopener noreferrer", text: href,
      }));
      continue;
    }
    target.appendChild(document.createTextNode(part));
  }
}

/**
 * Render Markdown ``source`` into a fresh <div> and return it.
 *
 * The returned node has class ``markdown-body``. Callers may append it
 * directly to the page container.
 */
export function renderMarkdown(source) {
  const root = el("div", { class: "markdown-body" });
  const lines = (source || "").split(/\r?\n/);

  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    // Blank line — paragraph separator.
    if (trimmed === "") { i += 1; continue; }

    // Code fence — collect raw text until the matching closing fence.
    if (_FENCE_RE.test(trimmed)) {
      const codeLines = [];
      i += 1;
      while (i < lines.length && !_FENCE_RE.test(lines[i].trim())) {
        codeLines.push(lines[i]);
        i += 1;
      }
      // Skip the closing fence (if present).
      if (i < lines.length) i += 1;
      root.appendChild(el("pre", { class: "md-code",
                                   text: codeLines.join("\n") }));
      continue;
    }

    // ATX heading.
    const headingMatch = _HEADING_RE.exec(trimmed);
    if (headingMatch) {
      const level = headingMatch[1].length;
      // Offset by one so a top-level # in the brief does not collide
      // with the page's existing <h1>/<h2>.
      const tag = "h" + Math.min(level + 1, 6);
      const node = el(tag, {});
      _renderInline(node, headingMatch[2]);
      root.appendChild(node);
      i += 1;
      continue;
    }

    // Blockquote — gather contiguous ``> `` lines.
    if (_BLOCKQUOTE_RE.test(trimmed)) {
      const quoteLines = [];
      while (i < lines.length && _BLOCKQUOTE_RE.test(lines[i].trim())) {
        quoteLines.push(_BLOCKQUOTE_RE.exec(lines[i].trim())[1]);
        i += 1;
      }
      const node = el("blockquote", { class: "md-quote" });
      _renderInline(node, quoteLines.join(" "));
      root.appendChild(node);
      continue;
    }

    // Unordered list — gather contiguous ``- `` lines.
    if (_LIST_RE.test(trimmed)) {
      const ul = el("ul", { class: "md-list" });
      while (i < lines.length && _LIST_RE.test(lines[i].trim())) {
        const itemText = _LIST_RE.exec(lines[i].trim())[1];
        const li = el("li", {});
        _renderInline(li, itemText);
        ul.appendChild(li);
        i += 1;
      }
      root.appendChild(ul);
      continue;
    }

    // Pipe table — header row + separator + body rows. Treat as a
    // table only when the next line is a separator row; otherwise fall
    // through to a paragraph so a stray pipe in prose does not break
    // the layout.
    if (
      _TABLE_ROW_RE.test(trimmed)
      && i + 1 < lines.length
      && _TABLE_SEP_RE.test(lines[i + 1].trim())
    ) {
      const headerCells = _splitTableCells(trimmed);
      i += 2; // skip header + separator
      const bodyRows = [];
      while (
        i < lines.length
        && _TABLE_ROW_RE.test(lines[i].trim())
        && !_TABLE_SEP_RE.test(lines[i].trim())
      ) {
        bodyRows.push(_splitTableCells(lines[i].trim()));
        i += 1;
      }
      const table = el("table", { class: "md-table" });
      const thead = el("thead", {});
      const headerRow = el("tr", {});
      for (const cell of headerCells) {
        const th = el("th", {});
        _renderInline(th, cell);
        headerRow.appendChild(th);
      }
      thead.appendChild(headerRow);
      table.appendChild(thead);
      const tbody = el("tbody", {});
      for (const row of bodyRows) {
        const tr = el("tr", {});
        for (const cell of row) {
          const td = el("td", {});
          _renderInline(td, cell);
          tr.appendChild(td);
        }
        tbody.appendChild(tr);
      }
      table.appendChild(tbody);
      root.appendChild(table);
      continue;
    }

    // Default — paragraph. Gather contiguous non-blank, non-special
    // lines into a single <p>.
    const paraLines = [];
    while (i < lines.length) {
      const l = lines[i].trim();
      if (
        l === ""
        || _HEADING_RE.test(l)
        || _LIST_RE.test(l)
        || _BLOCKQUOTE_RE.test(l)
        || _FENCE_RE.test(l)
        || _TABLE_ROW_RE.test(l)
      ) break;
      paraLines.push(l);
      i += 1;
    }
    if (paraLines.length > 0) {
      const p = el("p", {});
      _renderInline(p, paraLines.join(" "));
      root.appendChild(p);
    }
  }

  return root;
}
