"""Per-agent max_tokens and tool-call ceilings.

Values are drawn directly from the handover document and must not be changed
without updating tests/runaway/ accordingly.

max_tokens per agent call:
    research:          4000
    needs:             3000
    briefing_compiler: 8000
    mapping:           2000
    benefits_writer:   6000
    faq_writer:        4000
    objections_writer: 4000
    critic:            2000

Tool-call ceilings:
    research — web_search: 20, web_fetch: 10
    all other agents:        3 (any tool)
"""

MAX_TOKENS: dict[str, int] = {
    "research": 4000,
    "needs": 3000,
    "briefing_compiler": 8000,
    "mapping": 2000,
    "benefits_writer": 6000,
    "faq_writer": 4000,
    "objections_writer": 4000,
    "critic": 2000,
}

TOOL_CALL_LIMITS: dict[str, dict[str, int]] = {
    "research": {"web_search": 20, "web_fetch": 10},
}

DEFAULT_TOOL_CALL_LIMIT: int = 3
