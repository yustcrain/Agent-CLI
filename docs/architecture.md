# 项目架构

[返回项目首页](../README.md)

## 能力和范围

- 多轮对话使用 SSE 流式显示模型文本，自动执行文件、命令和 Skill 工具，并将结果回传给模型。
- Provider 接口支持原生 Anthropic、OpenAI-compatible 和完全离线的确定性 Mock 实现。
- 工具通过 `ToolRegistry` 注册，Agent Loop 不包含具体工具分支；新增工具只需要注册 schema 和 handler。
- 交互终端使用 `prompt_toolkit` 提供持久输入历史、Tab 命令补全和多行编辑；TTY 中使用 `rich`
  实时渲染 Markdown、代码块和等待状态，非交互输出自动退回纯文本。
- read/write/edit 在工作区内自动允许；绝对路径、`~` 和越出工作区的相对路径会请求权限。
  edit 仍要求原文唯一匹配。
- 默认使用 `workspace-write`：工作区普通文件操作直接执行，shell、敏感文件和工作区外操作按规则请求授权。
  `read-only` 禁止写入和 shell；`danger-full-access` 自动放行 ASK，但无法绕过固定 DENY 规则。
  该机制**不是沙箱**；命令可以访问当前系统账户有权限访问的范围，超时默认 60 秒。
- 使用 SQLite 保存带 `id/parent_id` 的会话树，支持恢复、切换节点和 fork 分支；
  通过版本检查阻止并发覆盖，大文本保存为内容文件。
- 每轮加载工作区根目录的全局 `MEMORY.md` 和当前会话专属 `MEMORY.md`。
- `/new`、`/model`、`/status`、`/doctor`、`/config`、`/tools`、`/history`、`/memory`、`/skills`、
  `/skill`、`/permissions`、`/context`、`/compact`、`/goal`、`/help`、`/exit`。
- `Ctrl+J` 或 `Esc+Enter` 插入换行；输入内容时 `Ctrl+C` 清空当前输入，空输入时 `Ctrl+C` 退出，
  `Ctrl+D` 退出。输入历史保存在 `workspace/.pi-python/input-history`。
- 每个用户请求默认最多 20 次模型调用，`--max-turns` 可调整。
- 工具结果截断到 24,000 字符，终端预览最多 500 字符。

这是可运行的核心版本；普通回复使用 SSE 流式响应，摘要和 Goal 合同使用完整响应；未移植完整 OAuth
授权流程、MCP、插件系统、子 Agent、定时任务、Slack 或完整 TUI。长会话可用 `/new` 切换，长期信息存入 MEMORY.md。
获得授权后的文件写入会直接生效，会话含原始对话和工具结果。

## PocketFlow 工作流

每条用户输入由一个共享状态字典贯穿以下节点：

```text
AddPrompt → CompactContext → CallModel
                              ├─ done  → Finish
                              └─ tools → ExecuteTools ─┬─ continue → CompactContext
                                                       └─ limit    → Finish
```

Goal 使用外层 PocketFlow 包装上述 Agent 流程：

```text
PrepareGoal → AgentFlow → VerifyAcceptance
     ↑                         ├─ failed, budget left → PrepareGoal
     │                         ├─ failed, limit reached → Finish(paused)
     │                         ├─ pending/blocked → Finish(blocked)
     └─────────────────────────└─ all passed → Finish(complete)
```

节点遵循 PocketFlow 的 `prep → exec → post` 生命周期：`prep` 从共享状态准备输入，
`exec` 只执行模型或工具计算，`post` 写回会话并选择下一条边。模型与工具接口仍保持独立，
CLI 通过 Agent 接口调用该流程；启动脚本从 `.env` 加载配置。


## 源码阅读顺序

1. [Agent 外观接口](../picli/agent/core.py) 与 [执行图](../picli/agent/flow.py)。
2. [模型适配](../picli/providers/client.py) 与 [工具注册](../picli/tools/registry.py)。
3. [会话上下文](../picli/memory/session.py)、[压缩](../picli/memory/compaction.py) 和 [数据库](../picli/memory/session_database.py)。
4. [Goal 状态机](../picli/agent/goal.py) 与 [验收检查](../picli/agent/acceptance.py)。
5. [CLI 交互](../picli/cli/main.py) 与 [终端展示](../picli/cli/terminal.py)。
