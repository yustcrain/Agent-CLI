# yu 评估使用说明

这套评估保留现有 runner，不依赖 Inspect AI。分开运行四组任务：

- `regression`：8 个基础回归场景，默认运行。包含真正的新会话记忆纠正、强制验证压缩发生、无关 Skill 不加载。
- `capability`：5 个手工编写的能力场景：Decimal 跨模块修复、标准 CSV 汇总、CLI 错误约定、跨会话约束、配置字段保留。它们是自编题，不宣称来自用户实际失败记录。
- `repository`：10 个多文件仓库场景，覆盖配置合并、库存原子性、分页、插件注册、事件迁移、依赖图、重试、日志、路径安全和会话过期。每题都有隐藏测试与独立参考实现，均明确标记为人工构造场景。
- `bigcodebench`：从官方题集导入的自定义 Agent 评估。结果不能直接与官方排行榜对比。

## 先运行离线检查

在项目根目录执行：

```bash
.venv/bin/python -B -m unittest discover -s tests -v
./eval.command --suite all --validate
./eval.command --suite all --list
.venv/bin/python -m evals.preflight --suite-root evals --suite repository
```

`--validate` 只检查配置和文件存在，不证明模型能力，也不检查 Docker 或第三方依赖。

## 真实模型评估（会调用配置的模型 API）

```bash
./eval.command --suite regression --runs 3
./eval.command --suite capability --runs 3
./eval.command --suite repository --runs 3
./eval.command --case invoice_multifile --runs 3
```

每个 case/repetition 新建临时目录；`new_session_before` 用零基 turn 索引指定清空聊天记录的时机，保留工作区和 MEMORY.md。

报告默认保存到 `evals/results/`。先看分组通过率，再看失败检查与证据。JSONL 保存模型、provider、配置、代码/题目/fixture/外部检查的 SHA-256 和题目来源。

工具事件由执行节点的独立审计回调保存，不再从压缩后的上下文反推。模型次数包含摘要和 Goal 验收条件生成。Token 仍是估算值，不等于供应商账单。

Goal 错误完成率 = 外部检查未通过但 Goal 声称 complete 的次数 / Goal 声称 complete 的次数；无 complete 样本显示 N/A。它不检测普通聊天中“嘴上说完成”。预算检查失败也会令整道题失败。

## 多文件仓库评估

先让 Docker 验证每个初始仓库至少有一个隐藏测试失败，而且参考实现全部通过：

```bash
docker pull python:3.11-slim
.venv/bin/python -m evals.preflight --suite-root evals --suite repository
```

先跑一题确认模型和环境，再跑整组：

```bash
./eval.command --case repo_config_layers --runs 1
./eval.command --suite repository --runs 3
```

`evals/references/` 不会复制进 Agent 工作区，只供 preflight 验证题目有效性。`evals/checks/` 同样位于工作区外，由 harness 在 Agent 结束后执行。

## 比较两个 Agent 版本

保存修改前后的 JSONL 报告，生成总体与逐题差异：

```bash
.venv/bin/python -m evals.compare \
  evals/results/BASELINE.jsonl evals/results/CANDIDATE.jsonl \
  --output evals/results/comparison.md
```

CI 中需要在总体成功率下降时返回非零退出码，可加 `--fail-on-success-drop`。随机模型建议每题至少运行三次；比较时应固定模型、provider、case、轮数、权限和 Docker 镜像。

## 收录真实失败案例

不要把人工场景冒充成真实失败。准备失败时的初始目录、人工修好且通过验收的参考目录，以及放在 Agent 工作区外的 Python 验收脚本，然后执行：

```bash
.venv/bin/python -m evals.scaffold_repository_case \
  --id real_issue_001 \
  --description "修复真实项目中的配置回归" \
  --prompt "用户当时给 Agent 的原始任务" \
  --initial /path/to/broken-repo \
  --reference /path/to/fixed-repo \
  --check /path/to/hidden_check.py

.venv/bin/python -m evals.preflight --suite-root evals --suite repository
```

脚手架会排除 `.git`、`.venv` 和缓存目录，并记录初始/参考目录的 SHA-256。提交前检查 fixture 不含凭据、个人数据和参考答案。

## 导入 BigCodeBench：先 10 道

当前已下载 BigCodeBench `v0.1.1` 的完整 1,140 道数据：

- `evals/downloads/BigCodeBench-v0.1.1.jsonl.gz`
- `evals/downloads/BigCodeBench-v0.1.1.jsonl`
- SHA-256 记录在 `evals/downloads/SHA256SUMS`

其中 10 道经过本机预检的标准库任务已经导入到
`evals/imported/bigcodebench-v0.1.1-stdlib10/`。每道题均满足初始 stub
失败、官方参考实现通过。这些任务最初用项目虚拟环境完成过预检；安装 Docker 后应执行下面的容器预检，确认正式评分环境一致。

网络下载依赖可选安装，不加入 picli 的运行依赖：

