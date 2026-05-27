"""Abstract foundation that every agent in the pipeline builds on.

Step 6a deliberately ships the wrapper *without* a tool-use loop. The
research agent's ``web_search``/``web_fetch`` loop lands in Step 6b on
top of this contract. Every other agent (needs, contact extraction,
briefing compiler, mapping, writers, critic) is text-in / JSON-out and
never sees a tool block.

Design rules (do not relax without an explicit handover update)
---------------------------------------------------------------

1. **CloudClient is injected, never constructed.** The constructor
   takes a :class:`CloudClientProtocol`. Agents never import
   :class:`backend.cost_control.cloud_client.CloudClient` directly,
   never call its ``for_production`` classmethod, never read the
   Keychain, never touch ``backend.credentials.*``. The static test
   ``tests/agents/test_no_direct_cloud_client_construction.py``
   enforces this.

2. **No agent catches** :class:`RunawayTrapFired`. Trap exceptions
   thrown by ``CloudClient.messages_create`` propagate untouched. The
   orchestrator (Step 9) has the single catch-point.

3. **Output failures are bounded.** Parse/validation failures retry
   once with a stricter "JSON only" reminder. A second failure raises
   :class:`AgentOutputInvalid`. Two retries would be plenty of room
   for a runaway cost spiral disguised as flaky output.

4. **Logs carry structure, never content.** The wrapper logs the
   agent name, model, attempt number, prompt length, and outcome.
   It does not log the user prompt, the response text, the parsed
   JSON, or the validation errors. The "no leakage" test in
   ``tests/agents/test_base_agent.py`` enforces this.

5. **Token estimate is intentionally cheap and conservative.** A
   word-count-style heuristic over the messages list. Over-estimating
   fails *safe* — Trap 3 / Trap 4 may trip a little early on tight
   budgets, but never late.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Literal, TypeVar

from pydantic import BaseModel, ValidationError

from backend.agents._protocols import CloudClientProtocol
from backend.agents.output import (
    AgentOutputInvalid,
    extract_json_block,
    extract_text,
)
from backend.agents.limits import MAX_TOKENS
from backend.cost_control.cloud_client import CloudCallResult
from backend.cost_control.config_loader import Models

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Role typing
# ---------------------------------------------------------------------------

AgentRole = Literal["writer_model", "research_model", "critic_model"]
"""Permitted ``BaseAgent.role`` values.

