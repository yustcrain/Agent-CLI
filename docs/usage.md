# 使用与配置

[返回项目首页](../README.md)

## 启动

```bash
cd /path/to/python-cli
source .venv/bin/activate
python -m picli --workspace ./workspace --demo
```

`.venv` 已创建并安装 PocketFlow；`start.command` 会在缺少依赖时自动执行
`pip install -r requirements.txt`。
演示模式使用确定性 Mock Provider，不联网，也不是真实模型回答。输入普通文本会得到固定回显；
输入 `mock:read` 或 `mock:write` 可以测试完整工具循环。

连接支持工具调用的 OpenAI Chat Completions 兼容服务：

```bash
export API_KEY='你的 API Key'
export BASE_URL='https://你的服务地址/v1'
export MODEL='你的模型 ID'
export PROVIDER='openai-compatible'
python -m picli --workspace ./workspace
```

也识别 `OPENAI_API_KEY`；地址留空时默认使用 `https://api.openai.com/v1`。
本地免鉴权服务可省略 Key。直接运行 `python -m picli` 使用 shell 环境变量；
`start.command` 会加载目录中的 `.env`；终端里已经 `export` 的配置优先。
`.env` 是 shell 配置文件，只填写你信任的内容。

```bash
python -m picli --workspace ./workspace --session coding
python -m picli --workspace ./workspace --prompt '列出你能使用的工具'
python -m picli --workspace ./workspace --permission-mode read-only
```

原生 Anthropic Messages API：

```bash
export PROVIDER='anthropic'
export MODEL='claude-sonnet-4-6'
export API_KEY='你的 Anthropic API Key'
python -m picli --workspace ./workspace
```

也识别 `ANTHROPIC_API_KEY`、`ANTHROPIC_AUTH_TOKEN` 和 `ANTHROPIC_BASE_URL`。

## 配置、诊断和 JSON 输出

配置按以下顺序合并，后面的值覆盖前面的值：

1. `~/.config/pi-cli/config.json`
2. `<workspace>/.pi-python/config.json`
3. `--config` 指定的 JSON 文件
4. 环境变量（如 `MODEL`、`API_KEY`）
5. CLI 参数

配置文件使用 snake_case 字段，例如：

```json
{
  "provider": "openai-compatible",
  "base_url": "https://api.deepseek.com",
  "model": "deepseek-v4-flash",
  "permission_mode": "workspace-write",
  "max_turns": 20
}
```

查看脱敏后的配置和运行状态：

```bash
python -m picli --workspace ./workspace config
python -m picli --workspace ./workspace status
python -m picli --workspace ./workspace doctor
python -m picli --workspace ./workspace tools
```

这些命令不要求配置模型。加入 `--output-format json` 后只输出一个 JSON 文档，适合脚本和 CI：

```bash
python -m picli --workspace ./workspace --output-format json doctor
python -m picli --workspace ./workspace --provider mock --output-format json prompt "hello"
```

API Key 和 Auth Token 在 `config` 输出中只显示 `configured` 或 `missing`，不会输出原值。

## 确定性 Mock Provider

Mock Provider 完全离线，同样经过 Agent、工具注册表、权限和会话保存流程，适合稳定测试：

```bash
python -m picli --workspace /tmp/picli-mock --provider mock prompt "hello"
python -m picli --workspace /tmp/picli-mock --provider mock prompt "mock:write result.txt::fixed output"
python -m picli --workspace /tmp/picli-mock --provider mock prompt "mock:read result.txt"
```

相同输入会产生相同的回复或工具调用，不需要 API Key，也不产生模型费用。旧的 `--demo` 现在是
`--provider mock` 的兼容别名。

## 桌面直接启动

双击 `start.command`，或在终端执行：

```bash
cd /path/to/python-cli
./start.command
```

已重建 `.venv` 并安装依赖。默认工作区为 `workspace/`。
`.env` 已创建（Key 和模型为空，权限为仅当前用户读写）。
编辑 `.env` 中的 `BASE_URL`、`MODEL` 和 `API_KEY` 后重新启动即可使用真实模型。
未填写模型时，无参数启动默认使用离线演示。

```bash
./start.command --demo
./start.command --permission-mode workspace-write
```

真实模型需要支持工具调用；API Key 必须由你自行填写。此项目不会读取原仓库的凭据。

macOS 上如果 python.org 安装的 Python 没有初始化自己的 CA 文件，CLI 会自动使用
`/etc/ssl/cert.pem`，并继续执行正常的 HTTPS 证书校验。也可以通过 `SSL_CERT_FILE`
明确指定自定义 CA 文件。

