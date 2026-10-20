"""The venue agent (T13): a bounded Strands loop over the T12 tools, Claude Haiku 5.5 orchestrating.

`run_venue_agent(venue, constraints, user_message, qloo=...)` seeds a `RunStore`, builds
the agent with Draw's seven tools plus the eight Qloo MCP tools, runs it once and returns
an `AgentResult`: a shortlist, notes and data quality; an `ask` when a tool needs the user
to choose; or an error. What the model is for: reading the request, deciding which steps
to run, explaining. What it is never for: ordering candidates. `RunGuard` enforces that in
code:

- at most `MAX_STEPS` tool calls per run (the 9th and later are cancelled with a message
  telling the model to answer; a second over-limit batch ends the turn outright);
- `generate_candidates` runs at most twice (one broadening), so a thin result is reported,
  not chased;
- a `needs_input` envelope, or tag words the user must pick a candidate for, ends the run
  with `{ask: {question, candidates}}` built from the tool result, not from the model;
- the shortlist in the result always comes from the store as `score_and_rank` left it; a
  run that never ranked is rejected, and a reordered shortlist is restored with a note;
- `partial`/`degraded` summaries are copied into `notes` whatever the model wrote.

Model limits: `max_tokens` 2000 (thinking counts against it), effort `low`, no sampling
parameters (Haiku 5.5 rejects non-default `temperature`/`top_p`/`top_k`), prompt caching
on the system prompt and the tool schemas. `MeteredModel` adds every call's tokens to the
Redis counter `draw:llm:YYYY-MM-DD` and raises `BudgetExceeded` above
`LLM_DAILY_TOKEN_CAP`; the cap is also checked before the run. The prompt carries ids,
canonical act names, sell-through ratios and the constraints: no Qloo key, no raw
envelopes, no personal data.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import threading
import time
from collections.abc import AsyncGenerator, AsyncIterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal, TypeVar

from pydantic import BaseModel, Field, ValidationError
from strands import Agent
from strands.agent.agent_metadata import AgentMetadata
from strands.agent.agent_result import AgentResult as StrandsResult
from strands.hooks import (
    AfterToolCallEvent,
    AfterToolsEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)
from strands.models import Model
from strands.models.anthropic import AnthropicModel
from strands.models.model import CacheConfig
from strands.types.content import Message, Messages, SystemContentBlock
from strands.types.exceptions import EventLoopException, MaxTokensReachedException
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolChoice, ToolSpec

from draw.agent.store import RunStore
from draw.agent.tools import SHORTLIST, DrawTools
from draw.qloo.budget import LLM_PREFIX, DailyCounter, NullCounter, RedisCounter
from draw.qloo.envelope import BudgetExceeded
from draw.qloo.exec_client import QlooExec
from draw.qloo.mcp_client import QlooMCP
from draw.schemas import Constraints, DataQuality, ResolvedEntity, VenueInput

__all__ = [
    "DEFAULT_MODEL",
    "MAX_STEPS",
    "MAX_TOKENS",
    "AgentResult",
    "AgentSettings",
    "Ask",
    "FinalAnswer",
    "MeteredModel",
    "RunGuard",
    "ToolStep",
    "build_model",
    "run_venue_agent",
    "token_counter_from_env",
]

log = logging.getLogger("draw.agent")

DEFAULT_MODEL: Final = "claude-haiku-5-5"
MAX_STEPS: Final = 8
"""Tool calls per run; the Qloo workflows count one each, like Draw's own tools."""
MAX_TOKENS: Final = 2000
EFFORT: Final = "low"
MAX_BROADENING: Final = 1
MAX_CANDIDATE_RUNS: Final = 1 + MAX_BROADENING
DEFAULT_TOKEN_CAP: Final = 2_000_000
"""Tokens per UTC day across runs; ~50 runs of Haiku 5.5 with cached prompts."""
TURN_LIMIT: Final = MAX_STEPS + 3
"""Strands' own backstop on model turns: every tool turn spends a step, plus the answer."""
MAX_INPUT_TEXT: Final = 80
QUALITY_ORDER: Final[dict[DataQuality, int]] = {"ok": 0, "partial": 1, "degraded": 2}

