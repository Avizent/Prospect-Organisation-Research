"""Structural typing surface that agents talk to.

The base agent depends on this :class:`CloudClientProtocol` rather than
the concrete :class:`backend.cost_control.cloud_client.CloudClient`.
That keeps the agent layer testable with a hand-rolled fake without any
patching, and makes it impossible for an agent to discover and reach for
``CloudClient.for_production`` through type narrowing.

The signature here must stay byte-for-byte aligned with
:meth:`CloudClient.messages_create`. The protocol's only consumer is
:class:`backend.agents.base.BaseAgent`, so any drift will surface as a
type-checker error on the call site or a test failure in
``tests/agents/test_base_agent.py``.
"""

from __future__ import annotations

from typing import Any, Protocol

from backend.cost_control.cloud_client import CloudCallResult


class CloudClientProtocol(Protocol):
    """The single method an agent is allowed to call on its cloud client.

    Mirrors :meth:`CloudClient.messages_create` exactly. The agent layer
    never sees any other attribute of the real client (no SDK handle, no
    keychain, no budget guard accessors) — the protocol is the moat.
    """

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
    ) -> CloudCallResult: ...
