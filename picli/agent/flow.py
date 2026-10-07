"""PocketFlow nodes and graph for the coding-agent loop."""

import json


from pocketflow import Flow, Node

from picli.memory.compaction import CompactionPlan, Compactor
from picli.providers.client import BaseProvider
from picli.memory.session import Session
from picli.cli.terminal import StreamingMarkdownRenderer
from picli.tools.builtin import Tools


class RunCancelled(Exception):
    """Cooperative cancellation between model/tool operations."""


class AddPromptNode(Node):
    def prep(self, shared: dict) -> str:
        return shared["prompt"]

    def exec(self, prompt: str) -> str:
        return prompt

    def post(self, shared: dict, prep_res: str, exec_res: str) -> None:
        session: Session = shared["session"]
        session.messages.append({"role": "user", "content": exec_res})
        session.save()


class CompactContextNode(Node):
    def __init__(self, provider: BaseProvider, compactor: Compactor, tools: Tools, event_sink=None, cancel_check=None):
        super().__init__()
        self.provider = provider
        self.compactor = compactor
        self.event_sink = event_sink
        self.cancel_check = cancel_check
        self.tools = tools

    def prep(self, shared: dict) -> tuple[CompactionPlan, list[dict]] | None:
        if self.cancel_check and self.cancel_check():
            raise RunCancelled()
        session: Session = shared["session"]
        plan = self.compactor.plan(
            session.system_message(self.tools.allow_bash, self.tools.permission_mode.value),
            session.summary,
            session.messages,
            bool(shared.get("force_compaction", False)),
        )
        if plan is None:
            return None
        return plan, self.compactor.prompt(session.summary, plan.compact)

    def exec(self, prep_res: tuple[CompactionPlan, list[dict]] | None) -> str | None:
        if prep_res is None:
            return None
        return self.provider.summarize(prep_res[1])

    def post(
        self,
        shared: dict,
        prep_res: tuple[CompactionPlan, list[dict]] | None,
        exec_res: str | None,
    ) -> None:
        shared["compacted"] = prep_res is not None
        if prep_res is None or exec_res is None:
            return
        plan = prep_res[0]
        session: Session = shared["session"]
        session.apply_compaction(plan, exec_res)
        if self.event_sink:
            self.event_sink("context.compacted", {"summarized": len(plan.compact)})
            return
        print(
            f"[context compacted: {len(plan.compact)} messages summarized, "
            f"{len(plan.keep)} recent messages kept]",
            flush=True,
        )


class CallModelNode(Node):
    def __init__(self, provider: BaseProvider, tools: Tools, event_sink=None, cancel_check=None):
        super().__init__()
        self.provider = provider
        self.tools = tools
        self.streamed_text = False
        self.event_sink = event_sink
        self.cancel_check = cancel_check

    def prep(self, shared: dict) -> list[dict]:
        if self.cancel_check and self.cancel_check():
            raise RunCancelled()
        session: Session = shared["session"]
        return session.context(self.tools.allow_bash, self.tools.permission_mode.value)

    def exec(self, context: list[dict]) -> dict:
        self.streamed_text = False
        if self.event_sink:
            def on_text(text):
                self.streamed_text = True
                self.event_sink("message.delta", {"text": text})
            return self.provider.complete(context, on_text=on_text)
        renderer = StreamingMarkdownRenderer()
        renderer.start()
        try:
            return self.provider.complete(context, on_text=renderer.write)
        finally:
            renderer.finish()
            self.streamed_text = renderer.has_text

    def post(self, shared: dict, prep_res: list[dict], exec_res: dict) -> str:
        session: Session = shared["session"]
        session.messages.append(exec_res)
        session.save()
        shared["model_turns"] += 1
        if exec_res.get("content") and not self.streamed_text:
            if self.event_sink:
                self.event_sink("message.delta", {"text": exec_res["content"]})
            else:
                print(f"\nassistant> {exec_res['content']}", flush=True)
        shared["tool_calls"] = exec_res.get("tool_calls", [])
        if shared["tool_calls"]:
            return "tools"
        shared["finish_reason"] = "model_done"
        return "done"


class ExecuteToolsNode(Node):
    def __init__(self, tools: Tools, event_sink=None, cancel_check=None):
        super().__init__()
        self.tools = tools
        self.event_sink = event_sink
        self.cancel_check = cancel_check

    def prep(self, shared: dict) -> list[dict]:
        return shared["tool_calls"]

    def exec(self, calls: list[dict]) -> tuple[list[dict], bool]:
        results = []
        interrupted = False
        for call in calls:
            name = call["function"]["name"]
            if self.event_sink:
                self.event_sink("tool.started", {"id": call["id"], "name": name})
            else:
                print(f"  [tool: {name}]", flush=True)
            try:
                if interrupted or (self.cancel_check and self.cancel_check()):
                    result = "Tool cancelled by user"
                else:
                    arguments = json.loads(call["function"]["arguments"])
                    result = self.tools.execute(name, arguments)
            except ValueError as error:
                result = f"Tool error: {error}"
            except (KeyboardInterrupt, EOFError):
                interrupted = True
                result = "Tool interrupted; side effects may already have occurred"
            results.append({"role": "tool", "tool_call_id": call["id"], "content": result})
            if self.event_sink:
                self.event_sink("tool.completed", {"id": call["id"], "result": result})
            else:
                print(f"  {result[:500]}", flush=True)
        return results, interrupted

    def post(
        self,
        shared: dict,
        prep_res: list[dict],
        exec_res: tuple[list[dict], bool],
    ) -> str:
        results, interrupted = exec_res
        session: Session = shared["session"]
        session.messages.extend(results)
        session.save()
        if interrupted:
            raise KeyboardInterrupt
        if shared["model_turns"] >= shared["max_turns"]:
            shared["finish_reason"] = "turn_limit"
            return "limit"
        return "continue"


class FinishNode(Node):
    def __init__(self, event_sink=None):
        super().__init__()
        self.event_sink = event_sink

    def post(self, shared: dict, prep_res: None, exec_res: None) -> None:
        if self.event_sink:
            return
        if shared.get("finish_reason") == "turn_limit":
            print(
                f"\n[agent 本轮暂停 | 达到模型调用上限 "
                f"{shared['model_turns']}/{shared['max_turns']} | "
                "任务可能未完成，会话已保存]",
                flush=True,
            )
            return
        if shared.get("finish_reason") == "model_done":
            print(
                f"\n[agent 本轮结束 | 模型未继续调用工具 | "
                f"model turns: {shared['model_turns']}/{shared['max_turns']}]",
                flush=True,
            )


def create_agent_flow(provider: BaseProvider, tools: Tools, compactor: Compactor, event_sink=None, cancel_check=None) -> Flow:
    add_prompt = AddPromptNode()
    compact = CompactContextNode(provider, compactor, tools, event_sink, cancel_check)
    call_model = CallModelNode(provider, tools, event_sink, cancel_check)
    execute_tools = ExecuteToolsNode(tools, event_sink, cancel_check)
    finish = FinishNode(event_sink)

    add_prompt >> compact >> call_model
    call_model.next(execute_tools, "tools")
    call_model.next(finish, "done")
    execute_tools.next(compact, "continue")
    execute_tools.next(finish, "limit")
    return Flow(start=add_prompt)
