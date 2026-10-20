"""A Strands `Model` that plays a script: each turn is tool calls or a final text.

A turn is either a string (the assistant's text, `end_turn`), a list of `(tool, input)`
pairs (one assistant message with that many tool uses), a `Stop` (a bare stop reason such
as `max_tokens` or `refusal`), or a callable receiving every tool result so far
(parsed JSON payloads, oldest first) so a script can thread ids from one tool to the next.
When the script runs out, the last turn repeats, which is how a runaway model is played.
Every call reports the same usage so token budgets are testable.
"""

from __future__ import annotations

import json
import threading
from collections.abc import AsyncGenerator, AsyncIterable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel
from strands.agent.agent_metadata import AgentMetadata
from strands.models import Model
from strands.types.content import Messages, SystemContentBlock
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolChoice, ToolSpec

T = TypeVar("T", bound=BaseModel)
ToolCalls = list[tuple[str, dict[str, Any]]]
Results = list[dict[str, Any]]


@dataclass(frozen=True)
class Stop:
    reason: str


Turn = str | ToolCalls | Stop
Script = Sequence[Turn | Callable[[Results], Turn]]

INPUT_TOKENS = 1000
OUTPUT_TOKENS = 100


def all_results(messages: Messages) -> Results:
    """Every tool result so far, oldest first, as the JSON payloads the tools returned."""
    out: Results = []
    for message in messages:
        if message["role"] != "user":
            continue
        for block in message.get("content") or []:
            if "toolResult" not in block:
                continue
            text = "".join(
                str(c["text"]) for c in block["toolResult"].get("content") or [] if "text" in c
            )
            try:
                loaded = json.loads(text)
            except json.JSONDecodeError:
                loaded = {"_text": text, "_status": block["toolResult"]["status"]}
            out.append(loaded if isinstance(loaded, dict) else {"_value": loaded})
    return out


class ScriptedModel(Model):
    def __init__(self, script: Script) -> None:
        self.script = list(script)
        self.turn = 0
        self.requests: list[Messages] = []
        self.tool_specs: list[ToolSpec] = []
        self.system_prompt: str | None = None

    def update_config(self, **model_config: Any) -> None:
        pass

    def get_config(self) -> Any:
        return {"model_id": "scripted"}

    def structured_output(
        self,
        output_model: type[T],
        prompt: Messages,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, T | Any], None]:
        raise NotImplementedError

    def next_turn(self, messages: Messages) -> Turn:
        entry = self.script[min(self.turn, len(self.script) - 1)]
        self.turn += 1
        return entry(all_results(messages)) if callable(entry) else entry

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
        self.requests.append(list(messages))
        self.tool_specs = list(tool_specs or [])
        self.system_prompt = system_prompt
        turn = self.next_turn(messages)
        for event in self._events(turn):
            yield event

    def _events(self, turn: Turn) -> list[StreamEvent]:
        events: list[StreamEvent] = [{"messageStart": {"role": "assistant"}}]
        if isinstance(turn, Stop):
            events.append({"contentBlockStart": {"start": {}}})
            events.append({"contentBlockDelta": {"delta": {"text": "…"}}})
            events.append({"contentBlockStop": {}})
            stop = turn.reason
        elif isinstance(turn, str):
            events.append({"contentBlockStart": {"start": {}}})
            events.append({"contentBlockDelta": {"delta": {"text": turn}}})
            events.append({"contentBlockStop": {}})
            stop = "end_turn"
        else:
            for i, (name, input_) in enumerate(turn):
                events.append(
                    {
                        "contentBlockStart": {
                            "start": {"toolUse": {"name": name, "toolUseId": f"t{self.turn}_{i}"}}
                        }
                    }
                )
                events.append(
                    {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(input_)}}}}
                )
                events.append({"contentBlockStop": {}})
            stop = "tool_use"
        events.append({"messageStop": {"stopReason": stop}})  # type: ignore[typeddict-item]
        events.append(
            {
                "metadata": {
                    "usage": {
                        "inputTokens": INPUT_TOKENS,
                        "outputTokens": OUTPUT_TOKENS,
                        "totalTokens": INPUT_TOKENS + OUTPUT_TOKENS,
                    },
                    "metrics": {"latencyMs": 1},
                }
            }
        )
        return events