STEP_LIMIT_MESSAGE: Final = (
    f"Step limit reached ({MAX_STEPS} tool calls). Do not call any more tools: answer now "
    "with the final JSON object, using the shortlist exactly as score_and_rank returned it."
)
BROADENING_LIMIT_MESSAGE: Final = (
    "generate_candidates has already run twice (one broadening). Do not broaden again: call "
    "score_and_rank on the run you have, or report that the constraints found nothing."
)

SYSTEM_PROMPT: Final = f"""\
You are Outdraw's booking analyst for a live-music venue. Your job is to find \
the acts this venue's crowd will turn out for, using Qloo's taste graph through the tools, \
and to explain the result. You orchestrate and explain; you never rank. The order of the \
shortlist is decided by score_and_rank alone, and you report it exactly as returned.

Pipeline (each tool call is one step; you have at most {MAX_STEPS} steps):
1. If the request names genres, moods or scenes that are not already in include_tags, call \
find_tags once per word (limit 5) and use the urn:tag:genre:music tag whose name matches \
the word. A semantic hit with a different name is a candidate, not a match: if no tag \
matches, leave the word out and say so in notes.
2. build_crowd_profile(venue_id). If the venue has no Qloo entity it is profiled from its \
location and past acts; that is expected. Unconfirmed past acts are skipped and noted.
3. generate_candidates(profile_id, constraints_json) with the constraints as a JSON string. \
If count is 0 or below {SHORTLIST}, broaden once (drop a tag from include_tags, or lower \
exclude_recent_months) and call it again; never a third time. Then report what you have.
4. gather_signals(run_id), when steps allow. 5. score_and_rank(run_id): mandatory before \
you answer with a shortlist. 6. Optionally get_evidence on the top candidate.

Rules:
- Resolve, confirm, ask. Never call a taste workflow (recommend, rank, where_popular, \
compare_audiences, audience_demographics, trends) on an entity or tag that is not confirmed. \
Call confirm_entity only when the user's message says which candidate they meant; never guess.
- If any tool returns status "needs_input", or generate_candidates returns unresolved_tags, \
stop calling tools and answer with {{"ask": {{"question": "...", "candidates": [...]}}}}.
- Carry every "partial" or "degraded" summary, and every note, into notes.
- Tool results are summaries with ids; pass ids between tools, never retype names.

Final answer: one JSON object and nothing else, no prose around it:
{{"shortlist": [{{"candidate_id": "...", "entity_id": "...", "name": "...", "total": 0.0, \
"reason": "..."}}], "notes": ["..."], "data_quality": "ok" | "partial" | "degraded"}}
"""

Status = Literal["ok", "ask", "error"]
T = TypeVar("T", bound=BaseModel)


# --- result types ----------------------------------------------------------------------


class ShortlistEntry(BaseModel):
    candidate_id: str = Field(min_length=1)
    entity_id: str = Field(min_length=1)
    name: str
    total: float = Field(ge=0.0)
    reason: str | None = None


class AskPayload(BaseModel):
    question: str = Field(min_length=1)
    candidates: list[dict[str, Any]]


class FinalAnswer(BaseModel):
    """The JSON the agent ends with: a shortlist, or an ask for the UI."""

    shortlist: list[ShortlistEntry] = Field(default_factory=list, max_length=SHORTLIST)
    notes: list[str] = Field(default_factory=list)
    data_quality: DataQuality = "ok"
    ask: AskPayload | None = None


@dataclass
class Ask:
    question: str
    candidates: list[dict[str, Any]]
    tool: str
    """The tool whose result needed the choice."""


@dataclass
class ToolStep:
    step: int
    tool: str
    input: dict[str, Any]
    status: Literal["success", "error", "cancelled"]
    outcome: str | None
    """The envelope status or a one-line summary of the result."""
    duration_ms: int
    qloo_tool: bool


