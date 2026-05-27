"""Logo processing pipeline.

Accepts: PNG, JPG, JPEG, SVG, WebP, GIF, BMP, TIFF, PDF, EPS, AI.
Stages:
  1. Format detection
  2. Format-specific decode (Pillow / cairosvg / pdf2image / Ghostscript subprocess)
  3. RGBA normalisation
  4. Whitespace trim via alpha bounding box
  5. Resolution check (warn if either axis <150px)
  6. Resize to two variants preserving aspect:
       header 120px  → jobs/{job_id}/logo_header.png
       large  300px  → jobs/{job_id}/logo_large.png
  7. Save outputs

Startup probe checks for Ghostscript and Poppler binaries; if missing,
disables corresponding formats in frontend file picker.
Rejects files >20MB or <50×50px.

Implemented in step 12 (Sonnet).
"""
# TODO: implement in step 12
