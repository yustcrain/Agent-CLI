# 评估体系

[返回项目首页](../README.md)

## Agent Eval

`evals/` 提供真实模型端到端评估。每个 Case 在独立临时工作区中运行，最终结果由 Harness
拥有的固定检查评判，不使用 Agent 自己生成的 Goal 验收条件作为最终分数。内置 Case 覆盖文件工具、
Python 修复、Memory 纠正、Skill 按需加载、上下文压缩、长期 Goal、危险命令策略，以及 10 个带隐藏测试和参考实现的多文件仓库任务。

Eval 中的 Goal 以无人值守方式运行，验收合同不允许 `manual` 检查；模型若返回人工验收条件，
合同会被判为无效并要求重新生成。需要验证运行时行为的 Goal case 应设置
`"allow_bash": true` 和 `"permission_mode": "allow"`，让本地命令验收可以在临时隔离工作区中执行。
Goal 合同生成和任务执行复用 Eval 已配置的 Provider 与 API key，不需要单独配置 Goal key。

先验证所有 Case 和 fixture，不调用模型：

```bash
./eval.command --validate
./eval.command --list
```

运行一个 Case：

```bash
./eval.command --case create_file
```

运行全套且每个 Case 重复三次：

```bash
./eval.command --runs 3
```

Eval 也经过 Provider 工厂，可以选择 OpenAI-compatible 或 Anthropic：

```bash
./eval.command --provider openai-compatible --runs 3
./eval.command --provider anthropic --runs 3
```

只有在需要全局评估 bash 行为时才加入 `--allow-bash`；安全 Case 会按自身配置启用。ASK 操作在 Eval
中默认拒绝；Case 可以显式将 `permission_mode` 配置为 `allow`。DENY 操作始终不可放行。结果写入
`evals/results/`：JSONL 保存逐次
运行、Grader 和工具轨迹。每次工具调用会分类为 `success`、`policy_blocked`、`user_denied`、
`capability_unavailable`、`invalid_arguments`、`operation_error`、`timeout`、`interrupted` 或
`unsafe_executed`。Markdown 分别汇总工具执行错误率、工具误用率、策略拦截率、安全逃逸率和错误
恢复率，不会把正常的安全拦截算作工具执行错误。

Eval 使用当前 Provider 对应的地址、凭据和 `MODEL`，因此可以比较 Anthropic 与不同的
OpenAI-compatible 模型。Token 是本地估算值；供应商返回的账单 Token 目前没有写入 Provider。

### 分组评估与公开题集

支持 `--suite regression|capability|repository|bigcodebench|all` 和 `--suite-root`。
默认只运行基础回归，能力任务与公开题集单独统计；工具审计独立于上下文压缩。
`repository` 覆盖跨文件配置、事务原子性、分页、插件注册、数据迁移、依赖图、重试、日志、路径安全和会话过期。
BigCodeBench 支持在线/JSONL 导入、Docker 外部测试和参考答案预检；
SWE-bench 支持指定 commit 推理与官方格式补丁导出。
完整命令、依赖和当前验证范围见 [evals/README.md](../evals/README.md)。

