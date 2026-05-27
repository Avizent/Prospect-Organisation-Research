"""Shared fixtures for agent tests.

The single rule of agent tests is: **never touch the real CloudClient,
never import the Anthropic SDK, never reach the network.** This
conftest provides a hand-rolled :class:`FakeCloudClient` that satisfies
:class:`backend.agents._protocols.CloudClientProtocol` structurally,
and a :func:`make_response` helper that builds dict-shaped responses
the same way the real SDK presents them.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.cost_control.cloud_client import CloudCallResult
from backend.cost_control.config_loader import Models


# ---------------------------------------------------------------------------
# FakeCloudClient
# ---------------------------------------------------------------------------

class FakeCloudClient:
    """In-memory stand-in for :class:`CloudClient`.

    Tests script the return values up front with :meth:`script`. Each
    call to :meth:`messages_create` pops the next entry from the queue.
    All call kwargs are captured on :attr:`calls` for assertions.

    Entries in the script can be either :class:`CloudCallResult`
    instances (returned as-is) or :class:`Exception` instances (raised).
    The latter lets tests prove that trap exceptions propagate through
    the base agent unchanged.
    """

    def __init__(self) -> None:
        self._queue: list[Any] = []
        self.calls: list[dict[str, Any]] = []

    def script(self, items: list[Any]) -> None:
        """Replace the queue with *items*. Last-in wins."""
        self._queue = list(items)

    def messages_create(self, **kwargs: Any) -> CloudCallResult:
        self.calls.append(kwargs)
        if not self._queue:
            raise AssertionError(
                "FakeCloudClient.messages_create called but the script "
                "is empty; add more entries via .script([...])."
            )
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        if not isinstance(item, CloudCallResult):
            raise AssertionError(
                f"FakeCloudClient script entry has unexpected type "
                f"{type(item).__name__!r}; expected CloudCallResult or "
                "Exception."
            )
        return item


# ---------------------------------------------------------------------------
# Response builder
# ---------------------------------------------------------------------------

def make_response(
    *,
    text: str | None = None,
    blocks: list[dict[str, Any]] | None = None,
    input_tokens: int = 100,
    output_tokens: int = 50,
    cost_usd: float = 0.001,
) -> CloudCallResult:
    """Build a :class:`CloudCallResult` whose response is a dict.

    Either pass ``text=`` for a single text block (the common case) or
    ``blocks=`` for an explicit content list (used when a test needs a
    response with no text blocks, or with mixed block types).
    """
    if blocks is None:
        if text is None:
            content: list[dict[str, Any]] = []
        else:
            content = [{"type": "text", "text": text}]
    else:
        content = list(blocks)

    response = {
        "content": content,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    }
    return CloudCallResult(
        response=response,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_cloud_client() -> FakeCloudClient:
    return FakeCloudClient()


@pytest.fixture()
def test_models() -> Models:
    """A :class:`Models` instance with distinct ids per role.

    Distinct values let the test that checks role→model lookup assert
    on identity, not just on presence.
    """
    return Models(
        writer_model="test-writer-model",
        research_model="test-research-model",
        critic_model="test-critic-model",
    )
