"""The model every Outdraw call shares: Claude Haiku 5.5 through Strands, metered and capped.

`build_model(settings)` is Haiku 5.5 with the T13 limits baked in: `max_tokens` 2000
(thinking counts against it), effort `low`, no sampling parameters (Haiku 5.5 rejects
non-default `temperature`/`top_p`/`top_k`), prompt caching on the system prompt and the tool
schemas. `MeteredModel` wraps any Strands model and adds every call's tokens to the daily
counter `draw:llm:YYYY-MM-DD`, raising `BudgetExceeded` above `LLM_DAILY_TOKEN_CAP`.

The agent (`draw.agent.agent`), the compare call, the refinement parser and the brief writer
all build their model here, so the limits live in one place; `draw.agent.agent` re-exports
these names for the callers that always read them from there.
"""

from __future__ import annotations

import os
import threading
from collections.abc import AsyncGenerator, AsyncIterable
from dataclasses import dataclass
from typing import Any, Final, TypeVar

from pydantic import BaseModel
from strands.agent.agent_metadata import AgentMetadata
from strands.models import Model
from strands.models.anthropic import AnthropicModel
from strands.models.model import CacheConfig
from strands.types.content import Messages, SystemContentBlock
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolChoice, ToolSpec

from draw.qloo.budget import LLM_PREFIX, DailyCounter, NullCounter, RedisCounter

__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_TOKEN_CAP",
    "EFFORT",
    "MAX_TOKENS",
    "AgentSettings",
    "MeteredModel",
    "build_model",
    "token_counter_from_env",
]

DEFAULT_MODEL: Final = "claude-haiku-5-5"
MAX_TOKENS: Final = 2000
EFFORT: Final = "low"
DEFAULT_TOKEN_CAP: Final = 2_000_000
"""Tokens per UTC day across runs; ~50 runs of Haiku 5.5 with cached prompts."""

T = TypeVar("T", bound=BaseModel)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


@dataclass(frozen=True)
class AgentSettings:
    model_id: str = DEFAULT_MODEL
    token_cap: int = DEFAULT_TOKEN_CAP
    redis_url: str | None = None

    @classmethod
    def from_env(cls) -> AgentSettings:
        return cls(
            model_id=os.environ.get("ANTHROPIC_MODEL", "").strip() or DEFAULT_MODEL,
            token_cap=_env_int("LLM_DAILY_TOKEN_CAP", DEFAULT_TOKEN_CAP),
            redis_url=os.environ.get("REDIS_URL") or None,
        )


def build_model(settings: AgentSettings | None = None) -> AnthropicModel:
    """Haiku 5.5 through Strands' Anthropic provider, with the T13 limits baked in."""
    settings = settings or AgentSettings.from_env()
    return AnthropicModel(
        model_id=settings.model_id,
        max_tokens=MAX_TOKENS,
        # Effort, not sampling: Haiku 5.5 rejects non-default temperature/top_p/top_k.
        params={"output_config": {"effort": EFFORT}},
        cache_config=CacheConfig(strategy="anthropic", system_prompt_ttl=True, tools_ttl=True),
    )


def token_counter_from_env(settings: AgentSettings | None = None) -> DailyCounter:
    """`draw:llm:YYYY-MM-DD` in Redis when `REDIS_URL` is set, else a no-op."""
    settings = settings or AgentSettings.from_env()
    if not settings.redis_url:
        return NullCounter()
    return RedisCounter(
        settings.redis_url,
        settings.token_cap,
        prefix=LLM_PREFIX,
        what="LLM token",
        charge_over=True,
    )


def _tokens_of(usage: dict[str, Any]) -> int:
    billed = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheWriteInputTokens")
    return sum(int(usage.get(k) or 0) for k in billed)


class MeteredModel(Model):
    """Wraps any Strands model: after each call, `incrby` the tokens it used; raise above cap."""

    def __init__(self, inner: Model, counter: DailyCounter) -> None:
        self.inner = inner
        self.counter = counter
        self.calls = 0
        self.tokens = 0

    def update_config(self, **model_config: Any) -> None:
        self.inner.update_config(**model_config)

    def get_config(self) -> Any:
        return self.inner.get_config()

    def structured_output(
        self,
        output_model: type[T],
        prompt: Messages,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, T | Any], None]:
        return self.inner.structured_output(output_model, prompt, system_prompt, **kwargs)

    async def stream(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        *,
        tool_choice: ToolChoice | None = None,
        system_prompt_content: list[SystemContentBlock] | None = None,
        invocation_state: dict[str, Any] | None = None,
        cancel_signal: threading.Event | None = None,
        agent_metadata: AgentMetadata | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[StreamEvent]:
        self.calls += 1
        events = self.inner.stream(
            messages,
            tool_specs,
            system_prompt,
            tool_choice=tool_choice,
            system_prompt_content=system_prompt_content,
            invocation_state=invocation_state,
            cancel_signal=cancel_signal,
            agent_metadata=agent_metadata,
            **kwargs,
        )
        async for event in events:
            if "metadata" in event:
                tokens = _tokens_of(dict(event["metadata"].get("usage") or {}))
                self.tokens += tokens
                if tokens:
                    # Raises `BudgetExceeded` above the cap; the spend stays on the counter.
                    await self.counter.reserve(tokens)
            yield event
