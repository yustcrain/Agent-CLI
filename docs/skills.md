# 项目技能

[返回项目首页](../README.md)

## Skills

Skill 是按需加载的项目操作手册，可以包含 `SKILL.md`、脚本、参考资料和模板。程序从以下
工作区目录递归发现 `SKILL.md`：

```text
skills/
.agents/skills/
.pi/skills/
```

启动时只有 Skill 的 `name`、`description` 和路径进入 system prompt。模型需要使用某个 Skill
时调用 `read_skill` 读取完整内容，避免所有说明长期占用上下文。也可以手动执行：

```text
/skills
/skill python-testing
/skill python-testing 检查当前项目
```

每个 `SKILL.md` 必须包含：

```markdown
---
name: example-skill
description: 说明这个 Skill 做什么以及什么时候使用。
---

# Instructions
具体工作流程……
```

同名 Skill 按 `skills/`、`.agents/skills/`、`.pi/skills/` 的顺序覆盖，后者优先。
示例位于 `workspace/.pi/skills/python-testing/SKILL.md`。Skill 中的脚本仍受当前工具权限控制；
`read-only` 模式不能运行 shell，其他模式按各自规则处理。

