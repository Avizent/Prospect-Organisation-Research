"""Output-parsing helpers shared by every agent.

Two responsibilities live here:

1. :class:`AgentOutputInvalid` — the *non-trap* exception raised when an
   agent's JSON response cannot be parsed or fails Pydantic validation
   after the wrapper's single retry. It is deliberately **not** a
   subclass of :class:`backend.cost_control.exceptions.RunawayTrapFired`
   because output-shape failures are not runaway events: the cost-control
   layer has already done its work (the call was costed, audited, and
   pre/post-call traps all passed). The orchestrator catches this
   separately from trap exceptions and decides per-job whether to fail
   or degrade.

2. Pure helpers — :func:`extract_text` pulls the model's text out of an
   SDK response (or a dict-shaped test fake), and
   :func:`extract_json_block` finds the JSON payload in that text. Both
   are pure functions so they're trivial to unit-test and impossible to
   leak prompts/responses into logs.

Nothing in this module imports the Anthropic SDK, the Keychain, or the
real :class:`CloudClient`. The import fence
(``tests/runaway/test_no_direct_anthropic_imports.py``) and the
agent-layer static test both depend on this staying true.
"""

from __future__ import annotations

import json
import re
from typing import Any


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class AgentOutputInvalid(Exception):
    """The agent produced text that could not be parsed or validated.

    Carries enough context for the orchestrator to make a per-job
    decision (fail the job vs. mark the section degraded) without
    re-parsing the message string.

    The error attributes are deliberately structured and **redacted** —
    no prompt content, no response content. ``validation_errors`` is a
    list of Pydantic-style location/message pairs (or a single decoded
    ``JSONDecodeError`` message string).
    """

    def __init__(
        self,
        reason: str,
        *,
        agent: str,
        model: str,
        attempt: int,
        validation_errors: list[Any] | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.agent = agent
        self.model = model
        self.attempt = attempt
        self.validation_errors: list[Any] = list(validation_errors or [])


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def extract_text(response: Any) -> str:
    """Concatenate every ``text`` block in a response, in order.

    Accepts both real Anthropic SDK objects (with ``response.content``
    being a list of block objects exposing ``.type`` and ``.text``) and
    dict-shaped fakes (``{"content": [{"type": "text", "text": "..."}]}``).
    Tool-use blocks and other block types are silently skipped — callers
    that care about tool_use blocks should inspect them on the raw
    response, not this string.

    Returns the empty string if no text blocks are present. That allows
    the caller's tool-loop driver (Step 6b) to distinguish "no text yet"
    (continue the loop) from "text was malformed" (parse fail).
    """
    content = getattr(response, "content", None)
    if content is None and isinstance(response, dict):
        content = response.get("content")
    if not content:
        return ""

    parts: list[str] = []
    for block in content:
        block_type = (
            block.get("type") if isinstance(block, dict)
            else getattr(block, "type", None)
        )
        if block_type != "text":
            continue
        text = (
            block.get("text") if isinstance(block, dict)
            else getattr(block, "text", None)
        )
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

# Matches a fenced JSON code block: ```json\n{...}\n```  (or ``` without
# the json language hint). The DOTALL flag lets `.*?` span newlines.
_FENCE_RE = re.compile(
    r"```(?:json)?\s*(?P<body>.*?)\s*```",
    flags=re.DOTALL | re.IGNORECASE,
)


def extract_json_block(text: str) -> Any:
    """Parse ``text`` as JSON, stripping a surrounding ```json ... ``` fence.

    Strategy, in order:

    1. If a fenced code block is present, parse its body.
    2. Otherwise, parse the entire string.
    3. As a last resort, locate the first ``{`` or ``[`` and parse from
       there to the matching end. This handles models that prefix the
       JSON with a sentence or two.

    Raises :class:`json.JSONDecodeError` on failure. The caller (the
    base agent) translates that into a one-shot retry, and on the second
    failure into :class:`AgentOutputInvalid`.
    """
    if not isinstance(text, str):
        raise json.JSONDecodeError("input is not a string", "", 0)

    fence_match = _FENCE_RE.search(text)
    if fence_match is not None:
        return json.loads(fence_match.group("body"))

    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        # Fall through to brace-locating heuristic.
        pass

    # Find the first opening brace or bracket and decode from there.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = stripped.find(opener)
        end = stripped.rfind(closer)
        if 0 <= start < end:
            return json.loads(stripped[start : end + 1])

    # Re-raise the most useful error.
    raise json.JSONDecodeError(
        "no JSON object or array found in response", stripped or "", 0
    )
