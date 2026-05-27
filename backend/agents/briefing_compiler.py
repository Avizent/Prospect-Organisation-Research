"""Briefing compiler agent — Stage 1, step 4.

Produces briefing.json with six sections:
  1. Snapshot (company info, lab maturity, why interesting to ANS)
  2. Business context (news, M&A, financials, regulatory, risks)
  3. IT & network landscape
  4. Key people
  5. ANS opportunity hypothesis (needs ranked, products, entry angle)
  6. Source register (URLs, dates, confidence, gaps array)

Each claim carries confidence: high | medium | low | inferred.
Written to jobs/{job_id}/briefing.json.

Implemented in step 7 (Sonnet).
"""
# TODO: implement in step 7