```bash
.venv/bin/python -m pip install datasets huggingface_hub
docker pull python:3.11-slim
.venv/bin/python -m evals.import_benchmarks bigcodebench \
  --revision main --split v0.1.4 --limit 10 \
  --output evals/imported/bigcodebench-v1
```

下载时 `main` 会先解析成确切的 Hugging Face commit，并记录到 manifest。默认按 ID 排序，保守筛选只使用标准库的任务；不足 10 道会明确报错，不会悄悄少导入。可通过重复 `--id BigCodeBench/编号` 固定选题。需要第三方依赖时，先准备带依赖的 Docker 镜像，再加 `--allow-third-party --image 镜像名`。严格重现时请使用镜像 digest。

已有官方 JSONL 导出时，无须网络依赖：

```bash
.venv/bin/python -m evals.import_benchmarks bigcodebench \
  --input-jsonl /path/to/bigcodebench.jsonl --revision YOUR_EXPORT_VERSION \
  --limit 10 --output evals/imported/bigcodebench-v1
```

本地导出记录文件 SHA-256，版本标签由提供者负责。导入不会覆盖已有目录。参考答案与外部测试保存在 fixture 之外，不交给 Agent。官方测试代码、题目保留原来源；不要把参考答案加入训练或提示词。

先验环境，再跑 Agent：

```bash
.venv/bin/python -m evals.preflight --suite-root evals/imported/bigcodebench-v1
./eval.command --suite-root evals/imported/bigcodebench-v1 --suite bigcodebench --validate
./eval.command --suite-root evals/imported/bigcodebench-v1 --suite bigcodebench --runs 3
```

针对已经下载并筛选好的题集，直接运行：

```bash
./eval.command \
  --suite-root evals/imported/bigcodebench-v0.1.1-stdlib10 \
  --suite bigcodebench --runs 3
```

预检要求初始 stub 失败、参考实现通过。依赖缺失或环境问题先修环境，不归因于 Agent。外部测试在无网络 Docker 容器执行，有时间、内存与进程限制；Agent 本身仍使用 picli 原有本地权限机制。临时目录并非操作系统沙箱。

## SWE-bench：选 3 个任务，保留官方验收

```bash
.venv/bin/python -m evals.import_benchmarks swebench \
  --revision main --limit 3 --output evals/imported/swebench-v1
```

也支持 `--input-jsonl`。导入只保留 issue、仓库、base commit 与来源，不复制参考补丁或隐藏测试给 Agent。默认是 ID 排序的前 3 道，不保证容易；推荐浏览任务后用 `--id` 固定选择。

准备 task 中 `repo` 对应的本地克隆和 picli 镜像：

```bash
docker build -f evals/docker/Dockerfile -t picli-eval .
```

该镜像只安装 picli。若要让 Agent 在推理阶段运行具体仓库的测试，应额外构建与仓库依赖匹配的镜像。推理容器需要访问模型 API；只挂载本次临时 checkout，不挂载主机 Docker socket。

将 API 配置导出到当前 shell 后运行（这个命令不自动加载 `.env`）：

```bash
.venv/bin/python -m evals.swebench_predict \
  --task evals/imported/swebench-v1/tasks/INSTANCE_ID.json \
  --repo /path/to/local/repository \
  --image picli-eval --provider openai-compatible --model YOUR_MODEL \
  --output evals/results/swe-INSTANCE_ID-run1
```

执行器用 `git archive` 取指定 base commit，不修改本地克隆，不暴露后续提交；保留新文件和修改的补丁，排除 picli 会话产物。容器内允许工具执行，picli 的硬拒绝仍保留。输出 `predictions.jsonl` 和 `run.json`；**导出成功不等于修复通过**。

安装和配置官方 SWE-bench harness 后，使用官方验收：

```bash
swebench eval verified \
  -p evals/results/swe-INSTANCE_ID-run1/predictions.jsonl \
  --run-id picli-UNIQUE_RUN_ID -j 1
```

官方工具的 Docker 镜像与任务仓库准备请按当前官方文档执行；Apple Silicon 可能需要本地构建任务镜像。每次变更补丁必须用新的 run-id，避免复用缓存成绩。代码只负责 picli 推理与预测格式，不重写 SWE-bench 判卷器。

## 加入自己的真实失败任务

复制一个 `evals/cases/` JSON，用 `suite: capability`；把初始状态放入独立 `fixtures/` 子目录；判分脚本放 `checks/`，使用 `python_test` 检查。优先验证：错误版本失败、修复版本通过。不要把只有文件存在的检查当成功能测试。

公开数据参考：
- https://huggingface.co/datasets/bigcode/bigcodebench （Apache-2.0，导入数据遵循上游许可）
- https://github.com/bigcode-project/bigcodebench
- https://github.com/SWE-bench/SWE-bench

本次已从 BigCodeBench 官方 GitHub release 下载 `v0.1.1` 的全部 1,140
道真实题目，并导入、预检其中 10 道标准库任务。尚未运行付费模型或 Docker
benchmark，因此目前只能确认题目与判分器可用，不能报告 picli 的模型通过率。
