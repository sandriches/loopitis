"""Drive the real agent graph with a scripted model: tools, subagent wiring and
the approval interrupt all run for real; only the LLM is replaced."""

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from loopitis.agent.graph import build_agent
from loopitis.als.parse import parse
from tests.live_set_builder import demo_set


class ScriptedModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def call(name: str, call_id: str, **args) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def test_plan_is_written_only_after_approval(tmp_path):
    path = str(demo_set(tmp_path / "demo.als"))
    plan = {
        "sections": [
            {"name": "Intro", "bars": 8, "clips": ["C1"], "purpose": "drums alone"},
            {"name": "Drop", "bars": 16, "clips": ["C1", "C2", "C3"], "purpose": "everything"},
        ]
    }
    model = ScriptedModel(
        messages=iter(
            [
                call("open_project", "1", path=path),
                call("preview_arrangement", "2", path=path, plan=plan),
                call("write_arrangement", "3", path=path, plan=plan),
                AIMessage(content="Written."),
            ]
        )
    )
    agent = build_agent(model=model, analyst_model=model, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t"}}

    state = agent.invoke({"messages": [{"role": "user", "content": f"Arrange {path}"}]}, config)

    assert "__interrupt__" in state
    request = state["__interrupt__"][0].value["action_requests"][0]
    assert request["name"] == "write_arrangement"
    assert not (tmp_path / "demo (arranged).als").exists()
    tool_output = {m.name: m.content for m in state["messages"] if m.type == "tool"}
    assert "| 2 | Drop | 9–24 | 16 bars | everything |" in tool_output["preview_arrangement"]

    state = agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), config)

    written = tmp_path / "demo (arranged).als"
    assert written.exists()
    assert len(parse(written).track(0).arrangement_clips) == 1
    assert state["messages"][-1].content == "Written."
