"""The only Qloo gateway: no other module may shell out to `qloo` or import an MCP client.

Use `QlooExec` for scripted calls (`qloo exec`); every call returns a `QlooResult` with a
`call_id` for evidence tracing, or raises one of `QlooNeedsInput`, `QlooEmpty`,
`QlooError`, `BudgetExceeded`. See `exec_client.py` for the per-call pipeline.
"""

from draw.qloo.envelope import (
    BudgetExceeded,
    QlooEmpty,
    QlooEnvelope,
    QlooError,
    QlooGatewayError,
    QlooNeedsInput,
    QlooResult,
    normalise,
)
from draw.qloo.exec_client import QlooExec

__all__ = [
    "BudgetExceeded",
    "QlooEmpty",
    "QlooEnvelope",
    "QlooError",
    "QlooExec",
    "QlooGatewayError",
    "QlooNeedsInput",
    "QlooResult",
    "normalise",
]
