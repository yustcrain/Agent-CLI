# 权限策略

[返回项目首页](../README.md)

## 权限

CLI 使用和 Claw Code 相同的三种显式权限模式：

| 模式 | 行为 |
|---|---|
| `read-only` | 允许读取；禁止 write、edit 和 bash |
| `workspace-write` | 默认模式；工作区普通文件操作直接执行，ASK 操作需要用户授权 |
| `danger-full-access` | 自动执行 ALLOW 和 ASK；固定 DENY 规则仍然生效 |

权限引擎先匹配固定 DENY，再根据模式处理 ALLOW 和 ASK。模型不能通过用户确认或
`danger-full-access` 覆盖 DENY。当前规则如下：

| 决策 | 操作 |
|---|---|
| ALLOW | workspace 内普通 read/write/edit；严格解析后的 `pwd`、安全 `ls`、`rg --files` |
| ASK | workspace 外普通文件；`.env`、证书等敏感文件；执行代码、Git、网络、安装和其他 shell 命令 |
| DENY | Agent 内部状态、私钥和登录数据库、系统目录写入、裸磁盘、广泛删除、磁盘格式化、关闭系统安全、下载后直接执行 |

模型仍可以传入绝对路径、`~` 路径或包含 `..` 的路径访问整台电脑。ASK 会在执行前显示操作、
风险和完整目标，并要求选择：

```text
y  仅允许这一次
a  本次进程内自动允许完全相同的操作和目标
n  拒绝，并在本次进程内拒绝完全相同的请求
```

没有交互终端时，`workspace-write` 中的 ASK 默认拒绝；ALLOW 仍可执行，DENY 仍被拦截。
启动时选择模式：

```bash
./start.command --permission-mode read-only
./start.command --permission-mode workspace-write
./start.command --permission-mode danger-full-access
```

`--dangerously-skip-permissions` 是 `danger-full-access` 的便捷别名。旧的 `--allow-bash` 参数仍可使用，
但默认的 `workspace-write` 已经提供受权限规则控制的 bash。也可以设置
`PERMISSION_MODE='workspace-write'`。运行中查看、切换模式或清空授权缓存：

```text
/permissions
/permissions read-only
/permissions workspace-write
/permissions danger-full-access
/permissions clear
```

该机制是用户确认层，不是操作系统沙箱。最终访问范围仍由运行 CLI 的系统账户权限决定。

