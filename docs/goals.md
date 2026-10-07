# 持续目标与验收

[返回项目首页](../README.md)

## 长期目标

Goal 层采用验收驱动流程。创建 Goal 时，主模型把目标转换成最多 12 条验收条件；内层 Agent
每次停止后，独立 Verifier 会实际执行文件和命令检查，并确认人工条件是否已获用户批准。
只有全部验收条件通过，Goal 才能进入 `complete`。Agent 的完成声明不会改变 Goal 状态。
验收阶段不调用额外模型。

自动验收支持：

- `command`：运行命令并要求退出码为 0；`workspace-write` 中按策略请求授权，`danger-full-access` 中直接执行。
- `file_exists`：检查工作区文件或目录存在。
- `file_equals`：精确比较完整文件内容，包括空格和结尾换行。
- `file_contains` / `file_not_contains`：检查文件是否包含指定文本。
- `manual`：无法自动判断的结果，必须由用户明确批准。

```text
/goal 实现并验证一个完整的命令行记账工具
/goal status
/goal criteria
/goal approve AC1
/goal resume
/goal pause
/goal cancel
```

`/goal criteria` 会显示验收合同和最近一次检查证据。对于 `manual` 条件，执行
`/goal approve <ID>` 后再运行 `/goal resume`，系统会重新执行全部验收。

CLI 的 Goal 状态保存在 `workspace/.pi-python/goals/<会话名>.json`。按 `Ctrl+C`、验收过程出错，
或一次运行达到默认 12 个循环时会保存为暂停状态；执行 `/goal resume` 可以从进度检查点继续。
每个循环仍受 `--max-turns` 限制，因此单次模型工具循环不会无限运行。

可以在 `.env` 调整一次 Goal 运行允许的循环数：

```bash
GOAL_CYCLES='12'
```

创建 Goal 时仍会额外调用一次当前主模型生成验收合同。`read-only` 模式下合同生成器不允许生成
`command` 检查；若模型仍生成不兼容的合同，会携带校验错误自动重试一次。验收命令遵守当前权限
模式和固定安全策略。

