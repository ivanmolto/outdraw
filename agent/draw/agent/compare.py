"""The LLM-only shortlist (T23): what Claude Haiku 5.5 suggests with no tools and no Qloo data.

`llm_only_shortlist(venue, constraints, ...)` makes exactly one model call, with no tools,
asking for `COMPARE_ACTS` acts for the same venue and constraints as a run, and returns a
`CompareOut` labelled `llm_only` throughout. It is the contrast for the shortlist page's
"With Qloo / LLM only" toggle, not a ranking: the order is the model's, nothing is scored,
and every act carries the label so the UI cannot mistake it for Outdraw's shortlist.

Same limits as the agent: `build_model` (Haiku 5.5, `max_tokens`, effort, no sampling),
`MeteredModel` on the shared daily token counter, and the cap checked before the model is
even built (`BudgetExceeded`, 429 at the API). The prompt carries only what the run's
prompt does about the venue: name, location, capacity, past acts with sell-through, the
constraints.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final, Literal

from pydantic import BaseModel, Field, ValidationError
from strands import Agent
from strands.agent.agent_result import AgentResult as StrandsResult
from strands.models import Model
from strands.types.exceptions import EventLoopException, MaxTokensReachedException

from draw.agent.agent import AgentSettings, MeteredModel, build_model, token_counter_from_env
from draw.qloo.budget import DailyCounter
from draw.qloo.envelope import BudgetExceeded
from draw.schemas import Constraints, VenueInput

__all__ = [
    "COMPARE_ACTS",
    "LLM_ONLY",
    "CompareAct",
    "CompareOut",
    "disclaimer",
    "llm_only_shortlist",
]

log = logging.getLogger("draw.agent.compare")

LLM_ONLY: Final = "llm_only"
COMPARE_ACTS: Final = 10


def disclaimer(model_id: str) -> str:
    """The label's long form, naming the model that actually answered (`ANTHROPIC_MODEL`)."""
    return (
        f"LLM only: {model_id}'s own suggestions from one call with no tools and no Qloo "
        "data. Not scored by Outdraw; the order is the model's."
    )


SYSTEM_PROMPT: Final = f"""\
You are a booking assistant for a live-music venue. Suggest {COMPARE_ACTS} real touring \
music acts this venue's crowd would turn out for, from your own knowledge only. Respect \
the constraints: size_tier (emerging, mid or headline), include_tags and exclude_tags as \
genres, and do not suggest an act from the past acts list.

Answer with one JSON object and nothing else:
{{"acts": [{{"name": "...", "reason": "one short sentence"}}]}}
"""

Label = Literal["llm_only"]


class CompareAct(BaseModel):
    name: str = Field(min_length=1)
    reason: str | None = None
    label: Label = LLM_ONLY


class _ModelAct(BaseModel):
    """An act as the model wrote it; anything else it adds (a label, say) is dropped."""

    name: str = Field(min_length=1)
    reason: str | None = None


class _Answer(BaseModel):
    acts: list[_ModelAct]


class CompareOut(BaseModel):
    """`GET /runs/{id}/compare`: the run's LLM-only shortlist, as cached on the run."""

    run_id: str
    label: Label = LLM_ONLY
    disclaimer: str
    model: str
    acts: list[CompareAct] = Field(default_factory=list, max_length=COMPARE_ACTS)
    error: str | None = None
    """Set when the model's answer was unusable; such a result is not cached."""
    token_usage: dict[str, int] = Field(default_factory=dict)
    created_at: datetime
    cached: bool = False


def _ratio(sold: int | None, capacity: int | None) -> str:
    if sold is None or not capacity:
        return "sell-through unknown"
    return f"sold {min(sold, capacity) / capacity:.0%}"


def _prompt(venue: VenueInput, constraints: Constraints) -> str:
    lines = [
        f"Venue: {venue.name}, {venue.city}, {venue.country}; capacity {venue.capacity}.",
    ]
    if venue.past_shows:
        lines.append("Past acts (sell-through):")
        lines.extend(f"- {s.act} ({_ratio(s.tickets_sold, s.capacity)})" for s in venue.past_shows)
    else:
        lines.append("Past acts: none.")
    lines.append(f"Constraints JSON: {constraints.model_dump_json()}")
    lines.append(f"Suggest {COMPARE_ACTS} acts.")
    return "\n".join(lines)


_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def _acts_of(result: StrandsResult | None) -> list[CompareAct] | None:
    if result is None:
        return None
    text = "".join(str(b["text"]) for b in result.message.get("content") or [] if "text" in b)
    match = _JSON_OBJECT.search(text)
    if not match:
        return None
    try:
        answer = _Answer.model_validate_json(match.group(0))
    except ValidationError:
        return None
    # The label is ours, whatever the model wrote.
    return [CompareAct(name=a.name, reason=a.reason) for a in answer.acts[:COMPARE_ACTS]]


async def llm_only_shortlist(
    run_id: str,
    venue: VenueInput,
    constraints: Constraints,
    *,
    model_factory: Callable[[], Model] | None = None,
    token_counter: DailyCounter | None = None,
    token_cap: int | None = None,
) -> CompareOut:
    """One tool-less model call for `venue` under `constraints`, labelled `llm_only`.

    Raises `BudgetExceeded` before the model is built when today's LLM tokens are at the
    cap, and when the call itself takes them over it. `model_factory` (None: Haiku 5.5 from
    the environment) is only called once the budget allows the call.
    """
    settings = AgentSettings.from_env()
    counter = token_counter if token_counter is not None else token_counter_from_env(settings)
    cap = token_cap if token_cap is not None else int(getattr(counter, "cap", settings.token_cap))
    used = await counter.used()
    if used >= cap:
        raise BudgetExceeded(used, cap, "LLM token")

    model = model_factory() if model_factory else build_model(settings)
    metered = MeteredModel(model, counter)
    agent = Agent(model=metered, tools=[], system_prompt=SYSTEM_PROMPT, callback_handler=None)
    result: StrandsResult | None = None
    error: str | None = None
    try:
        result = await agent.invoke_async(_prompt(venue, constraints), limits={"turns": 1})
    except MaxTokensReachedException:
        error = "The model hit max_tokens before finishing its list."
    except EventLoopException as exc:
        if isinstance(exc.original_exception, BudgetExceeded):
            raise exc.original_exception from None
        raise

    acts = _acts_of(result)
    if acts is None and error is None:
        error = "The model's answer was not the expected JSON list of acts."
    log.info("llm_only_compare run=%s acts=%d tokens=%d", run_id, len(acts or []), metered.tokens)
    return CompareOut(
        run_id=run_id,
        disclaimer=disclaimer(settings.model_id),
        model=settings.model_id,
        acts=acts or [],
        error=error,
        token_usage={"model_calls": metered.calls, "tokens_counted": metered.tokens},
        created_at=datetime.now(UTC),
    )
