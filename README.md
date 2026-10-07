# yu CLI

yu 是基于 Python 和 PocketFlow 的命令行编程 Agent。
提供模型流式响应、文件与 Shell 工具、权限检查、项目技能、上下文压缩、
持久化会话及 Goal 执行与验收。支持 Python 3.10+、macOS/Linux。

## 快速启动

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 离线演示，不调用真实模型
python -m picli --workspace ./workspace --demo

# 连接模型：在当前终端导出配置后运行
export PROVIDER='openai-compatible'
export MODEL='你的模型 ID'
export BASE_URL='https://你的服务地址/v1'
export API_KEY='你的 API Key'
python -m picli --workspace ./workspace
```

双击 `start.command` 可启动 CLI。
启动脚本实现在 `scripts/`。直接运行 `python -m picli` 时读取环境变量；
启动脚本还会加载根目录中可选的 `.env`，已导出的同名配置优先。

工具权限是应用层检查，不是操作系统沙箱。

## 代码目录

| 目录 | 职责 |
|---|---|
| `picli/agent/` | Agent 循环、Goal 状态机、确定性检查与人工验收 |
| `picli/providers/` | 模型协议适配、流式响应、Mock Provider |
| `picli/tools/` | 工具定义、注册分发、文件/Shell 执行和权限策略 |
| `picli/memory/` | 会话树、压缩、SQLite、内容文件、备份与回收 |
| `picli/skills/` | 项目技能发现、校验与加载 |
| `picli/cli/` | 参数、交互命令、终端输入与展示 |
| `picli/config.py`、`diagnostics.py` | 共享配置与运行诊断 |
| `tests/` | 按 Agent、存储、CLI 和评估模块分组的测试 |
| `evals/` | 模型评估执行器、场景、fixture、独立检查和参考答案 |
| `scripts/` | 启动与评估脚本 |
| `docs/` | 专题文档 |
| `workspace/` | 默认本地运行工作区，包含用户操作文件，不是应用源码 |

## 文档导航

- [使用与配置](docs/usage.md)
- [架构与执行流程](docs/architecture.md)
- [权限策略](docs/permissions.md)
- [上下文、会话与存储](docs/storage.md)
- [持续目标与验收](docs/goals.md)
- [项目技能](docs/skills.md)
- [评估说明](docs/evaluation.md) · [评估执行细节](evals/README.md)

## 测试与维护

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m picli.memory.maintenance --workspace ./workspace gc
.venv/bin/python -m picli.memory.maintenance --workspace ./workspace backup /path/to/new-backup
```

旧命令 `python -m picli.storage_maintenance` 仍可使用。
CLI 会话存入 `.pi-python/sessions.sqlite3`，大文本存入 `.pi-python/artifacts/`。旧 JSON 会话按需迁移，原文件保留。

评估中的 `fixtures/`、`checks/`、`references/` 是测试资产；`downloads/` 和 `results/`
分别是本地下载数据与运行报告，不应与应用源码混淆。Docker 仅用于可选的评估隔离，
日常运行 CLI 无需 Docker。
