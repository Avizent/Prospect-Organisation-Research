"""Critic agent — Stage 2, step 3.

Model: claude-haiku-4-5 (configured in config.yaml).
Reviews all three drafts against approved briefing + ANS knowledge. Flags:
  - Claims about target company not supported by briefing
  - Claims about ANS products not in knowledge base
  - Factual inconsistencies
  - Off-brand tone

Max one revision pass (Trap 6). If rejected after v2, ships v2 with warning flag.

Implemented in step 10 (Opus — subtle reasoning required).
"""
# TODO: implement in step 10
