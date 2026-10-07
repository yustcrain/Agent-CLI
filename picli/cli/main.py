"""Run with python -m picli."""

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

from picli.agent.acceptance import format_criteria, format_results
from picli.agent.core import Agent
from picli.memory.compaction import Compactor
from picli.config import AppConfig
from picli.diagnostics import doctor_report, status_report, text_report
from picli.agent.goal import GoalRunner
from picli.providers.client import BaseProvider, create_provider
from picli.cli.terminal import BASE_COMPLETIONS, TerminalSession
from picli.tools.builtin import Tools, sanitize_process_output


HELP = """/help              显示命令
/new [name]        切换到新会话（已有名称会恢复）
/sessions          列出会话
/resume name       恢复会话
/tree              显示当前会话的消息树和节点 ID
/checkout ID       将当前分支切换到指定消息节点
/fork NAME [ID]    从当前或指定节点创建新会话
/model [name]      查看或切换模型
/status            查看运行状态
/doctor            执行本地配置诊断
/config            查看脱敏后的有效配置
/tools             查看已注册工具
/history           显示当前会话
/memory            查看全局和当前会话 Memory
/memory global     查看全局 MEMORY.md
/memory session    查看当前会话 MEMORY.md
/skills            列出项目 Skill
/skill NAME [ARGS] 加载并执行指定 Skill
/permissions       查看当前权限模式和本次运行的决定
/permissions clear 清除本次运行缓存的权限决定
/permissions MODE  切换 read-only、workspace-write 或 danger-full-access
/context           查看上下文占用和摘要状态
/compact           立即压缩较早的对话
/goal              查看当前长期目标
/goal <目标>       创建并持续执行长期目标
/goal resume       恢复暂停或阻塞的目标
/goal criteria     查看验收条件和最近证据
/goal approve ID   人工通过指定的 manual 验收条件
/goal pause        暂停当前目标
/goal cancel       取消当前目标
/exit              退出
Tab 补全命令，Ctrl+J 或 Esc+Enter 插入换行。
输入文字时 Ctrl+C 清空输入；空输入时 Ctrl+C 或 Ctrl+D 退出。"""


def positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def emit(report: dict, output_format: str, title: str) -> None:
    if output_format == "json":
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(text_report(title, report))


def final_assistant(messages: list[dict]) -> str:
    return next(
        (
            str(message.get("content") or "")
            for message in reversed(messages)
            if message.get("role") == "assistant" and message.get("content")
        ),
        "",
    )


