"""Smoke-test the Qloo MCP route: connect, `find_tags("jazz")`, `describe("Radiohead")`.

Spends two Qloo calls (cold) and needs the real key in the environment (or
`~/.qloo/config`). Prints the readiness manifest summary and the two normalised envelopes.

    cd agent && uv run python scripts/mcp_smoke.py

Exit 0 only when both envelopes are `ok`.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from draw.qloo import QlooGatewayError, QlooMCP, QlooResult  # noqa: E402
from draw.qloo.config import QlooConfig  # noqa: E402
from draw.qloo.mcp_client import compact_envelope  # noqa: E402


def _print(title: str, payload: Any) -> None:
    print(f"\n=== {title}")
    print(json.dumps(payload, indent=2, ensure_ascii=False))


async def main() -> int:
    statuses: list[str] = []
    with QlooMCP(QlooConfig.from_env()) as qloo:
        adapter = qloo.capabilities.get("adapter", {})
        _print(
            "qloo_capabilities",
            {
                "ready": adapter.get("ready"),
                "supported_operation_ids": adapter.get("supported_operation_ids"),
                "contract_version": qloo.capabilities.get("contract_version"),
                "exposed_tools": [t.tool_name for t in qloo.tools()],
            },
        )
        calls: list[tuple[str, Callable[[], Awaitable[QlooResult]]]] = [
            ("find_tags(jazz)", lambda: qloo.find_tags("jazz", limit=5)),
            ("describe(Radiohead)", lambda: qloo.describe("Radiohead", type="artist")),
        ]
        for title, make in calls:
            try:
                result: QlooResult = await make()
            except QlooGatewayError as exc:
                envelope = getattr(exc, "envelope", None)
                _print(title, compact_envelope(envelope, error=exc))
                statuses.append("error")
                continue
            _print(title, result.model_dump(mode="json"))
            _print(f"{title} as the model sees it", compact_envelope(result))
            statuses.append(result.status)
    print(f"\nstatuses: {statuses}")
    return 0 if statuses and all(s == "ok" for s in statuses) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
