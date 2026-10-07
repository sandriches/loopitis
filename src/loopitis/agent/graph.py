"""The arrangement deep agent, exported as `agent` for `langgraph dev`."""

import os

from deepagents import create_deep_agent
from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver

from loopitis.agent.prompts import ARRANGER_PROMPT, LOOP_ANALYST_PROMPT
from loopitis.agent.tools import (
    inspect_clips,
    open_project,
    preview_arrangement,
    write_arrangement,
)

load_dotenv()

DEFAULT_MODEL = "claude-opus-5-5"


def build_model(model: str | None = None, effort: str | None = None) -> ChatAnthropic:
    kwargs = {}
    if os.getenv("LOOPITIS_FALLBACKS", "true").lower() == "true":
        # If a safety classifier declines a request, the API retries it on a
        # suitable fallback model instead of returning a refusal.
        kwargs = {
            "betas": ["server-side-fallback-2026-07-01"],
            "model_kwargs": {"extra_body": {"fallbacks": "default"}},
        }
    return ChatAnthropic(
        model=model or os.getenv("LOOPITIS_MODEL", DEFAULT_MODEL),
        reasoning_effort=effort or os.getenv("LOOPITIS_EFFORT", "high"),
        max_tokens=16000,
        **kwargs,
    )


def build_agent(
    model: BaseChatModel | None = None,
    analyst_model: BaseChatModel | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
):
    model = model or build_model()
    analyst_model = analyst_model or build_model(
        model=os.getenv("LOOPITIS_ANALYST_MODEL"),
        effort=os.getenv("LOOPITIS_ANALYST_EFFORT", "medium"),
    )
    return create_deep_agent(
        model=model,
        tools=[open_project, inspect_clips, preview_arrangement, write_arrangement],
        system_prompt=ARRANGER_PROMPT,
        subagents=[
            {
                "name": "loop-analyst",
                "description": (
                    "Analyses the notes of session loops and reports each loop's role, "
                    "energy, feel and arrangement use. Give it the .als path and clip ids."
                ),
                "system_prompt": LOOP_ANALYST_PROMPT,
                "tools": [open_project, inspect_clips],
                "model": analyst_model,
            }
        ],
        interrupt_on={
            "write_arrangement": {"allowed_decisions": ["approve", "edit", "reject"]},
        },
        checkpointer=checkpointer,
        name="loopitis",
    )


agent = build_agent()