def run_prompt(agent: Agent, provider: BaseProvider, prompt: str, output_format: str) -> int:
    if output_format == "text":
        agent.run(prompt)
        return 0
    captured = io.StringIO()
    message_count = len(agent.messages)
    try:
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            finish_reason = agent.run(prompt)
    except (OSError, ValueError) as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "type": "error",
                    "provider": provider.provider_name,
                    "model": provider.model,
                    "error": str(error),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    new_messages = agent.messages[message_count:]
    report = {
        "ok": finish_reason == "model_done",
        "type": "prompt",
        "provider": provider.provider_name,
        "model": provider.model,
        "session": agent.session,
        "finish_reason": finish_reason,
        "assistant": final_assistant(agent.messages),
        "model_messages": sum(message.get("role") == "assistant" for message in new_messages),
        "tool_results": sum(message.get("role") == "tool" for message in new_messages),
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="yu terminal coding agent")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, help="Additional JSON config file")
    parser.add_argument("--provider", choices=("auto", "openai", "openai-compatible", "anthropic", "mock"))
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--auth-token")
    parser.add_argument("--output-format", choices=("text", "json"))
    parser.add_argument("--session")
    parser.add_argument("--prompt", help="Run one prompt and exit")
    parser.add_argument("--demo", action="store_true", help="Compatibility alias for --provider mock")
    parser.add_argument(
        "--allow-bash",
        action="store_true",
        help="Compatibility flag; workspace-write already exposes Bash behind permission checks",
    )
    parser.add_argument(
        "--permission-mode",
        choices=("read-only", "workspace-write", "danger-full-access"),
    )
    parser.add_argument(
        "--dangerously-skip-permissions",
        action="store_true",
        help="Use danger-full-access; hard DENY safety rules still apply",
    )
    parser.add_argument("--max-turns", type=positive)
    parser.add_argument("--tool-timeout", type=positive)
    parser.add_argument("--context-window", type=positive)
    parser.add_argument("--compact-at", type=float)
    parser.add_argument("--keep-turns", type=positive)
    parser.add_argument("--goal-cycles", type=positive)
    parser.add_argument("command", nargs="?", help="prompt, status, doctor, config, or tools")
    parser.add_argument("command_values", nargs="*")
    args = parser.parse_args()
    workspace = args.workspace.expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    try:
        config = AppConfig.load(workspace, args.config).override(
            provider="mock" if args.demo else args.provider,
            model=args.model,
            base_url=args.base_url,
            auth_token=args.auth_token,
            output_format=args.output_format,
            permission_mode=args.permission_mode,
            max_turns=args.max_turns,
            tool_timeout=args.tool_timeout,
            context_window=args.context_window,
            compact_at=args.compact_at,
            keep_turns=args.keep_turns,
            goal_cycles=args.goal_cycles,
        )
    except ValueError as error:
        parser.error(str(error))
    permission_mode = (
        "danger-full-access"
        if args.dangerously_skip_permissions
        else config.permission_mode
    )
    if permission_mode != config.permission_mode:
        config = config.override(permission_mode=permission_mode)
    tools = Tools(
        workspace,
        args.allow_bash or permission_mode != "read-only",
        config.tool_timeout,
        permission_mode=permission_mode,
    )
    command = args.command
    if command not in {None, "prompt", "status", "doctor", "config", "tools"}:
        parser.error(f"Unknown command: {command}")
    if command == "config":
        emit({"ok": True, **config.public()}, config.output_format, "Effective configuration")
        return 0
    if command == "doctor":
        report = doctor_report(config, workspace, tools)
        emit(report, config.output_format, "yu doctor")
        return 0 if report["ok"] else 2
    if command == "tools":
        report = {
            "ok": True,
            "tools": [
                {"name": spec.name, "description": spec.description, "fields": list(spec.fields)}
                for spec in tools.registry.specs()
            ],
        }
        emit(report, config.output_format, "Registered tools")
        return 0
    if command == "status":
        emit(status_report(config, workspace, tools, args.session), config.output_format, "yu status")
        return 0
    if command == "prompt":
        if not args.command_values:
            parser.error("prompt requires text")
        if args.prompt is not None:
            parser.error("Use either prompt TEXT or --prompt TEXT")
        args.prompt = " ".join(args.command_values)
    elif args.command_values:
        parser.error("Unexpected positional arguments")
    if config.output_format == "json" and args.prompt is None:
        parser.error("JSON output requires a direct command or one-shot prompt")
    if config.provider != "mock" and not config.model:
        parser.error("Set --model or MODEL, or use --provider mock")
    provider = create_provider(
        config.provider,
        config.base_url,
        config.api_key,
        config.model,
        args.demo,
        config.auth_token,
    )
    compactor = Compactor(config.context_window, config.compact_at, config.keep_turns)
    agent = Agent(
        workspace,
        provider,
        tools,
        args.session,
        config.max_turns,
        compactor,
    )
    goals = GoalRunner(agent, config.goal_cycles)

    def completion_candidates() -> list[str]:
        candidates = list(BASE_COMPLETIONS)
        candidates.extend(f"/resume {name}" for name in agent.store.list_names())
        candidates.extend(f"/new {name}" for name in agent.store.list_names())
        candidates.extend(f"/checkout {node['id'][:8]}" for node in agent.store.branch_nodes())
        candidates.extend(f"/skill {skill.name}" for skill in agent.tools.skills.list())
        goal = goals.current()
        if goal is not None:
            candidates.extend(
                f"/goal approve {criterion['id']}"
                for criterion in goal.criteria
                if criterion["check"]["type"] == "manual"
            )
        return candidates

    terminal = TerminalSession(
        workspace / ".pi-python" / "input-history",
        completion_candidates,
        lambda: (
            f" model: {provider.model} | "
            f"permission: {agent.tools.permission_mode.value} | Tab 补全 | Ctrl+J 换行"
        ),
    )
    if config.output_format == "text":
        print(
            f"yu | provider: {provider.provider_name} | model: {provider.model}"
            f"\nPermission: {agent.tools.permission_mode.value}"
            f"\nWorkspace: {workspace}\nSession: {agent.session}"
        )
    if args.prompt is not None:
        return run_prompt(agent, provider, args.prompt, config.output_format)
    print("输入 /help 查看命令。")
    while True:
        try:
            value_read = terminal.read()
            if value_read is None:
                print()
                return 0
            prompt = value_read.strip()
            if not prompt:
                continue
            command, _, value = prompt.partition(" ")
            value = value.strip()
            if command == "/exit":
                return 0
            if command == "/help":
                print(HELP)
            elif command in {"/new", "/resume"}:
                if command == "/resume" and not value:
                    raise ValueError("Usage: /resume name")
                agent.select(value or None)
                print(f"Session: {agent.session}")
            elif command == "/sessions":
                print("\n".join(name for name in agent.store.list_names()) or "暂无已保存会话")
            elif command == "/tree":
                if value:
                    raise ValueError("Usage: /tree")
                print(sanitize_process_output(agent.store.format_tree()))
            elif command == "/checkout":
                if not value:
                    raise ValueError("Usage: /checkout <node-id>")
                identifier = agent.store.checkout(value)
                print(f"当前分支已切换到节点 {identifier[:8]}。下一条消息会从这里创建新分支。")
            elif command == "/fork":
                name, _, node = value.partition(" ")
                if not name:
                    raise ValueError("Usage: /fork <new-session-name> [node-id]")
                agent.store.fork(name, node.strip() or None)
                print(f"已创建并切换到会话 {agent.session}。")
            elif command == "/model":
                provider.model = value or provider.model
                print(provider.model or "demo")
            elif command == "/status":
                if value:
                    raise ValueError("Usage: /status")
                print(text_report("yu status", status_report(config, workspace, tools, agent.session)))
            elif command == "/doctor":
                if value:
                    raise ValueError("Usage: /doctor")
                print(text_report("yu doctor", doctor_report(config, workspace, tools)))
            elif command == "/config":
                if value:
                    raise ValueError("Usage: /config")
                print(text_report("Effective configuration", {"ok": True, **config.public()}))
            elif command == "/tools":
                if value:
                    raise ValueError("Usage: /tools")
                print(
                    "\n".join(
                        f"{spec.name}: {spec.description} ({', '.join(spec.fields)})"
                        for spec in tools.registry.specs()
                    )
                )
            elif command == "/history":
                for node in agent.store.branch_nodes():
                    message = node["message"]
                    content = sanitize_process_output(str(message.get("content") or "[tool calls]"))
                    print(f"{node['id'][:8]} {message['role']}> {content}")
            elif command == "/memory":
                global_path = workspace / "MEMORY.md"
                session_path = agent.store.session_memory_path
                if value not in {"", "global", "session"}:
                    raise ValueError("Usage: /memory [global|session]")
                if value in {"", "global"}:
                    content = global_path.read_text(encoding="utf-8") if global_path.is_file() else "(empty)"
                    print("[global MEMORY.md]\n" + content)
                if value in {"", "session"}:
                    content = session_path.read_text(encoding="utf-8") if session_path.is_file() else "(empty)"
                    print(f"[session {agent.session} MEMORY.md]\n" + content)
            elif command == "/context":
                print(
                    f"约 {agent.context_tokens()} / {compactor.context_window} tokens；"
                    f"自动压缩阈值 {compactor.threshold}；摘要 {'已有' if agent.summary else '暂无'}。"
                )
            elif command == "/compact":
                if not agent.compact(force=True):
                    print(f"至少需要超过 {compactor.keep_turns} 个用户轮次才能压缩。")
            elif command == "/skills":
                agent.tools.skills.refresh()
                skills = agent.tools.skills.list()
                print(
                    "\n".join(f"{skill.name}: {skill.description}" for skill in skills)
                    or "暂无项目 Skill"
                )
                if agent.tools.skills.diagnostics:
                    print("诊断:\n" + "\n".join(agent.tools.skills.diagnostics))
            elif command == "/skill":
                name, _, arguments = value.partition(" ")
                if not name:
                    raise ValueError("Usage: /skill <name> [arguments]")
                agent.tools.skills.refresh()
                agent.run_skill(name, arguments.strip())
            elif command == "/permissions":
                if value == "clear":
                    agent.tools.permissions.clear()
                    print("已清除本次运行缓存的权限决定。")
                elif value in {"read-only", "workspace-write", "danger-full-access"}:
                    mode = agent.tools.set_permission_mode(value)
                    print(f"权限模式已切换为 {mode.value}。")
                elif value:
                    raise ValueError(
                        "Usage: /permissions [clear|read-only|workspace-write|danger-full-access]"
                    )
                else:
                    print(f"Permission mode: {agent.tools.permission_mode.value}")
                    print(agent.tools.permissions.status())
            elif command == "/goal":
                if not value or value == "status":
                    goal = goals.current()
                    if goal is None:
                        print("当前会话没有长期目标。用 /goal <目标> 创建。")
                    else:
                        print(f"状态: {goal.status}；本次 cycles: {goal.cycle}/{goal.max_cycles}")
                        print(f"目标: {goal.objective}")
                        if goal.progress:
                            print(f"进度: {goal.progress}")
                        if goal.last_error:
                            print(f"信息: {goal.last_error}")
                elif value == "criteria":
                    goal = goals.current()
                    if goal is None:
                        print("当前会话没有长期目标。")
                    else:
                        print("验收条件:\n" + format_criteria(goal.criteria))
                        print(
                            "最近证据:\n"
                            + (format_results(goal.verification) if goal.verification else "尚未执行验收。")
                        )
                elif value == "resume":
                    goals.resume()
                elif value.startswith("approve "):
                    criterion_id = value.removeprefix("approve ").strip().upper()
                    goal = goals.approve(criterion_id)
                    print(f"已记录 {criterion_id} 的人工验收；状态: {goal.status}。用 /goal resume 复验。")
                elif value == "pause":
                    goal = goals.pause()
                    print(f"Goal {goal.status}。")
                elif value == "cancel":
                    goal = goals.cancel()
                    print(f"Goal {goal.status}。")
                else:
                    goals.start(value)
            elif command.startswith("/"):
                print("未知命令，输入 /help 查看帮助。")
            else:
                agent.run(prompt)
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print("\n已中断。")
        except (OSError, ValueError) as error:
            print(f"Error: {error}", file=sys.stderr)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        sys.exit(130)
