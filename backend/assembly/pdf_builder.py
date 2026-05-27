"""PDF document builder using WeasyPrint + Jinja2 templates.

Renders briefing.html, benefits.html, and faq.html templates.
All PDFs are watermarked:
  - Footer: "INTERNAL DRAFT — {company} — {date} — Not for external distribution"
    (8pt, grey #666, every page via WeasyPrint CSS)
  - Background: "DRAFT" repeating diagonal, low opacity, -45deg, behind content.
All outputs are light-themed regardless of app theme.

Implemented in step 11 (Sonnet — deterministic Python, fully specified).
"""
# TODO: implement in step 11
