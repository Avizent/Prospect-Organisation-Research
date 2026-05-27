"""Cost-control package — runaway-protection layer.

Re-exports the public surface so callers can write::

    from backend.cost_control import CloudClient, JobBudgetGuard, ...

Internals (``audit``, ``notify``, ``config_loader``) are imported via
their dotted paths in production code; they are not part of the public
package surface.
"""

from backend.cost_control.budget_state import BudgetDecision, BudgetState
from backend.cost_control.cloud_client import (
    AGENT_TIMEOUT_SECONDS,
    CALL_TIMEOUT_SECONDS,
    JOB_TIMEOUT_SECONDS,
    CloudCallResult,
    CloudClient,
    run_with_agent_timeout,
    run_with_job_timeout,
)
from backend.cost_control.exceptions import (
    AgentTimeoutExceeded,
    CallTimeoutExceeded,
    ConcurrentJobLimitExceeded,
    CriticReviseLimitExceeded,
    DailyBudgetExceeded,
    JobBudgetExceeded,
    JobTimeoutExceeded,
    MaxTokensExceeded,
    MonthlyBudgetExceeded,
    RunawayTrapFired,
    ToolCallLimitExceeded,
    UnknownAgent,
    UnknownModel,
)
from backend.cost_control.job_budget import JobBudgetGuard
from backend.cost_control.pricing import (
    KNOWN_PRICES_AS_OF,
    PRICES,
    PRICING_SOURCE,
    ModelPrice,
    actual_call_cost_usd,
    estimate_call_cost_usd,
    get_price,
)
from backend.cost_control.revise_counter import (
    MAX_REVISIONS_PER_AGENT,
    CriticReviseLimiter,
)

__all__ = [
    # cloud_client
    "CloudClient",
    "CloudCallResult",
    "CALL_TIMEOUT_SECONDS",
    "AGENT_TIMEOUT_SECONDS",
    "JOB_TIMEOUT_SECONDS",
    "run_with_agent_timeout",
    "run_with_job_timeout",
    # budget_state
    "BudgetState",
    "BudgetDecision",
    # job_budget
    "JobBudgetGuard",
    # pricing
    "KNOWN_PRICES_AS_OF",
    "PRICING_SOURCE",
    "PRICES",
    "ModelPrice",
    "get_price",
    "estimate_call_cost_usd",
    "actual_call_cost_usd",
    # revise_counter
    "CriticReviseLimiter",
    "MAX_REVISIONS_PER_AGENT",
    # exceptions
    "RunawayTrapFired",
    "MaxTokensExceeded",
    "UnknownAgent",
    "ToolCallLimitExceeded",
    "JobBudgetExceeded",
    "DailyBudgetExceeded",
    "MonthlyBudgetExceeded",
    "CallTimeoutExceeded",
    "AgentTimeoutExceeded",
    "JobTimeoutExceeded",
    "CriticReviseLimitExceeded",
    "ConcurrentJobLimitExceeded",
    "UnknownModel",
]
