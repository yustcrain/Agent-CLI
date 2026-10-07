# 上下文、会话与存储

[返回项目首页](../README.md)

## 自动上下文压缩

每次调用模型前都会估算当前上下文大小。默认在 32,000 token 上限的 75% 时触发，
调用当前模型总结较早对话，并保留最近 4 个完整用户轮次。摘要随会话持久化，恢复会话后继续使用。
被压缩的原始消息追加保存在 `workspace/.pi-python/archives/<会话名>.jsonl`。
会话树中的原始节点不会删除；压缩记录保存摘要覆盖到的节点 ID，切换到较早分支时不会错误使用
其他分支的摘要。

可以在 `.env` 调整：

```bash
CONTEXT_WINDOW='32000'
COMPACT_AT='0.75'
KEEP_TURNS='4'
```

`CONTEXT_WINDOW` 应填写所用模型的实际上下文上限。`COMPACT_AT` 可设为 0.1–0.95；
值越小越早压缩。`/context` 查看估算占用，`/compact` 手动压缩。

## 会话树

每条用户、Assistant 和工具消息都保存为独立节点：

```json
{"id":"...", "parent_id":"...", "message":{"role":"assistant", "content":"..."}}
```

`head_id` 指向当前分支末端。切换到历史节点后发送新消息，会在同一个会话的消息树中形成新分支：

```text
/tree
/checkout <节点ID或唯一前缀>
/fork experiment <可选节点ID>
/history
```

`/checkout` 在当前会话树中移动 head；`/fork` 将目标节点之前的分支复制为一个新会话并切换过去。
旧版线性 session JSON 会在下次保存时自动迁移到 version 3，会话消息和已有摘要继续保留。

## Memory

Memory 使用与 `pi-mono` Mom 相同的双层 Markdown 工作记忆：

- `workspace/MEMORY.md`：所有会话共享的项目事实、约定和用户偏好。
- `workspace/.pi-memory/<会话名>/MEMORY.md`：当前会话的决定和进行中的工作。

程序在每次模型调用前重新完整读取两个文件并放入 system prompt。Agent 使用现有的
`read`、`write`、`edit` 工具维护它们，并被要求修改已有事实、保持内容简洁，避免无限追加。
CLI 对话状态保存在 `.pi-python/sessions.sqlite3`，大文本位于 `.pi-python/artifacts/`；上下文过长时，旧消息仍通过自动压缩生成摘要，原文保存在
`.pi-python/archives/`。Memory 本身使用 Markdown 文件，不使用 embedding、向量数据库或自动 RAG。

```text
/memory
/memory global
/memory session
```


### CLI 会话存储与迁移

CLI 会话元数据、消息树和压缩记录保存到 `.pi-python/sessions.sqlite3`；
超过 16 KiB 的文本复用 `.pi-python/artifacts/` 内容存储。

`/sessions` 和 Tab 补全同时列出数据库会话与旧 `.pi-python/sessions/*.json` 会话。
旧会话在首次 `/resume` 或按名称打开时自动导入 SQLite；源 JSON 保留作备份，
之后读写以数据库为准。`/fork` 和 `/checkout` 保留原有消息树和摘要边界行为。
备份 CLI 会话须同时保存 `sessions.sqlite3` 与 `artifacts/`。
此次迁移仅针对会话存储；CLI Goal 文件和压缩归档文件仍使用原有格式。

### 增量写入与并发保护

CLI 使用 `SessionDatabase` 和 `SQLiteSession` 存储实现。
`sessions` 保存 ID、标题字段、创建/更新时间、`head_id` 和 `revision`；
`message_nodes` 按消息节点保存内容与父节点关系；`compactions` 保存摘要及覆盖边界。
保存时只追加新增消息和压缩记录，与会话位置和版本更新在同一事务中提交。
旧消息视为不可变记录；应用层应通过追加消息或切换分支修改对话。

每次保存都检查读取时的 `revision`。若其他实例已保存或删除会话，当前保存失败，
不会覆盖已提交数据。CLI 遇到该错误后使用 `/resume <会话名>` 重新加载；
冲突实例中尚未提交的消息不会自动合并。该检查不会撤销已经执行的工具副作用。
会话列表按更新时间排序。数据库通过 `session_storage_meta` 记录模式版本，
旧 SQLite 会话迁移失败时回滚模式和数据；旧 JSON 会话仍按名称首次打开时导入。

恢复会话时加载消息树的轻量元数据，消息正文和摘要按需读取。
模型上下文只读取当前分支压缩边界之后的消息与适用摘要；查看完整历史、
`/tree`、切换分支或派生会话时才按需读取相关旧正文。元数据本身仍按整个会话加载。

### 在线备份与内容回收

工具无需模型配置，`--workspace` 指定需要维护的工作区：

```bash
# 在线备份会话数据库及其引用的内容文件，目标目录必须不存在
.venv/bin/python -m picli.storage_maintenance --workspace ./workspace backup /path/to/backups/snapshot-001

# 预览没有被任何数据库引用的内容文件数量和大小
.venv/bin/python -m picli.storage_maintenance --workspace ./workspace gc

# 实际回收孤立内容文件
.venv/bin/python -m picli.storage_maintenance --workspace ./workspace gc --apply
```

维护操作与正常数据库访问使用工作区级跨进程读写锁。备份/回收期间数据库访问暂时等待，
无需退出 CLI；正在进行的模型推理可以继续。使用 SQLite backup API 生成一致快照，
检查被引用文件的 SHA-256，并在全部复制成功后发布备份目录；失败不会发布半成品。
回收扫描工作区 `.pi-python/*.sqlite3` 中所有 JSON 文本引用，包括当前会话库与已有数据库，
先验证所有被引用的内容，再删除无引用的哈希文件。缺失或损坏引用会终止操作。
这些保障要求所有写入使用项目存储接口；请勿同时用外部脚本直接修改数据库或内容目录。

备份目录包含 `.pi-python/` 数据库和内容文件及 `manifest.json`。恢复时将其中的
`.pi-python/` 放到一个新的工作区（可直接把备份目录作为工作区打开）；
需要原项目代码时另行恢复项目文件。不要覆盖正在使用的数据库。
此备份覆盖 SQLite 管理的会话；不包含项目文件、配置/密钥、
`MEMORY.md`、CLI Goal JSON、旧 JSON 迁移备份及压缩归档，这些需要单独备份。

