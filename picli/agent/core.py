"""CLI-facing facade around the PocketFlow agent graph."""

from pathlib import Path

from picli.memory.compaction import Compactor
from picli.agent.flow import CompactContextNode, create_agent_flow
from picli.providers.client import BaseProvider
from picli.memory.sqlite_session import SQLiteSession
from picli.tools.builtin import Tools


class Agent:
    def __init__(
        self,
        workspace: Path,
        provider: BaseProvider,
        tools: Tools,
        session: str | None,
        max_turns: int,
        compactor: Compactor,
        event_sink=None,
        cancel_check=None,
        store=None,
    ):
        self.tool_observer = None  # Optional audit sink, independent of compacted context.
        self.provider = provider
        self.tools = tools
        if hasattr(provider, "set_tool_schemas"):
            provider.set_tool_schemas(tools.schemas)
        self.max_turns = max_turns
        self.compactor = compactor
        self.store = store if store is not None else SQLiteSession(workspace, session, tools.skills)
        self.flow = create_agent_flow(provider, tools, compactor, event_sink, cancel_check)

    @property
    def directory(self) -> Path:
        return self.store.directory

    @property
    def messages(self) -> list[dict]:
        return self.store.messages

    @property
    def summary(self) -> str:
        return self.store.summary

    @property
    def session(self) -> str:
        return self.store.name

    def select(self, session: str | None = None) -> None:
        self.store.select(session)

    def context_tokens(self) -> int:
        return self.store.context_tokens(
            self.tools.allow_bash,
            self.tools.permission_mode.value,
        )

    def compact(self, force: bool = False) -> bool:
        shared = {"session": self.store, "force_compaction": force, "compacted": False}
        CompactContextNode(self.provider, self.compactor, self.tools).run(shared)
        return shared["compacted"]

    def run(self, prompt: str) -> str:
        shared = {
            "session": self.store,
            "prompt": prompt,
            "model_turns": 0,
            "max_turns": self.max_turns,
            "tool_calls": [],
            "tool_observer": self.tool_observer,
            "finish_reason": "",
        }
        self.flow.run(shared)
        return shared.get("finish_reason", "unknown")

    def run_skill(self, name: str, arguments: str = "") -> None:
        skill = self.tools.skills.get(name)
        content = self.tools.skills.read(skill.name)
        prompt = (
            f"Apply the following project skill to the user's request.\n\n"
            f"<skill name=\"{skill.name}\" path=\"{skill.path.relative_to(self.store.workspace)}\">\n"
            f"{content}\n</skill>"
        )
        if arguments:
            prompt += f"\n\nUser request:\n{arguments}"
        self.run(prompt)
