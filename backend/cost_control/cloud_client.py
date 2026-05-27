"""Anthropic SDK wrapper that enforces all seven runaway traps.

Every Claude API call in this application goes through
:class:`CloudClient`. No agent or other module calls the Anthropic
SDK directly; the test ``tests/runaway/test_no_direct_anthropic_imports``
proves that fence is in place.

Traps enforced here
-------------------
* Trap 1 — :func:`MAX_TOKENS` ceiling per agent (from
  ``backend/agents/limits.py``). No default fallback; an unknown
  agent name raises :class:`UnknownAgent`.
* Trap 2 — tool-call ceiling per agent. Counts ``tool_use`` blocks in
  the agent loop and raises :class:`ToolCallLimitExceeded` on the
  first overflow.
* Trap 3 — per-job budget guard via :class:`JobBudgetGuard`.
* Trap 4 — daily/monthly caps via :class:`BudgetState`.
* Trap 5 — wall-clock timeouts. Per-call 90s is set on the SDK client
  here. Per-agent and per-job timeouts are exposed as
  :func:`run_with_agent_timeout` / :func:`run_with_job_timeout` for
  the orchestrator to wire in Step 9.
* Trap 6 — critic-revise loop bound. Primitive lives in
  :mod:`backend.cost_control.revise_counter`; the orchestrator (Step 10)
  invokes it.
* Trap 7 — concurrent job semaphore. Primitive lives in
  :mod:`backend.cost_control.concurrency`; the job-start route
  (Step 7) ``async with``\\ s it.

Credentials
-----------
:meth:`CloudClient.for_production` lazily reads the Anthropic API key
from the macOS Keychain (never from env vars). Tests do not call
``for_production``; they construct a :class:`CloudClient` with an
injected fake SDK directly. No real API call is made anywhere in
Step 5.

Reviewer note (Opus 4.7 audit)
------------------------------
This file is the lone allowed importer of ``anthropic``. If you
introduce another import site, ``tests/runaway/test_no_direct_anthropic_imports``
fails the build.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

# NB: ``anthropic`` is intentionally imported lazily inside
# ``for_production`` only. Module-level import would still pass the
# fence test, but lazy import keeps Step 5 free of any *side effect*
# from the SDK during test collection.

from backend.agents.limits import (
    DEFAULT_TOOL_CALL_LIMIT,
    MAX_TOKENS,
    TOOL_CALL_LIMITS,
)
from backend.cost_control import audit as audit_mod
from backend.cost_control.budget_state import BudgetState
from backend.cost_control.exceptions import (
    AgentTimeoutExceeded,
    CallTimeoutExceeded,
    JobTimeoutExceeded,
    MaxTokensExceeded,
    ToolCallLimitExceeded,
    UnknownAgent,
)
from backend.cost_control.job_budget import JobBudgetGuard
from backend.cost_control.pricing import (
    actual_call_cost_usd,
    estimate_call_cost_usd,
)


# ---------------------------------------------------------------------------
# Trap 5 — wall-clock timeouts
# ---------------------------------------------------------------------------

#: Per-API-call timeout, in seconds. Passed to the SDK client.
CALL_TIMEOUT_SECONDS: float = 90.0

#: Per-agent timeout, in seconds. Used by :func:`run_with_agent_timeout`.
AGENT_TIMEOUT_SECONDS: float = 5 * 60.0

#: Per-job timeout, in seconds. Used by :func:`run_with_job_timeout`.
JOB_TIMEOUT_SECONDS: float = 20 * 60.0


# ---------------------------------------------------------------------------
# Minimal SDK protocols — keep the type surface explicit so test fakes
# match exactly what the production code touches.
# ---------------------------------------------------------------------------

class _SDKMessages(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class _SDKClient(Protocol):
    messages: _SDKMessages


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class CloudCallResult:
    """The return value of :meth:`CloudClient.messages_create`.

    Carries everything the caller needs (the SDK response and the
    realised cost in USD) without leaking secret material.
    """

    response: Any
    input_tokens: int
    output_tokens: int
    cost_usd: float


# ---------------------------------------------------------------------------
# CloudClient
# ---------------------------------------------------------------------------

class CloudClient:
    """Single point of entry for every Claude API call in the app.

    Construct in production via :meth:`for_production`. Construct in
    tests with an injected fake ``sdk_client``.
    """

    def __init__(
        self,
        *,
        sdk_client: _SDKClient,
        budget_state: BudgetState,
        job_guard: JobBudgetGuard,
        call_timeout_seconds: float = CALL_TIMEOUT_SECONDS,
        force_daily_override: bool = False,
    ) -> None:
        # Constructor is keyword-only and has no default ``sdk_client``,
        # so it is impossible to forget to inject one. The fence test
        # additionally proves nobody else imports ``anthropic``.
        self._sdk = sdk_client
        self._budget_state = budget_state
        self._job_guard = job_guard
        self._call_timeout = float(call_timeout_seconds)
        self._force_daily_override = bool(force_daily_override)

    # ------------------------------------------------------------------
    # Production-only constructor
    # ------------------------------------------------------------------

    @classmethod
    def for_production(
        cls,
        *,
        budget_state: BudgetState,
        job_guard: JobBudgetGuard,
        force_daily_override: bool = False,
    ) -> "CloudClient":
        """Build a :class:`CloudClient` against the real Anthropic SDK.

        Pulls the API key from the macOS Keychain. Raises
        :class:`RuntimeError` if no key is configured — the admin
        credentials flow (Step 4) is what stores it.

        Tests must **not** call this method.
        """
        from anthropic import Anthropic  # noqa: WPS433 — lazy import by design

        from backend.credentials import keychain

        api_key = keychain.get_anthropic_key()
        if not api_key:
            raise RuntimeError(
                "Anthropic API key not configured; visit /admin/credentials"
            )
        sdk = Anthropic(api_key=api_key, timeout=CALL_TIMEOUT_SECONDS)
        return cls(
            sdk_client=sdk,
            budget_state=budget_state,
            job_guard=job_guard,
            force_daily_override=force_daily_override,
        )

    # ------------------------------------------------------------------
    # The single guarded entry point
    # ------------------------------------------------------------------

    def messages_create(
        self,
        *,
        agent: str,
        model: str,
        job_id: str | None,
        messages: list[dict[str, Any]],
        max_tokens: int,
        input_tokens_estimate: int,
        tools: list[dict[str, Any]] | None = None,
        tool_use_counts: dict[str, int] | None = None,
        approved_by: str = "user",
        extra_sdk_kwargs: dict[str, Any] | None = None,
    ) -> CloudCallResult:
        """Run a single Claude API call through all pre/post-call traps.

        Parameters
        ----------
        agent
            Agent name. Must be a key of
            :data:`backend.agents.limits.MAX_TOKENS`. No fallback.
        model
            Model id (e.g. ``"claude-sonnet-4-6"``). Must be in
            :data:`backend.cost_control.pricing.PRICES`.
        job_id
            The owning job's id (UUID string), or ``None`` for
            jobless calls. Recorded in the audit row.
        messages
            The ``messages`` list the SDK expects. Only ever hashed.
        max_tokens
            The caller's requested cap. Must be ``<= MAX_TOKENS[agent]``.
        input_tokens_estimate
            Caller's best estimate of input tokens, used for the
            pre-call cost estimate (Trap 3, Trap 4). Post-call the
            true count comes from the SDK response's ``usage``.
        tools
            Forwarded to the SDK. ``None`` for non-tool calls.
        tool_use_counts
            Optional mapping of ``tool_name -> count_so_far`` for this
            agent run. ``messages_create`` checks the running total
            against :data:`TOOL_CALL_LIMITS`/``DEFAULT_TOOL_CALL_LIMIT``
            *before* the SDK call (Trap 2). On return, the caller
            updates this dict in place based on the response.
        approved_by
            Recorded in the audit row. Defaults to ``"user"`` because
            this is a single-user tool; the value exists for future
            CLI/cron paths.
        extra_sdk_kwargs
            Passed through to ``sdk.messages.create`` as-is. Reserved
            for ``stop_sequences`` etc. The trap layer ignores them.

        Returns
        -------
        :class:`CloudCallResult` with the SDK response and the realised
        cost.

        Raises
        ------
        :class:`MaxTokensExceeded`, :class:`UnknownAgent`,
        :class:`ToolCallLimitExceeded`, :class:`JobBudgetExceeded`,
        :class:`DailyBudgetExceeded`, :class:`MonthlyBudgetExceeded`,
        :class:`UnknownModel`, :class:`CallTimeoutExceeded`.
        Trap exceptions also write the incident log and fire a macOS
        notification via :func:`audit.fire_trap`.
        """
        # -- Trap 1 --------------------------------------------------
        self._enforce_max_tokens(agent, max_tokens, job_id)

        # -- Trap 2 (pre-call) ---------------------------------------
        # We check the running totals supplied by the caller. The
        # in-loop count update happens after the SDK returns when the
        # caller increments ``tool_use_counts`` based on the response.
        if tool_use_counts is not None:
            self._enforce_tool_call_limits(agent, tool_use_counts, job_id)

        # -- Cost estimate (used by Traps 3 & 4) ---------------------
        estimate_usd = estimate_call_cost_usd(
            model=model,
            input_tokens=input_tokens_estimate,
            max_output_tokens=max_tokens,
        )

        # -- Trap 3 (per-job) ----------------------------------------
        try:
            self._job_guard.check_estimate(estimate_usd)
        except Exception as exc:
            # Annotate with job_id/agent then fire.
            if hasattr(exc, "agent") and getattr(exc, "agent") is None:
                exc.agent = agent  # type: ignore[attr-defined]
            audit_mod.fire_trap(exc)

        # -- Trap 4 (daily / monthly) --------------------------------
        try:
            decision = self._budget_state.check_and_reserve(
                estimate_usd, force=self._force_daily_override
            )
        except Exception as exc:
            if hasattr(exc, "job_id") and getattr(exc, "job_id") is None:
                exc.job_id = job_id  # type: ignore[attr-defined]
            if hasattr(exc, "agent") and getattr(exc, "agent") is None:
                exc.agent = agent  # type: ignore[attr-defined]
            audit_mod.fire_trap(exc)

        # -- SDK call (Trap 5a: per-call timeout) --------------------
        sdk_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if tools is not None:
            sdk_kwargs["tools"] = tools
        if extra_sdk_kwargs:
            sdk_kwargs.update(extra_sdk_kwargs)
        # The SDK client was constructed with ``timeout=CALL_TIMEOUT_SECONDS``
        # in production. For test fakes we honour the same contract by
        # catching their own TimeoutError and re-raising as a trap.
        try:
            response = self._sdk.messages.create(**sdk_kwargs)
        except TimeoutError as exc:
            audit_mod.fire_trap(
                CallTimeoutExceeded(
                    f"per-call timeout after {self._call_timeout:.0f}s",
                    job_id=job_id,
                    agent=agent,
                    context={"timeout_s": self._call_timeout},
                ),
            )
            raise exc  # unreachable; fire_trap re-raises  # pragma: no cover

        # -- Post-call cost --------------------------------------------
        usage = self._extract_usage(response)
        actual_usd = actual_call_cost_usd(
            model=model,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
        )
        self._job_guard.record(actual_usd)
        self._budget_state.commit(actual_usd)

        # -- Trap 2 (post-call increment & re-check) -----------------
        if tool_use_counts is not None:
            self._update_tool_use_counts_from_response(
                response, tool_use_counts
            )
            # Re-check after counting the tool_use blocks in *this*
            # response, so the next call has up-to-date totals.
            self._enforce_tool_call_limits(agent, tool_use_counts, job_id)

        # -- Audit log -----------------------------------------------
        audit_mod.write_audit(
            job_id=job_id,
            agent=agent,
            model=model,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cost_usd=actual_usd,
            prompt_hash=audit_mod.hash_prompt(messages),
            approved_by=approved_by,
            budget_remaining_job=self._job_guard.remaining_usd,
            budget_remaining_day=decision.remaining_day_usd - actual_usd,
        )

        return CloudCallResult(
            response=response,
            input_tokens=int(usage["input_tokens"]),
            output_tokens=int(usage["output_tokens"]),
            cost_usd=actual_usd,
        )

    # ------------------------------------------------------------------
    # Trap 1 / Trap 2 helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _enforce_max_tokens(
        agent: str, max_tokens: int, job_id: str | None
    ) -> None:
        if agent not in MAX_TOKENS:
            audit_mod.fire_trap(
                UnknownAgent(
                    f"no max_tokens ceiling registered for agent {agent!r}",
                    job_id=job_id,
                    agent=agent,
                    context={"agent": agent},
                ),
            )
        ceiling = MAX_TOKENS[agent]
        if max_tokens <= 0:
            audit_mod.fire_trap(
                MaxTokensExceeded(
                    f"max_tokens must be > 0 (got {max_tokens})",
                    job_id=job_id,
                    agent=agent,
                    context={
                        "agent": agent,
                        "max_tokens": max_tokens,
                        "ceiling": ceiling,
                    },
                ),
            )
        if max_tokens > ceiling:
            audit_mod.fire_trap(
                MaxTokensExceeded(
                    f"agent {agent!r} requested max_tokens={max_tokens} "
                    f"> ceiling {ceiling}",
                    job_id=job_id,
                    agent=agent,
                    context={
                        "agent": agent,
                        "max_tokens": max_tokens,
                        "ceiling": ceiling,
                    },
                ),
            )

    @staticmethod
    def _enforce_tool_call_limits(
        agent: str,
        tool_use_counts: dict[str, int],
        job_id: str | None,
    ) -> None:
        per_agent = TOOL_CALL_LIMITS.get(agent, {})
        for tool_name, count in tool_use_counts.items():
            ceiling = per_agent.get(tool_name, DEFAULT_TOOL_CALL_LIMIT)
            if count > ceiling:
                audit_mod.fire_trap(
                    ToolCallLimitExceeded(
                        f"agent {agent!r} used tool {tool_name!r} "
                        f"{count} times; cap is {ceiling}",
                        job_id=job_id,
                        agent=agent,
                        context={
                            "agent": agent,
                            "tool": tool_name,
                            "count": count,
                            "ceiling": ceiling,
                        },
                    ),
                )

    # ------------------------------------------------------------------
    # SDK-response helpers — keep separate so tests can fake at the
    # smallest possible surface.
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_usage(response: Any) -> dict[str, int]:
        """Pull ``input_tokens`` and ``output_tokens`` from a response.

        Accepts both the real ``anthropic`` SDK objects (which expose
        ``response.usage.input_tokens`` etc.) and dict-shaped fakes.
        """
        usage = getattr(response, "usage", None)
        if usage is None and isinstance(response, dict):
            usage = response.get("usage")
        if usage is None:
            return {"input_tokens": 0, "output_tokens": 0}

        def _get(name: str) -> int:
            if isinstance(usage, dict):
                return int(usage.get(name, 0))
            return int(getattr(usage, name, 0))

        return {
            "input_tokens": _get("input_tokens"),
            "output_tokens": _get("output_tokens"),
        }

    @staticmethod
    def _update_tool_use_counts_from_response(
        response: Any,
        tool_use_counts: dict[str, int],
    ) -> None:
        """Increment ``tool_use_counts`` for every tool_use block.

        Accepts SDK objects with ``content`` blocks of type ``"tool_use"``
        or dict-shaped fakes with ``{"content": [{"type": "tool_use",
        "name": "..."}, ...]}``.
        """
        content = getattr(response, "content", None)
        if content is None and isinstance(response, dict):
            content = response.get("content")
        if not content:
            return
        for block in content:
            block_type = (
                block.get("type") if isinstance(block, dict)
                else getattr(block, "type", None)
            )
            if block_type != "tool_use":
                continue
            name = (
                block.get("name") if isinstance(block, dict)
                else getattr(block, "name", None)
            )
            if not name:
                continue
            tool_use_counts[name] = tool_use_counts.get(name, 0) + 1


# ---------------------------------------------------------------------------
# Trap 5b/5c — agent and job timeouts (orchestrator wires these later).
# ---------------------------------------------------------------------------

async def run_with_agent_timeout(
    coro_factory: Callable[[], Awaitable[Any]],
    *,
    agent: str,
    job_id: str | None,
    timeout_s: float = AGENT_TIMEOUT_SECONDS,
) -> Any:
    """Run ``coro_factory()`` under a per-agent timeout (Trap 5b)."""
    try:
        return await asyncio.wait_for(coro_factory(), timeout=timeout_s)
    except asyncio.TimeoutError:
        audit_mod.fire_trap(
            AgentTimeoutExceeded(
                f"agent {agent!r} exceeded {timeout_s:.0f}s",
                job_id=job_id,
                agent=agent,
                context={"timeout_s": timeout_s},
            ),
        )
        # Unreachable; fire_trap re-raises.
        raise  # pragma: no cover


async def run_with_job_timeout(
    coro_factory: Callable[[], Awaitable[Any]],
    *,
    job_id: str,
    timeout_s: float = JOB_TIMEOUT_SECONDS,
) -> Any:
    """Run ``coro_factory()`` under a per-job timeout (Trap 5c)."""
    try:
        return await asyncio.wait_for(coro_factory(), timeout=timeout_s)
    except asyncio.TimeoutError:
        audit_mod.fire_trap(
            JobTimeoutExceeded(
                f"job {job_id!r} exceeded {timeout_s:.0f}s",
                job_id=job_id,
                context={"timeout_s": timeout_s},
            ),
        )
        raise  # pragma: no cover