Each member matches an attribute of
:class:`backend.cost_control.config_loader.Models`, so the model lookup
is a single ``getattr(models, self.role)`` call with no string
construction.
"""


OutputT = TypeVar("OutputT", bound=BaseModel)


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------

#: Maximum total attempts per :meth:`BaseAgent.run` call. First attempt
#: plus exactly one retry on parse/validation failure. Each attempt is a
#: fresh ``messages_create``, so each is costed and audited independently
#: by CloudClient — the retry does not bypass any trap.
_MAX_ATTEMPTS: int = 2

#: Reminder appended to the user prompt on the retry attempt. Intentionally
#: short — the goal is to nudge the model toward strict JSON, not to give
#: it new information.
_RETRY_REMINDER: str = (
    "\n\nYour previous response could not be parsed. Return ONLY a valid "
    "JSON object matching the requested schema, with no commentary, no "
    "markdown fences, and no surrounding prose."
)


# ---------------------------------------------------------------------------
# BaseAgent
# ---------------------------------------------------------------------------

class BaseAgent(ABC):
    """Single-turn, text-in / JSON-out agent foundation.

    Subclasses declare four class-level attributes:

    ``name``
        Must be a key of :data:`backend.agents.limits.MAX_TOKENS`. This
        is the same string CloudClient uses to look up the Trap 1 / Trap 2
        ceilings — no fallback, no aliasing.
    ``role``
        One of :data:`AgentRole`. Maps to a model id via the injected
        :class:`Models` dataclass.
    ``output_model``
        A Pydantic :class:`BaseModel` subclass. ``run()`` validates the
        parsed JSON against this and returns the validated instance.

    Subclasses implement :meth:`system_prompt` and :meth:`user_prompt`.
    They do **not** override :meth:`run` for Step 6a — the tool-use loop
    that Step 6b adds is a separate override surface.
    """

    name: ClassVar[str]
    role: ClassVar[AgentRole]
    output_model: ClassVar[type[BaseModel]]

    def __init__(
        self,
        *,
        client: CloudClientProtocol,
        models: Models,
    ) -> None:
        # The CloudClient is borrowed for the duration of run(); the
        # base agent has no opinions about its lifecycle. The
        # orchestrator (Step 9) will build one per job.
        self._client = client
        self._models = models

    # ------------------------------------------------------------------
    # Prompts — subclass surface
    # ------------------------------------------------------------------

    def tools(self) -> list[dict[str, Any]] | None:
        """Return the tool list this agent is allowed to use, or ``None``.

        Default: ``None`` — the vast majority of agents (needs, contact
        extraction, briefing compiler, mapping, writers, critic) are
        text-in / JSON-out and never see a tool block. Step 6b's
        :class:`ResearchAgent` overrides this to declare its
        ``web_search`` / ``web_fetch`` server tools.

        Returning a list (even an empty one) signals "this agent uses
        tools" — the wrapper will allocate a ``tool_use_counts`` dict
        so Trap 2 (per-tool ceilings) is enforced by the cloud client.
        """
        return None

    @abstractmethod
    def system_prompt(self) -> str:
        """Return the agent's system prompt.

        Must be a pure function of the agent's identity — no runtime
        secrets, no env reads, no Keychain access. Tests assert this
        indirectly via the static "no Keychain import" check.
        """

    @abstractmethod
    def user_prompt(self, **inputs: Any) -> str:
        """Render the user-turn content from caller-supplied inputs.

        ``inputs`` are validated job fields from the orchestrator (e.g.
        company name, URL, prior dossier text). The base class does not
        inspect them.
        """

    # ------------------------------------------------------------------
    # The single public entry point
    # ------------------------------------------------------------------

    def run(self, *, job_id: str | None, **inputs: Any) -> BaseModel:
        """Build prompts, call the cloud client, parse and validate.

        Parameters
        ----------
        job_id
            The owning job's id. ``None`` is permitted only for jobless
            probes (Step 6a tests, and a small set of admin paths).
        **inputs
            Forwarded to :meth:`user_prompt` verbatim.

        Returns the validated :attr:`output_model` instance.

        Raises
        ------
        :class:`backend.cost_control.exceptions.RunawayTrapFired`
            Any trap exception from ``CloudClient.messages_create``
            propagates **unchanged**. Subclasses must not catch this.
        :class:`AgentOutputInvalid`
            Both attempts failed to parse or validate.
        """
        model = self._resolve_model()
        system_text = self.system_prompt()
        user_text = self.user_prompt(**inputs)

        # Resolve tools once, outside the retry loop. The counts dict
        # is allocated once so Trap 2 accounting accumulates across
        # both attempts — a retry must not reset the per-job tool spend.
        tools = self.tools()
        tool_use_counts: dict[str, int] | None = {} if tools is not None else None

        last_reason = "no attempts made"
        last_errors: list[Any] = []

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            messages = self._build_messages(system_text, user_text, attempt)
            estimate = self._input_tokens_estimate(messages)

            log.info(
                "agent call start",
                extra={
                    "agent": self.name,
                    "model": model,
                    "attempt": attempt,
                    "job_id": job_id,
                    "prompt_chars": sum(
                        len(m.get("content", "")) for m in messages
                    ),
                },
            )

            # Trap exceptions raised inside messages_create are NOT
            # caught. They propagate to the orchestrator's single
            # except RunawayTrapFired.
            result: CloudCallResult = self._client.messages_create(
                agent=self.name,
                model=model,
                job_id=job_id,
                messages=messages,
                max_tokens=MAX_TOKENS[self.name],
                input_tokens_estimate=estimate,
                tools=tools,
                tool_use_counts=tool_use_counts,
            )

            try:
                parsed = self._parse_and_validate(result)
            except _ParseFailure as exc:
                last_reason = exc.reason
                last_errors = exc.errors
                log.info(
                    "agent call output invalid",
                    extra={
                        "agent": self.name,
                        "model": model,
                        "attempt": attempt,
                        "job_id": job_id,
                        "reason": exc.reason,
                    },
                )
                continue
            else:
                log.info(
                    "agent call ok",
                    extra={
                        "agent": self.name,
                        "model": model,
                        "attempt": attempt,
                        "job_id": job_id,
                        "response_chars": result.output_tokens * 4,
                    },
                )
                return parsed

        # Both attempts exhausted.
        raise AgentOutputInvalid(
            f"agent {self.name!r} failed to produce valid output "
            f"after {_MAX_ATTEMPTS} attempts: {last_reason}",
            agent=self.name,
            model=model,
            attempt=_MAX_ATTEMPTS,
            validation_errors=last_errors,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_model(self) -> str:
        """Return the model id for this agent's :attr:`role`.

        ``Models`` is a frozen dataclass whose attribute names equal the
        :data:`AgentRole` literal members, so this is a direct lookup.
        """
        return getattr(self._models, self.role)

    @staticmethod
    def _build_messages(
        system_text: str,
        user_text: str,
        attempt: int,
    ) -> list[dict[str, Any]]:
        """Build the Anthropic-shaped messages list for one attempt.

        Step 6a is single-turn: a system block (prepended to the user
        content) and exactly one user message. Step 6b will extend this
        for tool-use turns.
        """
        body = user_text
        if attempt > 1:
            body = body + _RETRY_REMINDER
        # System prompt is concatenated to the user message rather than
        # passed as ``system=...`` so the protocol surface stays a single
        # ``messages`` list. The cloud client already supports passing
        # extra SDK kwargs if a subclass wants a separate system field
        # in the future.
        combined_content = f"{system_text}\n\n{body}" if system_text else body
        return [{"role": "user", "content": combined_content}]

    @staticmethod
    def _input_tokens_estimate(messages: list[dict[str, Any]]) -> int:
        """Cheap, conservative tokens-from-bytes estimate.

        ``len(json.dumps(messages)) // 4`` over-estimates by ~30-50%
        versus the real Anthropic tokenizer. That is intentional: an
        over-estimate trips Trap 3 / Trap 4 *early* (safe direction); an
        under-estimate would trip them late (runaway direction).
        """
        return max(1, len(json.dumps(messages)) // 4)

    def _parse_and_validate(self, result: CloudCallResult) -> BaseModel:
        """Extract → JSON-decode → Pydantic-validate, or raise.

        Raises :class:`_ParseFailure` on any failure so the retry
        bookkeeping in :meth:`run` can capture the reason without
        the response text leaking into logs.
        """
        text = extract_text(result.response)
        if not text:
            raise _ParseFailure(
                reason="response contained no text blocks",
                errors=[],
            )
        try:
            decoded = extract_json_block(text)
        except json.JSONDecodeError as exc:
            raise _ParseFailure(
                reason=f"json decode failed: {exc.msg}",
                errors=[{"msg": exc.msg, "pos": exc.pos}],
            ) from exc

        try:
            return self.output_model.model_validate(decoded)
        except ValidationError as exc:
            # Pydantic's errors() returns dicts already free of any user
            # input — just locations and message strings. Safe to keep.
            raise _ParseFailure(
                reason="pydantic validation failed",
                errors=list(exc.errors()),
            ) from exc


# ---------------------------------------------------------------------------
# Internal parse-failure carrier
# ---------------------------------------------------------------------------

class _ParseFailure(Exception):
    """Internal-only signal between :meth:`_parse_and_validate` and the
    retry loop in :meth:`run`. Not part of the public API.
    """

    def __init__(self, *, reason: str, errors: list[Any]) -> None:
        super().__init__(reason)
        self.reason = reason
        self.errors = errors