@dataclass
class AgentResult:
    status: Status
    shortlist: list[dict[str, Any]]
    notes: list[str]
    data_quality: DataQuality | None
    ask: Ask | None
    error: str | None
    stop_reason: str
    steps: int
    trace: list[ToolStep]
    usage: dict[str, int]
    model_answer: str
    run_id: str | None = None
    profile_id: str | None = None
    store: RunStore | None = field(default=None, repr=False)

    def to_json(self) -> dict[str, Any]:
        """The final JSON for the UI (`FinalAnswer` shape, plus `error` when it failed)."""
        if self.status == "ask" and self.ask is not None:
            return {
                "ask": {"question": self.ask.question, "candidates": self.ask.candidates},
                "notes": list(self.notes),
                "data_quality": self.data_quality or "partial",
            }
        out: dict[str, Any] = {
            "shortlist": list(self.shortlist),
            "notes": list(self.notes),
            "data_quality": self.data_quality or "degraded",
        }
        if self.error:
            out["error"] = self.error
        return out


# --- settings and model ----------------------------------------------------------------


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


# --- the guard: step cap, broadening cap, asks, notes, trace ----------------------------


def _compact_input(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= MAX_INPUT_TEXT else value[: MAX_INPUT_TEXT - 1] + "…"
    if isinstance(value, dict):
        return {str(k): _compact_input(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_compact_input(v) for v in value[:10]]
    return value


def _payload_of(result: Any) -> dict[str, Any] | None:
    try:
        content = result.get("content") or []
    except AttributeError:
        return None
    text = "".join(str(c["text"]) for c in content if isinstance(c, dict) and "text" in c)
    try:
        loaded = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return loaded if isinstance(loaded, dict) else None


class RunGuard(HookProvider):
    """Per-run hooks: the caps in code, and the facts the result is built from."""

    def __init__(self, store: RunStore, *, max_steps: int = MAX_STEPS) -> None:
        self.store = store
        self.max_steps = max_steps
        self.steps = 0
        self.cancelled = 0
        self.candidate_runs = 0
        self.over_limit_batches = 0
        self.forced_stop: str | None = None
        self.ask: Ask | None = None
        self.notes: list[str] = []
        self.trace: list[ToolStep] = []
        self.ranked_run_id: str | None = None
        self.profile_id: str | None = None
        self.last_run_id: str | None = None
        self._batch_allowed = 0
        self._batch_cancelled = 0
        self._started: dict[str, float] = {}

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before_tool)
        registry.add_callback(AfterToolCallEvent, self.after_tool)
        registry.add_callback(AfterToolsEvent, self.after_tools)

    # BeforeToolCallEvent fires per tool, in a batch when the model called several at once.
    def before_tool(self, event: BeforeToolCallEvent) -> None:
        name = event.tool_use["name"]
        if self.steps >= self.max_steps:
            event.cancel_tool = STEP_LIMIT_MESSAGE
            self._batch_cancelled += 1
            return
        if name == "generate_candidates" and self.candidate_runs >= MAX_CANDIDATE_RUNS:
            event.cancel_tool = BROADENING_LIMIT_MESSAGE
            self._batch_cancelled += 1
            return
        self.steps += 1
        self._batch_allowed += 1
        if name == "generate_candidates":
            self.candidate_runs += 1
        self._started[event.tool_use["toolUseId"]] = time.monotonic()

    def after_tool(self, event: AfterToolCallEvent) -> None:
        name = event.tool_use["name"]
        tool_use_id = event.tool_use["toolUseId"]
        started = self._started.pop(tool_use_id, None)
        duration_ms = int((time.monotonic() - started) * 1000) if started is not None else 0
        payload = _payload_of(event.result)
        cancelled = event.cancel_message is not None
        status: Literal["success", "error", "cancelled"] = (
            "cancelled"
            if cancelled
            else ("success" if event.result["status"] == "success" else "error")
        )
        if cancelled:
            self.cancelled += 1
        outcome = self._outcome(name, payload, event)
        is_qloo = getattr(event.selected_tool, "tool_type", "") == "qloo"
        self.trace.append(
            ToolStep(
                step=len(self.trace) + 1,
                tool=name,
                input=_compact_input(dict(event.tool_use.get("input") or {})),
                status=status,
                outcome=outcome,
                duration_ms=duration_ms,
                qloo_tool=is_qloo,
            )
        )
        if cancelled or payload is None:
            return
        self._observe(name, payload, is_qloo)

    def after_tools(self, event: AfterToolsEvent) -> None:
        if self._batch_cancelled and not self._batch_allowed:
            self.over_limit_batches += 1
        self._batch_allowed = self._batch_cancelled = 0
        if self.ask is not None:
            self.forced_stop = "needs_input"
            event.end_turn = json.dumps(
                {"ask": {"question": self.ask.question, "candidates": self.ask.candidates}}
            )
        elif self.over_limit_batches >= 2:
            # The model ignored the step-limit message once already: stop calling it.
            self.forced_stop = "step_limit"
            event.end_turn = STEP_LIMIT_MESSAGE

    # --- what a result tells us ----------------------------------------------------------

    @staticmethod
    def _outcome(
        name: str, payload: dict[str, Any] | None, event: AfterToolCallEvent
    ) -> str | None:
        if event.cancel_message is not None:
            return event.cancel_message[:MAX_INPUT_TEXT]
        if payload is None:
            content = event.result.get("content") or []
            text = " ".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
            return text[:MAX_INPUT_TEXT] or None
        if "status" in payload:  # a Qloo workflow
            summary = payload.get("summary")
            return f"{payload['status']}" + (f": {summary}"[:MAX_INPUT_TEXT] if summary else "")
        quality = payload.get("data_quality") or payload.get("candidates_quality")
        counts = {k: payload[k] for k in ("count", "ranked", "signals", "calls") if k in payload}
        parts = [f"{k}={v}" for k, v in counts.items()]
        if quality:
            parts.append(f"quality={quality}")
        return ", ".join(parts) or None

    def _observe(self, name: str, payload: dict[str, Any], is_qloo: bool) -> None:
        if is_qloo:
            status = payload.get("status")
            summary = payload.get("summary")
            if status == "needs_input" and self.ask is None:
                self.ask = Ask(
                    question=str(summary or f"Which did you mean for {name}?"),
                    candidates=list(payload.get("candidates") or []),
                    tool=name,
                )
            elif status in ("partial", "degraded") and summary:
                self._note(f"{name} ({status}): {summary}")
            return
        for note in payload.get("notes") or []:
            self._note(str(note))
        if name == "build_crowd_profile":
            self.profile_id = str(payload.get("profile_id") or "") or self.profile_id
            if payload.get("data_quality") in ("partial", "degraded"):
                self._note(f"Crowd profile is {payload['data_quality']}.")
        elif name == "generate_candidates":
            run_id = str(payload.get("run_id") or "")
            self.last_run_id = run_id or self.last_run_id
            if payload.get("reason"):
                self._note(str(payload["reason"]))
            if payload.get("unresolved_tags") and self.ask is None and run_id in self.store.runs:
                unresolved = self.store.runs[run_id].candidates.unresolved_tags
                self.ask = Ask(
                    question=(
                        "Which Qloo tag is meant for "
                        + ", ".join(repr(t.query) for t in unresolved)
                        + "?"
                    ),
                    candidates=[
                        {
                            "for_input": t.query,
                            "id": c.entity_id,
                            "name": c.name,
                            "type": c.type,
                        }
                        for t in unresolved
                        for c in t.candidates[:5]
                    ],
                    tool=name,
                )
        elif name == "score_and_rank":
            self.ranked_run_id = str(payload.get("run_id") or "") or self.ranked_run_id

    def _note(self, note: str) -> None:
        if note and note not in self.notes:
            self.notes.append(note)


# --- the run ---------------------------------------------------------------------------


def _ratio(sold: int | None, capacity: int | None) -> str:
    if sold is None or not capacity:
        return "sell-through unknown"
    return f"sold {min(sold, capacity) / capacity:.0%}"


def _prompt(
    venue: VenueInput,
    venue_id: str,
    acts: Sequence[tuple[str, ResolvedEntity]],
    venue_entity: ResolvedEntity | None,
    constraints: Constraints,
    user_message: str,
) -> str:
    sold_by_act = {s.act.casefold(): s for s in venue.past_shows}
    lines = [
        f"Venue: {venue.name}, {venue.city}, {venue.country}; capacity {venue.capacity}; "
        f"venue_id {venue_id}.",
        "Qloo venue entity: "
        + (
            f"{venue_entity.canonical_name} ({venue_entity.entity_id})"
            if venue_entity and venue_entity.entity_id
            else "none (profile from location and past acts)"
        )
        + ".",
    ]
    if acts:
        lines.append("Past acts (resolved_id: canonical name, confirmed?, sell-through):")
        for resolved_id, act in acts:
            show = sold_by_act.get(act.query.casefold())
            ratio = _ratio(show.tickets_sold, show.capacity) if show else "sell-through unknown"
            state = (
                "confirmed"
                if act.confirmed
                else (
                    "UNCONFIRMED, candidates: "
                    + ", ".join(f"{c.name} ({c.entity_id})" for c in act.candidates[:5])
                )
            )
            lines.append(f"- {resolved_id}: {act.canonical_name}, {state}, {ratio}")
    else:
        lines.append("Past acts: none.")
    lines.append(f"Constraints JSON: {constraints.model_dump_json()}")
    lines.append(f"Request: {user_message.strip() or 'Shortlist acts for this venue.'}")
    return "\n".join(lines)


_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def _text_of(message: Message | None) -> str:
    if not message:
        return ""
    return "".join(str(b["text"]) for b in message.get("content") or [] if "text" in b)


def _parse_answer(text: str) -> FinalAnswer | None:
    match = _JSON_OBJECT.search(text)
    if not match:
        return None
    try:
        return FinalAnswer.model_validate_json(match.group(0))
    except ValidationError:
        return None


def _worst(*qualities: DataQuality) -> DataQuality:
    return max(qualities, key=lambda q: QUALITY_ORDER[q]) if qualities else "ok"


def _usage_of(result: StrandsResult | None, metered: MeteredModel) -> dict[str, int]:
    usage: dict[str, Any] = dict(result.metrics.accumulated_usage) if result else {}

    def count(key: str) -> int:
        value = usage.get(key)
        return int(value) if isinstance(value, int) else 0

    return {
        "input_tokens": count("inputTokens"),
        "output_tokens": count("outputTokens"),
        "cache_read_tokens": count("cacheReadInputTokens"),
        "cache_write_tokens": count("cacheWriteInputTokens"),
        "model_calls": metered.calls,
        "tokens_counted": metered.tokens,
    }


def _finalise(
    guard: RunGuard,
    store: RunStore,
    result: StrandsResult | None,
    *,
    stop_reason: str,
    error: str | None,
    metered: MeteredModel,
) -> AgentResult:
    """Build the result from the store and the guard; the model's JSON is read, not trusted."""
    text = _text_of(result.message if result else None)
    answer = _parse_answer(text)
    notes = list(guard.notes)
    if answer is not None:
        for note in answer.notes:
            if note not in notes:
                notes.append(note)
    usage = _usage_of(result, metered)
    common: dict[str, Any] = {
        "stop_reason": stop_reason,
        "steps": guard.steps,
        "trace": guard.trace,
        "usage": usage,
        "model_answer": text,
        "run_id": guard.ranked_run_id or guard.last_run_id,
        "profile_id": guard.profile_id,
        "store": store,
    }

    if guard.ask is not None:
        return AgentResult(
            status="ask",
            shortlist=[],
            notes=notes,
            data_quality="partial",
            ask=guard.ask,
            error=None,
            **common,
        )
    if error is None and stop_reason == "refusal":
        error = "The model refused the request (stop_reason refusal)."
    if error is None and guard.ranked_run_id is None:
        error = (
            "The run ended without score_and_rank: the agent never ranked, so there is no "
            "shortlist to report."
            if answer is None or not answer.shortlist
            else "Final answer rejected: it lists a shortlist but score_and_rank was never called."
        )
    if error is not None:
        return AgentResult(
            status="error",
            shortlist=[],
            notes=notes,
            data_quality=None,
            ask=None,
            error=error,
            **common,
        )

    assert guard.ranked_run_id is not None
    run = store.run(guard.ranked_run_id)
    profile = store.profile(run.profile_id)
    reasons = {e.candidate_id: e.reason for e in (answer.shortlist if answer else [])}
    shortlist: list[dict[str, Any]] = []
    for i, item in enumerate((run.ranked or [])[:SHORTLIST]):
        candidate_id = run.candidate_ids[item.candidate.entity_id]
        shortlist.append(
            {
                "candidate_id": candidate_id,
                "entity_id": item.candidate.entity_id,
                "name": item.candidate.name,
                "total": round(item.score.total, 4),
                "reason": item.score.top_reason if i < 5 else reasons.get(candidate_id),
            }
        )
    expected = [e["candidate_id"] for e in shortlist]
    if answer is None:
        notes.append(
            "The model's final answer was not the expected JSON; built from the ranked run."
        )
    elif [e.candidate_id for e in answer.shortlist] != expected[: len(answer.shortlist)]:
        notes.append("Shortlist order restored from score_and_rank (the model had reordered it).")
    if guard.forced_stop == "step_limit":
        notes.append(f"Stopped at the {MAX_STEPS}-step limit; the shortlist is as ranked.")
    data_quality = _worst(profile.data_quality, run.candidates.candidates_quality)
    if run.signals is None:
        data_quality = _worst(data_quality, "partial")
        notes.append("Signals were not gathered; past_fit, local_heat and momentum are neutral.")
    return AgentResult(
        status="ok",
        shortlist=shortlist,
        notes=notes,
        data_quality=data_quality,
        ask=None,
        error=None,
        **common,
    )


async def run_venue_agent(
    venue: VenueInput,
    constraints: Constraints,
    user_message: str,
    *,
    qloo: QlooExec,
    acts: Sequence[ResolvedEntity] = (),
    venue_entity: ResolvedEntity | None = None,
    model: Model | None = None,
    token_counter: DailyCounter | None = None,
    token_cap: int | None = None,
    today: dt.date | None = None,
    store: RunStore | None = None,
) -> AgentResult:
    """One bounded agent run for `venue` under `constraints`, answering `user_message`.

    `acts` are the venue's past acts as the resolve step (T07) left them; `venue_entity` is
    its Qloo place when known. `qloo` is the gateway both tool sets share: `QlooMCP` in the
    service (its `tools()` are added to the agent), `QlooExec` in tests without a server.
    Raises `BudgetExceeded` before any model call when today's LLM tokens are already at
    the cap; a cap hit during the run comes back as an error result with the trace so far.
    """
    settings = AgentSettings.from_env()
    counter = token_counter if token_counter is not None else token_counter_from_env(settings)
    cap = token_cap if token_cap is not None else int(getattr(counter, "cap", settings.token_cap))
    used = await counter.used()
    if used >= cap:
        raise BudgetExceeded(used, cap, "LLM token")

    store = store or RunStore()
    venue_id = store.add_venue(venue)
    if venue_entity is not None:
        store.set_venue_entity(venue_id, venue_entity)
    seeded = [(store.add_resolved(venue_id, act), act) for act in acts]
    tools = DrawTools(store, qloo, today=today)
    agent_tools: list[Any] = list(tools.tools())
    if isinstance(qloo, QlooMCP):
        agent_tools.extend(qloo.tools())
    guard = RunGuard(store)
    metered = MeteredModel(model or build_model(settings), counter)
    agent = Agent(
        model=metered,
        tools=agent_tools,
        system_prompt=SYSTEM_PROMPT,
        hooks=[guard],
        callback_handler=None,
    )
    prompt = _prompt(venue, venue_id, seeded, venue_entity, constraints, user_message)

    result: StrandsResult | None = None
    error: str | None = None
    stop_reason = "error"
    started = time.monotonic()
    try:
        result = await agent.invoke_async(prompt, limits={"turns": TURN_LIMIT})
        stop_reason = str(result.stop_reason)
    except MaxTokensReachedException:
        stop_reason = "max_tokens"
        error = f"The model hit max_tokens ({MAX_TOKENS}) before finishing its answer."
    except BudgetExceeded as exc:
        stop_reason = "budget"
        error = str(exc)
    except EventLoopException as exc:
        # Strands wraps what a model stream raises; the budget stop is ours, nothing else is.
        if not isinstance(exc.original_exception, BudgetExceeded):
            raise
        stop_reason = "budget"
        error = str(exc.original_exception)
    log.info(
        "venue_agent stop=%s steps=%d tokens=%d ms=%d",
        stop_reason,
        guard.steps,
        metered.tokens,
        int((time.monotonic() - started) * 1000),
    )
    if guard.forced_stop == "needs_input":
        stop_reason = "needs_input"
    elif guard.forced_stop == "step_limit":
        stop_reason = "step_limit"
    return _finalise(guard, store, result, stop_reason=stop_reason, error=error, metered=metered)
